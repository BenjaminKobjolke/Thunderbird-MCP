"""Daemon startup and advertisement polling."""

from __future__ import annotations

import asyncio
import logging
import time

from . import daemon_lock, ipc
from .daemon import DEFAULT_IDLE_TIMEOUT, Daemon
from .profile import SOURCE_LABELS, find_profile

log = logging.getLogger("tbmcp.daemon")


async def run_daemon(
    profile_hint: str | None = None,
    *,
    idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
    force: bool = False,
) -> int:
    # One daemon per user, because only one can hold the add-on's connection.
    #
    # The lock covers *startup only*, and is released as soon as the advertisement is
    # published. Holding it for the daemon's whole life looked tidier but could wedge
    # the system: if the holder ever stopped advertising, every later daemon stood
    # down without publishing anything, and the bridge was stuck until that process
    # died. The advertisement is the source of truth; the lock only stops two daemons
    # binding in the same instant, and `_watch_superseded` handles the rest.
    lock: daemon_lock.DaemonLock | None = None
    if not force:
        lock = daemon_lock.DaemonLock.acquire()
        if lock is None:
            # Someone is starting up. Give them a moment to advertise before deciding.
            existing = await _await_advertisement(timeout=6.0)
            if existing is not None:
                log.info(
                    "a daemon is already running (pid %d, control port %d); nothing to do",
                    existing.pid,
                    existing.port,
                )
                return 0
            log.warning("a stale start-up lock is in the way; taking it over")
            lock = daemon_lock.DaemonLock.acquire_forcibly()

    profile = find_profile(profile_hint)
    if profile is None:
        log.error("no Thunderbird profile found; pass --profile with an explicit path")
        if lock is not None:
            lock.release()
        return 2

    log.info("selected profile %s (%s)", profile.path, SOURCE_LABELS[profile.source])

    daemon = Daemon(profile, idle_timeout=idle_timeout, startup_lock=lock)
    try:
        await daemon.run()
    except asyncio.CancelledError:
        daemon._cleanup()
        raise
    finally:
        if lock is not None:
            lock.release()
    return 0


async def _await_advertisement(*, timeout: float) -> ipc.DaemonInfo | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = ipc.DaemonInfo.load()
        if info is not None:
            return info
        await asyncio.sleep(0.25)
    return ipc.DaemonInfo.load()
