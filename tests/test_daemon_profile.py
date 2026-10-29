"""Automatic Thunderbird profile discovery and pairing-file lifecycle coverage.

Every case here comes from something that actually went wrong during development, and
each one presented as "Thunderbird is not connected" with every process looking
healthy — which is why they are pinned down as tests rather than left to judgement:

- a second daemon binding its own sockets while the add-on stayed attached to the
  first, so `serve` and the add-on were talking to different processes;
- a live process holding the start-up lock without ever advertising, which made every
  later daemon stand down and left the bridge down until that process died.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time

import pytest
from support_daemon import _daemon

from tbmcp import ipc
from tbmcp.daemon import Daemon
from tbmcp.errors import NotConnectedError
from tbmcp.profile import BRIDGE_FILE, ThunderbirdProfile

pytestmark = pytest.mark.anyio


async def test_auto_profile_moves_pairing_file(isolated_state, monkeypatch):
    old = isolated_state / "old"
    new = isolated_state / "portable"
    old.mkdir()
    new.mkdir()
    daemon = Daemon(ThunderbirdProfile(old, "old", True, isolated_state, "default"))
    daemon._control_port = 12345
    daemon._write_bridge_file(54321)
    daemon._advertise()
    monkeypatch.setattr("tbmcp.daemon_pairing.running_profile_dirs", lambda: [new])

    await daemon._follow_thunderbird()

    assert daemon.profile.path == new
    assert daemon.status()["profile"]["source"] == "running"
    assert not (old / BRIDGE_FILE).exists()
    assert json.loads((new / BRIDGE_FILE).read_text())["pid"] == os.getpid()
    assert ipc.DaemonInfo.load().profile == str(new)


async def test_explicit_profile_reports_other_without_moving(isolated_state, monkeypatch):
    old = isolated_state / "explicit"
    new = isolated_state / "portable"
    old.mkdir()
    new.mkdir()
    daemon = Daemon(ThunderbirdProfile(old, "explicit", False, isolated_state))
    daemon._write_bridge_file(54321)
    monkeypatch.setattr("tbmcp.daemon_pairing.running_profile_dirs", lambda: [new])
    await daemon._follow_thunderbird()
    assert daemon.status()["profile"]["thunderbirdProfiles"] == [str(new)]
    assert (old / BRIDGE_FILE).exists() and not (new / BRIDGE_FILE).exists()


async def test_failed_profile_move_keeps_old_pairing(isolated_state, monkeypatch):
    old = isolated_state / "old"
    new = isolated_state / "portable"
    old.mkdir()
    new.mkdir()
    daemon = Daemon(ThunderbirdProfile(old, "old", True, isolated_state, "default"))
    daemon._control_port = 12345
    daemon._write_bridge_file(54321)
    daemon._advertise()
    monkeypatch.setattr("tbmcp.daemon_pairing.running_profile_dirs", lambda: [new])
    write = daemon._write_bridge_file

    def fail_new(port):
        if daemon.profile.path == new:
            raise OSError("unwritable")
        write(port)

    monkeypatch.setattr(daemon, "_write_bridge_file", fail_new)
    await daemon._follow_thunderbird()
    assert daemon.profile.path == old
    assert (old / BRIDGE_FILE).exists()
    assert ipc.DaemonInfo.load().profile == str(old)


async def test_wait_follows_profile_that_appears_later(isolated_state, monkeypatch):
    from types import SimpleNamespace

    old = isolated_state / "old"
    new = isolated_state / "portable"
    old.mkdir()
    new.mkdir()
    daemon = Daemon(ThunderbirdProfile(old, "old", True, isolated_state, "default"))
    daemon._write_bridge_file(54321)
    running = []
    monkeypatch.setattr("tbmcp.daemon_pairing.running_profile_dirs", lambda: running[:])
    monkeypatch.setattr("tbmcp.daemon_pairing.FOLLOW_INTERVAL", 0.05)

    async def start_thunderbird():
        await asyncio.sleep(0.08)
        running.append(new)
        while not (new / BRIDGE_FILE).exists():
            await asyncio.sleep(0.01)
        daemon.session = SimpleNamespace(describe=lambda: {})
        daemon._session_ready.set()

    task = asyncio.create_task(start_thunderbird())
    status = await daemon._invoke(
        "daemon.waitForThunderbird", {"timeout": 1}, timeout=2, on_progress=None
    )
    await task
    assert status["connected"] and status["profile"]["path"] == str(new)


async def test_profile_follow_respects_owner_session_and_interval(isolated_state, monkeypatch):
    old = isolated_state / "old"
    new = isolated_state / "portable"
    old.mkdir()
    new.mkdir()
    daemon = Daemon(ThunderbirdProfile(old, "old", True, isolated_state, "default"))
    daemon._write_bridge_file(54321)
    (old / BRIDGE_FILE).write_text(json.dumps({"pid": 999001}))
    lookups = []

    def running():
        lookups.append(1)
        return [new]

    monkeypatch.setattr("tbmcp.daemon_pairing.running_profile_dirs", running)
    await daemon._follow_thunderbird()
    assert (old / BRIDGE_FILE).exists()
    assert len(lookups) == 1
    await daemon._follow_thunderbird()
    assert len(lookups) == 1
    daemon.session = object()
    daemon._followed_at = 0
    await daemon._follow_thunderbird()
    assert len(lookups) == 1


def _held_lookup(monkeypatch, result):
    """A profile lookup that blocks in its worker thread until released."""
    started, release = threading.Event(), threading.Event()

    def running():
        started.set()
        release.wait(5)
        return result

    monkeypatch.setattr("tbmcp.daemon_pairing.running_profile_dirs", running)
    return started, release


async def test_profile_follow_rechecks_session_after_lookup(isolated_state, monkeypatch):
    old = isolated_state / "old"
    new = isolated_state / "portable"
    old.mkdir()
    new.mkdir()
    daemon = Daemon(ThunderbirdProfile(old, "old", True, isolated_state, "default"))
    daemon._control_port = 12345
    daemon._write_bridge_file(54321)
    daemon._advertise()
    started, release = _held_lookup(monkeypatch, [new])

    follow = asyncio.create_task(daemon._follow_thunderbird())
    await asyncio.to_thread(started.wait, 5)
    daemon.session = object()
    release.set()
    await follow

    assert daemon.profile.path == old
    assert (old / BRIDGE_FILE).exists() and not (new / BRIDGE_FILE).exists()
    assert ipc.DaemonInfo.load().profile == str(old)


async def test_wait_deadline_covers_slow_profile_lookup(isolated_state, monkeypatch):
    daemon = _daemon(isolated_state)
    _started, release = _held_lookup(monkeypatch, [])
    begun = time.monotonic()
    try:
        with pytest.raises(NotConnectedError):
            await daemon._invoke(
                "daemon.waitForThunderbird", {"timeout": 0.1}, timeout=2, on_progress=None
            )
        assert time.monotonic() - begun < 1
    finally:
        release.set()


async def test_wait_returns_when_session_attaches_during_lookup(isolated_state, monkeypatch):
    from types import SimpleNamespace

    old = isolated_state / "old"
    new = isolated_state / "portable"
    old.mkdir()
    new.mkdir()
    daemon = Daemon(ThunderbirdProfile(old, "old", True, isolated_state, "default"))
    daemon._write_bridge_file(54321)
    started, release = _held_lookup(monkeypatch, [new])

    async def attach():
        await asyncio.to_thread(started.wait, 5)
        daemon.session = SimpleNamespace(describe=lambda: {})
        daemon._session_ready.set()

    task = asyncio.create_task(attach())
    begun = time.monotonic()
    try:
        status = await daemon._invoke(
            "daemon.waitForThunderbird", {"timeout": 3}, timeout=4, on_progress=None
        )
        assert time.monotonic() - begun < 1
    finally:
        release.set()
    await task
    await asyncio.sleep(0.05)  # the released lookup must not publish a move
    assert status["connected"] and status["profile"]["path"] == str(old)
    assert (old / BRIDGE_FILE).exists() and not (new / BRIDGE_FILE).exists()
