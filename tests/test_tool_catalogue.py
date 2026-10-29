"""Characterize the tool contracts exposed to MCP clients."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from tbmcp.config import ALL_TOOLSETS, Settings
from tbmcp.server import build_server

pytestmark = pytest.mark.anyio

FIXTURE = Path(__file__).parent / "fixtures" / "tool_catalogue.json"
TOOL_FIELDS = ("name", "title", "description", "inputSchema", "annotations")


def _catalogue_row(tool: Any) -> dict[str, Any]:
    dumped = tool.model_dump(mode="json", by_alias=True, exclude_none=False)
    return {field: dumped[field] for field in TOOL_FIELDS}


async def test_tool_catalogue_matches_client_contract_fixture() -> None:
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    actual = {}

    for name in ALL_TOOLSETS:
        server = build_server(Settings().merged_with(toolsets=(name,)))
        actual[name] = [_catalogue_row(tool) for tool in await server.list_tools()]

    assert actual == expected


async def test_settings_describe_ordered_areas_and_preference_contract() -> None:
    server = build_server(Settings().merged_with(toolsets=("settings",)))
    async with Client(server) as client:
        result = await client.call_tool("settings_describe", {})

    assert not result.is_error
    payload = json.loads(result.content[0].text)
    assert payload["areas"] == [
        "Message list and layout",
        "Threading and sort defaults",
        "Reading",
        "Composing",
        "Quoting and replies",
        "Spelling",
        "New-mail notification",
        "Return receipts",
        "Calendar",
        "Updates",
        "Startup",
    ]
    assert payload["settingsCount"] == 43
    assert payload["items"][0]["settings"][0] == {
        "pref": "mail.pane_config.dynamic",
        "type": "int",
        "what": "Where the message pane sits relative to the message list.",
        "values": "0 = classic (list on top, message below), 1 = wide (message below, full width), 2 = vertical (message to the right)",
        "restartNeeded": False,
        "note": "Existing windows may need reopening; new ones pick it up at once.",
        "writable": True,
    }
