"""Shared message data and MCP setup for mail tool tests."""

from __future__ import annotations

from tbmcp.config import Settings
from tbmcp.server import build_server

MESSAGE = {
    "id": 42,
    "subject": "Invoice 2026-07",
    "author": "Supplier <billing@example.com>",
    "recipients": ["me@example.com"],
    "date": "2026-07-01T09:30:00Z",
    "read": False,
    "flagged": False,
    "tags": ["$label1"],
    "size": 4096,
    "folderId": "account1://INBOX",
}


def _server(bridge, **overrides):
    # build_server installs its own Bridge unless one is handed in, which would
    # replace the recorder the fixture just set up.
    return build_server(Settings().merged_with(toolsets=("mail",), **overrides), bridge=bridge)


def _text(result) -> str:
    return " ".join(getattr(block, "text", "") for block in result.content)
