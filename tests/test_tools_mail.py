"""End-to-end through a real in-memory MCP client.

This is the layer that matters: it exercises schema validation, the consent
resolver, and the error path a model would actually see - none of which are covered
by calling the tool functions directly.
"""

from __future__ import annotations

import pytest
from mcp import Client
from support_tools_mail import MESSAGE, _server, _text

pytestmark = pytest.mark.anyio


async def test_search_requires_at_least_one_filter(fake_bridge) -> None:
    """A bare search over every folder would be slow and useless, so it is refused
    with a message naming the parameter to use instead."""
    bridge = fake_bridge()
    async with Client(_server(bridge)) as client:
        result = await client.call_tool("mail_search", {})
    assert result.is_error
    assert "full_text" in _text(result)
    assert bridge.calls == []


async def test_list_all_inboxes_and_include_folder_path(fake_bridge) -> None:
    bridge = fake_bridge(
        {
            "messages.list": {
                "messages": [{**MESSAGE, "folderPath": "/Inbox"}],
                "folderIds": ["account1://INBOX"],
            }
        }
    )
    async with Client(_server(bridge)) as client:
        result = await client.call_tool("mail_list", {"special_use": "inbox", "limit": 5})
    assert not result.is_error, _text(result)
    params = bridge.params_for("messages.list")
    assert params["specialUse"] == "inbox"
    assert "folderId" not in params
    assert result.structured_content["items"][0]["folderPath"] == "/Inbox"
    assert result.structured_content["folderIds"] == ["account1://INBOX"]


async def test_list_requires_exactly_one_folder_scope(fake_bridge) -> None:
    bridge = fake_bridge()
    async with Client(_server(bridge)) as client:
        missing = await client.call_tool("mail_list", {})
        both = await client.call_tool(
            "mail_list", {"folder_id": "account1://INBOX", "special_use": "inbox"}
        )
    assert missing.is_error and both.is_error
    assert bridge.calls == []


async def test_search_maps_arguments_onto_the_query(fake_bridge) -> None:
    bridge = fake_bridge({"messages.query": {"messages": [MESSAGE], "cursor": "list-1"}})
    async with Client(_server(bridge)) as client:
        result = await client.call_tool(
            "mail_search",
            {"full_text": "invoice", "unread": True, "from_date": "2026-07-01", "limit": 5},
        )
    assert not result.is_error, _text(result)
    params = bridge.params_for("messages.query")
    assert params["query"]["fullText"] == "invoice"
    assert params["query"]["unread"] is True
    assert params["query"]["fromDate"].startswith("2026-07-01")
    assert params["limit"] == 5

    payload = result.structured_content
    assert payload["count"] == 1
    assert payload["items"][0]["subject"] == "Invoice 2026-07"
    assert payload["nextCursor"] == "list-1"


async def test_search_rejects_a_malformed_date(fake_bridge) -> None:
    bridge = fake_bridge()
    async with Client(_server(bridge)) as client:
        result = await client.call_tool("mail_search", {"subject": "x", "from_date": "last week"})
    assert result.is_error
    assert "ISO-8601" in _text(result)


async def test_search_echoes_the_scope_the_add_on_searched(fake_bridge) -> None:
    """The answer says what it covered, so nobody has to enumerate folders to find out."""
    scope = {"folderIds": ["account1://INBOX"], "accountIds": None, "includeSubFolders": True}
    bridge = fake_bridge({"messages.query": {"messages": [MESSAGE], "scope": scope}})
    async with Client(_server(bridge)) as client:
        result = await client.call_tool("mail_search", {"subject": "Invoice"})
    assert not result.is_error, _text(result)
    assert result.structured_content["scope"] == scope


async def test_search_passes_on_the_index_note(fake_bridge) -> None:
    bridge = fake_bridge({"messages.query": {"messages": [], "indexNote": "indexing is off"}})
    async with Client(_server(bridge)) as client:
        result = await client.call_tool("mail_search", {"full_text": "invoice"})
    assert not result.is_error, _text(result)
    assert result.structured_content["indexNote"] == "indexing is off"


async def test_search_reports_no_field_the_add_on_never_filled(fake_bridge) -> None:
    """`searchedFolders` was such a field: always null, and a null reads to a model
    as a fact about the mailbox rather than as a field nobody wrote."""
    bridge = fake_bridge({"messages.query": {"messages": [MESSAGE]}})
    async with Client(_server(bridge)) as client:
        result = await client.call_tool("mail_search", {"subject": "Invoice"})
    assert not result.is_error, _text(result)

    payload = result.structured_content
    assert "searchedFolders" not in payload
    assert "scope" not in payload
    assert "indexNote" not in payload
    assert [key for key, value in payload.items() if value is None] == []


async def test_delete_without_confirmation_is_refused(fake_bridge) -> None:
    bridge = fake_bridge()
    async with Client(_server(bridge)) as client:
        result = await client.call_tool("mail_delete", {"message_ids": [42]})
    assert result.is_error
    assert "confirm=true" in _text(result)
    assert "messages.delete" not in bridge.methods(), "it deleted anyway"


async def test_delete_with_confirmation_goes_through(fake_bridge) -> None:
    bridge = fake_bridge({"messages.delete": {"deleted": 1, "permanent": False}})
    async with Client(_server(bridge)) as client:
        result = await client.call_tool("mail_delete", {"message_ids": [42], "confirm": True})
    assert not result.is_error, _text(result)
    assert bridge.params_for("messages.delete") == {"messageIds": [42], "permanent": False}
    payload = result.structured_content
    assert payload["changed"] is True
    assert payload["recoverable"] is True
    assert payload["previous"] == {"messageIds": [42]}


async def test_dry_run_reports_the_plan_without_calling_thunderbird(fake_bridge) -> None:
    bridge = fake_bridge()
    async with Client(_server(bridge)) as client:
        result = await client.call_tool(
            "mail_delete", {"message_ids": [1, 2], "dry_run_only": True}
        )
    assert not result.is_error, _text(result)
    payload = result.structured_content
    assert payload["dryRun"] is True and payload["changed"] is False
    assert bridge.calls == []


async def test_permanent_delete_is_reported_as_unrecoverable(fake_bridge) -> None:
    bridge = fake_bridge({"messages.delete": {"deleted": 1, "permanent": True}})
    async with Client(_server(bridge)) as client:
        result = await client.call_tool(
            "mail_delete", {"message_ids": [42], "permanent": True, "confirm": True}
        )
    assert not result.is_error, _text(result)
    assert result.structured_content["recoverable"] is False


async def test_mark_needs_something_to_change(fake_bridge) -> None:
    bridge = fake_bridge()
    async with Client(_server(bridge)) as client:
        result = await client.call_tool("mail_mark", {"message_ids": [1]})
    assert result.is_error
    assert "add_tags" in _text(result)


async def test_mark_is_not_gated(fake_bridge) -> None:
    """Flags are cheap and reversible; a confirmation prompt for them would train the
    user to click through the prompts that do matter."""
    bridge = fake_bridge({"messages.mark": {"updated": 1}})
    async with Client(_server(bridge)) as client:
        result = await client.call_tool("mail_mark", {"message_ids": [1], "read": True})
    assert not result.is_error, _text(result)
    assert bridge.params_for("messages.mark")["read"] is True


async def test_mark_reports_previous_state_and_requested_changes(fake_bridge) -> None:
    previous = [
        {
            "id": 1,
            "headerMessageId": "<one@example.com>",
            "folderId": "inbox",
            "read": False,
            "flagged": False,
            "junk": False,
            "tags": ["old"],
        }
    ]
    bridge = fake_bridge({"messages.mark": {"updated": 1, "failures": [2], "previous": previous}})
    async with Client(_server(bridge)) as client:
        result = await client.call_tool(
            "mail_mark",
            {"message_ids": [1, 2], "read": True, "add_tags": ["new"], "remove_tags": ["old"]},
        )
    assert not result.is_error, _text(result)
    payload = result.structured_content
    assert payload["previous"] == {"messages": previous}
    assert payload["current"] == {"read": True, "addTags": ["new"], "removeTags": ["old"]}
    assert payload["updated"] == 1
    assert payload["failures"] == [2]


async def test_empty_id_list_is_rejected(fake_bridge) -> None:
    bridge = fake_bridge()
    async with Client(_server(bridge)) as client:
        result = await client.call_tool("mail_mark", {"message_ids": [], "read": True})
    assert result.is_error


async def test_read_only_mode_hides_the_write_tools(fake_bridge) -> None:
    bridge = fake_bridge()
    async with Client(_server(bridge, read_only=True)) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
    assert "mail_search" in names
    assert "mail_delete" not in names
    assert "mail_move" not in names


async def test_yolo_skips_the_gate(fake_bridge) -> None:
    bridge = fake_bridge({"messages.delete": {"deleted": 1}})
    async with Client(_server(bridge, yolo=True)) as client:
        result = await client.call_tool("mail_delete", {"message_ids": [42]})
    assert not result.is_error, _text(result)
    assert "messages.delete" in bridge.methods()


async def test_body_is_truncated_rather_than_dropped(fake_bridge) -> None:
    from tbmcp.tools._common import MAX_TEXT

    bridge = fake_bridge(
        {
            "messages.read": {
                "header": MESSAGE,
                "body": "x" * (MAX_TEXT + 100),
                "bodyIsHtml": False,
                "attachments": [],
            }
        }
    )
    async with Client(_server(bridge)) as client:
        result = await client.call_tool("mail_get", {"message_id": 42})
    assert not result.is_error, _text(result)
    payload = result.structured_content
    assert payload["bodyTruncated"] is True
    assert len(payload["body"]) == MAX_TEXT


async def test_get_many_has_a_ceiling(fake_bridge) -> None:
    bridge = fake_bridge()
    async with Client(_server(bridge)) as client:
        result = await client.call_tool("mail_get_many", {"message_ids": list(range(51))})
    assert result.is_error
    assert "50" in _text(result)
