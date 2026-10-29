"""Pair the daemon with the Thunderbird profile that owns the add-on."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from . import ipc
from .profile import BRIDGE_FILE, ThunderbirdProfile, running_profile_dirs, same_path

if TYPE_CHECKING:
    from .daemon import Daemon

log = logging.getLogger("tbmcp.daemon")
FOLLOW_INTERVAL = 5.0


def bridge_file_pid(path: Path) -> int | None:
    """The pid named by a pairing file, or None if it names nothing we can read."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return int(payload["pid"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def write_bridge_file(daemon: Daemon, port: int) -> None:
    payload = {
        "version": ipc.PROTOCOL_VERSION,
        "port": port,
        "token": daemon.addon_token,
        "pid": os.getpid(),
    }
    path = daemon.profile.path / BRIDGE_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    ipc._restrict_permissions(path)
    daemon._addon_port = port
    log.info("pairing file written to %s (port %d)", path, port)


def remove_bridge_file(daemon: Daemon, path: Path | None = None) -> None:
    path = path or daemon.profile.path / BRIDGE_FILE
    if bridge_file_pid(path) == os.getpid():
        with contextlib.suppress(OSError):
            path.unlink()


def advertise(daemon: Daemon) -> None:
    if daemon._control_port is None:
        return
    ipc.DaemonInfo(
        version=ipc.PROTOCOL_VERSION,
        port=daemon._control_port,
        token=daemon.control_token,
        pid=os.getpid(),
        profile=str(daemon.profile.path),
    ).write()


async def follow_thunderbird(daemon: Daemon) -> None:
    """Move an auto-picked pairing file to the running Thunderbird profile."""
    if daemon.session is not None or time.monotonic() - daemon._followed_at < FOLLOW_INTERVAL:
        return
    daemon._followed_at = time.monotonic()
    daemon.thunderbird_profiles = await asyncio.to_thread(running_profile_dirs)
    # An add-on can attach while the lookup runs; its profile then stays put.
    if (
        daemon.session is not None
        or daemon.profile.source == "explicit"
        or not daemon.thunderbird_profiles
        or any(same_path(daemon.profile.path, p) for p in daemon.thunderbird_profiles)
        or daemon._addon_port is None
    ):
        return
    old = daemon.profile
    path = daemon.thunderbird_profiles[0]
    log.warning("moving pairing file from %s to running profile %s", old.path, path)
    daemon.profile = ThunderbirdProfile(path, path.name, False, path.parent.parent, "running")
    try:
        daemon._write_bridge_file(daemon._addon_port)
        daemon._advertise()
    except OSError:
        daemon._remove_bridge_file()
        daemon.profile = old
        log.exception("could not publish pairing file for %s", path)
        return
    daemon._remove_bridge_file(old.bridge_file)


async def follow_then_pause(daemon: Daemon) -> None:
    """One follow attempt and the pause before the next, for `waitForThunderbird`."""
    await daemon._follow_thunderbird()
    await asyncio.sleep(FOLLOW_INTERVAL)
