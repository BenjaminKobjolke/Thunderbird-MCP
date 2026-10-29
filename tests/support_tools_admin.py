"""Shared fixtures and sample status for admin-tool tests."""

from __future__ import annotations

import time

from tbmcp.config import Settings
from tbmcp.server import build_server

FAILING_HANDSHAKE = {
    "attempts": 3,
    "lastAttemptAt": time.time() - 5,
    "lastOutcome": "no-upgrade",
    "lastCloseCode": None,
    "lastUserAgent": "Thunderbird/155.0",
    "recentFailures": 3,
    "recentOutcomes": {"no-upgrade": 3},
    "windowSeconds": 60.0,
    "recent": [],
}

QUIET_HANDSHAKE = {
    "attempts": 0,
    "lastAttemptAt": None,
    "lastOutcome": None,
    "lastCloseCode": None,
    "lastUserAgent": None,
    "recentFailures": 0,
    "recentOutcomes": {},
    "windowSeconds": 60.0,
    "recent": [],
}


def _server(bridge):
    return build_server(Settings().merged_with(toolsets=("admin",)), bridge=bridge)


def _daemon_status(handshake: dict | None) -> dict:
    return {
        "daemon": {"pid": 1234, "uptimeSeconds": 60.0, "clients": 1},
        "profile": {"path": "/profile", "name": "default"},
        "thunderbird": None,
        "connected": False,
        "handshake": handshake,
        "logFile": "/state/tbmcp/daemon.log",
    }
