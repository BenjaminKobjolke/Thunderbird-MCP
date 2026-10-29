"""Process launch and shared-instance helpers for the daemon bridge."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Any

from . import ipc, thunderbird_process
from .errors import NotConnectedError, TransportError

if TYPE_CHECKING:
    from .bridge import Bridge

log = logging.getLogger("tbmcp.bridge")
SPAWN_WAIT_SECONDS = 12.0
ATTACH_WAIT_SECONDS = 20.0

_DAEMON_LOCAL = frozenset(
    {"daemon.status", "daemon.events", "daemon.waitForThunderbird", "daemon.shutdown"}
)


def _daemon_local(method: str) -> bool:
    return method in _DAEMON_LOCAL


async def spawn_daemon(profile_hint: str | None) -> None:
    """Start a detached daemon and wait for it to advertise itself."""
    argv = [sys.executable, "-m", "tbmcp", "daemon"]
    if profile_hint:
        argv += ["--profile", profile_hint]
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_NO_WINDOW = 0x08000000
        # DETACHED_PROCESS makes Windows ignore CREATE_NO_WINDOW. A venv
        # launcher can then give its Python child a visible console.
        kwargs["creationflags"] = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    log.info("starting the tbmcp daemon")
    try:
        subprocess.Popen(argv, **kwargs)
    except OSError as exc:
        raise TransportError(
            f"could not start the tbmcp daemon: {exc}", code="SPAWN_FAILED"
        ) from exc

    deadline = time.monotonic() + SPAWN_WAIT_SECONDS
    while time.monotonic() < deadline:
        if ipc.DaemonInfo.load() is not None:
            return
        await asyncio.sleep(0.15)
    raise TransportError(
        "the tbmcp daemon did not come up in time; run `tbmcp daemon` in a terminal to see why",
        code="SPAWN_TIMEOUT",
    )


async def wait_for_attachment(bridge: Bridge) -> None:
    """Block until Thunderbird is attached, at most once per connection.

    Cleared again whenever a call comes back "not connected", so a Thunderbird
    that restarts mid-session is waited for afresh rather than failing every call.
    """
    if bridge._attached:
        return
    async with bridge._attach_lock:
        if bridge._attached:
            return
        if not thunderbird_process.is_running():
            log.info("Thunderbird is not running; starting the configured executable")
            thunderbird_process.start_thunderbird()
        try:
            await bridge.call(
                "daemon.waitForThunderbird",
                {"timeout": ATTACH_WAIT_SECONDS},
                timeout=ATTACH_WAIT_SECONDS + 5.0,
                _retry=False,
            )
        except NotConnectedError:
            # Do not raise from here. The caller's own request will fail with the
            # same message, and doing it there keeps one wait from turning into two
            # for a single tool call.
            log.info("Thunderbird has not attached after %.0fs", ATTACH_WAIT_SECONDS)
            return
        bridge._attached = True


_shared: Bridge | None = None


def shared_bridge() -> Bridge:
    """Process-wide bridge, so every tool reuses one daemon connection."""
    from .bridge import Bridge

    global _shared
    if _shared is None:
        _shared = Bridge(
            profile_hint=os.environ.get("TBMCP_PROFILE"),
            autostart=os.environ.get("TBMCP_NO_AUTOSTART") not in ("1", "true", "yes"),
        )
    return _shared


def set_shared_bridge(bridge: Bridge | None) -> None:
    """Test seam."""
    global _shared
    _shared = bridge
