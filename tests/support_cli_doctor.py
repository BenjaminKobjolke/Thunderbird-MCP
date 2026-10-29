"""Shared setup and sample reports for CLI doctor tests."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

from tbmcp.cli import build_parser
from tbmcp.cli_doctor import cmd_doctor
from tbmcp.profile import ThunderbirdProfile

# ------------------------------------------------- what doctor now has to explain
#
# The chain was healthy-looking and broken at the same time: Thunderbird was
# running, the add-on was dialling in every ~25 s and failing the handshake, and
# the add-on it had installed (1.2.0) was older than the one in this package. All
# `doctor` would say was "the add-on has not dialled in yet; try again in a moment".

_ADDON_STATUS = {
    "writtenAt": "2026-09-08T15:00:00.000Z",
    "addonVersion": "1.2.0",
    "capabilities": {"privilegedModules": {"loaded": ["admin", "prefs"]}},
    "methodCount": 70,
}

_TRANSPORT = {
    "state": "connecting",
    "connecting": True,
    "attempt": 7,
    "inFlight": 0,
    "consecutiveFailures": 7,
    "lastCloseCode": 1006,
    "lastCloseAt": "2026-09-08T15:04:00.000Z",
    "lastWelcomeAt": None,
    "port": 50284,
}

_FAILING_HANDSHAKE = {
    "attempts": 3,
    "lastAttemptAt": time.time() - 12,
    "lastOutcome": "no-hello-timeout",
    "lastCloseCode": 4002,
    "lastUserAgent": "Thunderbird/155.0",
    "recentFailures": 3,
    "recentOutcomes": {"no-hello-timeout": 3},
    "windowSeconds": 60.0,
    "recent": [],
}


def _stub_common(
    monkeypatch,
    *,
    bridge_status: dict,
    addon_summary: dict | None = None,
    profile=None,
) -> list:
    """Fake every dependency `cmd_doctor` reaches for besides `Bridge`/`build_server`,
    which each test fakes itself. Returns the bridges that were constructed.

    `addon_summary` stands in for `addon_install.summary()` (the version in *this*
    package's source), and `profile` for a real profile directory — the version the
    add-on actually installed is read out of the status file it leaves there.
    """
    created_bridges: list = []

    class FakeBridge:
        def __init__(self, *, profile_hint=None, autostart=True, default_timeout=30.0):
            self.profile_hint = profile_hint
            self.autostart = autostart
            self.default_timeout = default_timeout
            self.closed = False
            created_bridges.append(self)

        async def require_thunderbird(self, *, wait=0.0):
            return dict(bridge_status)

        async def status(self):
            return dict(bridge_status)

        async def close(self):
            self.closed = True

    monkeypatch.setattr("tbmcp.bridge.Bridge", FakeBridge)
    monkeypatch.setattr("tbmcp.profile.list_profiles", lambda: [profile] if profile else [])
    monkeypatch.setattr("tbmcp.profile.find_profile", lambda *_a, **_k: profile)
    monkeypatch.setattr("tbmcp.addon_install.summary", lambda: dict(addon_summary or {}))

    from tbmcp.ipc import DaemonInfo

    monkeypatch.setattr(DaemonInfo, "load", classmethod(lambda cls: None))

    return created_bridges


def _profile(tmp_path, status: dict | None = None):
    """A real `ThunderbirdProfile` on disk, optionally with the add-on's own report."""
    profile = ThunderbirdProfile(path=tmp_path, name="default", is_default=True, root=tmp_path)
    if status is not None:
        profile.addon_status_file.write_text(json.dumps(status), encoding="utf-8")
    return profile


def _stub_server(monkeypatch, *, connected: bool) -> None:
    class FakeMCP:
        async def call_tool(self, name, args):
            return SimpleNamespace(structured_content={"connected": connected})

    monkeypatch.setattr("tbmcp.server.build_server", lambda settings, *, bridge=None: FakeMCP())


def _run_doctor(monkeypatch, argv: list[str], **stubs) -> int:
    _stub_common(monkeypatch, **stubs)
    _stub_server(monkeypatch, connected=bool(stubs["bridge_status"].get("connected")))
    return cmd_doctor(build_parser().parse_args(["doctor", *argv, "--wait", "0"]))
