from __future__ import annotations

import pytest
from mcp import Client
from support_tools_mail import _server, _text

from tbmcp.policy import FolderRule

pytestmark = pytest.mark.anyio


async def test_move_reports_the_source_folders(fake_bridge) -> None:
    """Without this a move is not undoable: the ids change, and the old location is
    gone from the caller's point of view."""
    bridge = fake_bridge({"messages.move": {"moved": 2, "sourceFolderIds": ["account1://INBOX"]}})
    async with Client(_server(bridge)) as client:
        result = await client.call_tool(
            "mail_move",
            {
                "message_ids": [1, 2],
                "destination_folder_id": "account1://Archive",
                "confirm": True,
            },
        )
    assert not result.is_error, _text(result)
    payload = result.structured_content
    assert payload["previous"]["folderIds"] == ["account1://INBOX"]
    assert payload["current"]["folderId"] == "account1://Archive"
    assert "re-query" in payload["note"]
    assert bridge.params_for("messages.move")["destinationFolderId"] == "account1://Archive"


async def test_move_reports_each_source_and_landing(fake_bridge) -> None:
    previous = [{"id": 1, "headerMessageId": "<one@example.com>", "folderId": "inbox"}]
    landed = [{"id": 10, "headerMessageId": "<one@example.com>", "folderId": "archive"}]
    bridge = fake_bridge(
        {
            "messages.move": {
                "moved": 1,
                "sourceFolderIds": ["inbox"],
                "previous": previous,
                "landed": landed,
            }
        }
    )
    async with Client(_server(bridge)) as client:
        result = await client.call_tool(
            "mail_move", {"message_ids": [1], "destination_folder_id": "archive", "confirm": True}
        )
    assert not result.is_error, _text(result)
    payload = result.structured_content
    assert payload["previous"] == {"messageIds": [1], "folderIds": ["inbox"], "messages": previous}
    assert payload["current"] == {"folderId": "archive", "messages": landed}
    assert payload["moved"] == 1


async def test_copy_reports_source_and_copies(fake_bridge) -> None:
    previous = [{"id": 1, "headerMessageId": "<one@example.com>", "folderId": "inbox"}]
    landed = [{"id": 10, "headerMessageId": "<one@example.com>", "folderId": "archive"}]
    bridge = fake_bridge({"messages.copy": {"copied": 1, "previous": previous, "landed": landed}})
    async with Client(_server(bridge)) as client:
        result = await client.call_tool(
            "mail_copy", {"message_ids": [1], "destination_folder_id": "archive", "confirm": True}
        )
    assert not result.is_error, _text(result)
    payload = result.structured_content
    assert payload["previous"] == {"messages": previous}
    assert payload["current"] == {"folderId": "archive", "messages": landed}
    assert payload["copied"] == 1
    assert payload["destinationFolderId"] == "archive"


async def test_archive_reports_source_and_landing(fake_bridge) -> None:
    previous = [{"id": 1, "headerMessageId": "<one@example.com>", "folderId": "inbox"}]
    landed = [{"id": 10, "headerMessageId": "<one@example.com>", "folderId": "archive"}]
    bridge = fake_bridge(
        {"messages.archive": {"archived": 1, "previous": previous, "landed": landed}}
    )
    async with Client(_server(bridge)) as client:
        result = await client.call_tool("mail_archive", {"message_ids": [1], "confirm": True})
    assert not result.is_error, _text(result)
    payload = result.structured_content
    assert payload["previous"] == {"messages": previous}
    assert payload["current"] == {"messages": landed}
    assert payload["archived"] == 1


async def test_mail_writes_accept_older_handler_replies(fake_bridge) -> None:
    bridge = fake_bridge(
        {
            "messages.mark": {"updated": 1},
            "messages.move": {"moved": 1, "sourceFolderIds": ["inbox"]},
            "messages.copy": {"copied": 1},
            "messages.archive": {"archived": 1},
        }
    )
    async with Client(_server(bridge)) as client:
        mark = await client.call_tool("mail_mark", {"message_ids": [1], "read": True})
        move = await client.call_tool(
            "mail_move", {"message_ids": [1], "destination_folder_id": "archive", "confirm": True}
        )
        copy = await client.call_tool(
            "mail_copy", {"message_ids": [1], "destination_folder_id": "archive", "confirm": True}
        )
        archive = await client.call_tool("mail_archive", {"message_ids": [1], "confirm": True})
    for result in (mark, move, copy, archive):
        assert not result.is_error, _text(result)
        assert result.structured_content["previous"]["messages"] == []
    for result in (move, copy, archive):
        assert result.structured_content["current"]["messages"] == []
    assert move.structured_content["previous"]["folderIds"] == ["inbox"]


@pytest.mark.parametrize(
    ("action", "destination", "sources", "allowed"),
    [
        ("move_in", "/@BKToDo/new", None, True),
        ("move_in", "/Inbox", None, False),
        ("move_out", "/Inbox", ["/@BKToDo/a", "/@BKToDo/b"], True),
        ("move_out", "/Inbox", ["/@BKToDo/a", "/Outside"], False),
    ],
)
async def test_move_folder_rules(fake_bridge, action, destination, sources, allowed) -> None:
    responses = {
        "folders.get": lambda params: {
            "folder": {
                "path": destination
                if params["folderId"] == "dest"
                else {"src1": sources[0], "src2": sources[1]}[params["folderId"]],
                "accountId": "account1",
            }
        },
        "messages.readMany": {
            "messages": [
                {"header": {"id": 1, "folderId": "src1"}},
                {"header": {"id": 2, "folderId": "src2"}},
            ],
            "failures": [],
        },
    }
    bridge = fake_bridge(responses)
    rules = (FolderRule("/@BKToDo", frozenset({action})),)
    async with Client(_server(bridge, folder_rules=rules)) as client:
        result = await client.call_tool(
            "mail_move", {"message_ids": [1, 2], "destination_folder_id": "dest"}
        )
    assert (not result.is_error) is allowed, _text(result)
    assert ("messages.move" in bridge.methods()) is allowed
    if action == "move_in":
        assert "messages.readMany" not in bridge.methods()


@pytest.mark.parametrize(
    "read_result",
    [
        {"messages": [{"header": {"id": 1, "folderId": "src1"}}], "failures": []},
        {
            "messages": [
                {"header": {"id": 1, "folderId": "src1"}},
                {"header": {"id": 2, "folderId": "src2"}},
            ],
            "failures": [2],
        },
        {
            "messages": [{"header": {"id": 1, "folderId": "src1"}}, {"header": {"id": 2}}],
            "failures": [],
        },
        {
            "messages": [
                {"header": {"id": 1, "folderId": "src1"}},
                {"header": {"id": 3, "folderId": "src2"}},
            ],
            "failures": [],
        },
    ],
)
async def test_move_out_requires_complete_source_lookup(fake_bridge, read_result) -> None:
    bridge = fake_bridge(
        {
            "folders.get": {"folder": {"path": "/Outside", "accountId": "account1"}},
            "messages.readMany": read_result,
        }
    )
    rules = (FolderRule("/@BKToDo", frozenset({"move_out"})),)
    async with Client(_server(bridge, folder_rules=rules)) as client:
        result = await client.call_tool(
            "mail_move", {"message_ids": [1, 2], "destination_folder_id": "dest"}
        )
    assert result.is_error
    assert "messages.move" not in bridge.methods()


async def test_move_without_rules_needs_confirmation_without_policy_reads(fake_bridge) -> None:
    bridge = fake_bridge()
    async with Client(_server(bridge)) as client:
        result = await client.call_tool(
            "mail_move", {"message_ids": [1], "destination_folder_id": "dest"}
        )
    assert result.is_error
    assert "NEEDS_CONFIRMATION" in _text(result)
    assert bridge.calls == []
