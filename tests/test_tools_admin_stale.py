"""Checks for reporting stale server source code."""

from __future__ import annotations

import pytest
from mcp import Client
from support_tools_admin import _daemon_status, _server

from tbmcp import thunderbird_process
from tbmcp.tools.admin import NOT_RUNNING_HINT

pytestmark = pytest.mark.anyio


async def test_tb_status_flags_server_started_before_source_changed(fake_bridge, monkeypatch):
    from tbmcp import ipc
    from tbmcp.tools import admin

    monkeypatch.setattr(admin, "SERVER_STARTED_AT", 10.0)
    monkeypatch.setattr(ipc, "newest_source_mtime", lambda: 20.0)
    status = _daemon_status(None)
    status["connected"] = True
    bridge = fake_bridge({"daemon.status": status})
    async with Client(_server(bridge)) as client:
        payload = (await client.call_tool("tb_status", {})).structured_content
    assert payload["serverCodeStale"] is True
    assert "reconnect the server" in payload["hint"]


async def test_tb_status_does_not_flag_current_server(fake_bridge, monkeypatch):
    from tbmcp import ipc
    from tbmcp.tools import admin

    monkeypatch.setattr(admin, "SERVER_STARTED_AT", 20.0)
    monkeypatch.setattr(ipc, "newest_source_mtime", lambda: 10.0)
    bridge = fake_bridge({"daemon.status": _daemon_status(None)})
    async with Client(_server(bridge)) as client:
        payload = (await client.call_tool("tb_status", {})).structured_content
    assert "serverCodeStale" not in payload


async def test_tb_status_preserves_existing_hint_when_server_is_stale(fake_bridge, monkeypatch):
    from tbmcp import ipc
    from tbmcp.tools import admin

    monkeypatch.setattr(admin, "SERVER_STARTED_AT", 10.0)
    monkeypatch.setattr(ipc, "newest_source_mtime", lambda: 20.0)
    monkeypatch.setattr(thunderbird_process, "is_running", lambda: False)
    bridge = fake_bridge({"daemon.status": _daemon_status(None)})
    async with Client(_server(bridge)) as client:
        payload = (await client.call_tool("tb_status", {})).structured_content
    assert payload["serverCodeStale"] is True
    assert payload["hint"] == NOT_RUNNING_HINT
