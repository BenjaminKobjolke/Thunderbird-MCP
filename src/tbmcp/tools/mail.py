"""Reading, triaging and moving mail — the reference toolset.

Every other toolset follows the shape established here: read tools via
`registrar.read_tool`, mutating tools via `registrar.write_tool` with a `confirm`
parameter, a `Gate(...)` consent, `guard_write()`, and a `changed()`/`dry_run()`
return envelope.
"""

# NOTE: no `from __future__ import annotations` in toolset modules. Tool signatures
# must evaluate at definition time so `Gate(...)` produces a real
# `Annotated[Consent, Resolve(...)]` rather than a string the SDK has to re-evaluate.

from typing import Any, Literal

from ..errors import UsageError
from ..safety import large_output
from ..server import Registrar
from ._common import (
    call,
    clamp,
    coerce_date,
    message_summary,
    one_of,
    page,
    require_ids,
    trim,
)
from .folders import SpecialUse
from .mail_actions import register_actions


def register(reg: Registrar) -> None:
    # ------------------------------------------------------------------ searching

    @reg.read_tool(title="Search mail", meta=large_output())
    async def mail_search(
        full_text: str | None = None,
        subject: str | None = None,
        author: str | None = None,
        recipients: str | None = None,
        body: str | None = None,
        folder_id: str | None = None,
        account_id: str | None = None,
        include_subfolders: bool = True,
        unread: bool | None = None,
        flagged: bool | None = None,
        junk: bool | None = None,
        has_attachment: bool | None = None,
        tags: list[str] | None = None,
        tag_mode: Literal["all", "any", "none"] = "any",
        from_date: str | None = None,
        to_date: str | None = None,
        to_me: bool | None = None,
        from_me: bool | None = None,
        min_size: int | None = None,
        max_size: int | None = None,
        limit: int = 25,
        cursor: str | None = None,
        sort: Literal["newest", "oldest", "none"] = "newest",
    ) -> dict[str, Any]:
        """Search the user's mail. Combine `full_text` with any filters below.

        `full_text` uses Thunderbird's global index and searches headers and bodies
        of already-indexed messages; `subject`/`author`/`body` are substring matches
        evaluated per folder. Dates are ISO-8601. Results come newest first; `sort`
        "oldest" reverses that, "none" returns folder storage order (faster on huge
        result sets). Results are summaries — call
        `mail_get` for a body. Continue with `cursor=nextCursor`. A first page
        carries `scope` — the folder and account ids the query covered — so an empty
        result can be read against what was actually searched.
        """
        query: dict[str, Any] = {}
        if full_text:
            query["fullText"] = full_text
        if subject:
            query["subject"] = subject
        if author:
            query["author"] = author
        if recipients:
            query["recipients"] = recipients
        if body:
            query["body"] = body
        if folder_id:
            query["folderId"] = folder_id
        if account_id:
            query["accountId"] = account_id
        if folder_id or account_id:
            query["includeSubFolders"] = include_subfolders
        for name, value in (
            ("unread", unread),
            ("flagged", flagged),
            ("junk", junk),
            ("attachment", has_attachment),
            ("toMe", to_me),
            ("fromMe", from_me),
        ):
            if value is not None:
                query[name] = value
        if tags:
            query["tags"] = {
                "tags": dict.fromkeys(tags, True),
                "mode": one_of(tag_mode, ("all", "any", "none"), field="tag_mode", default="any"),
            }
        start = coerce_date(from_date, field="from_date")
        end = coerce_date(to_date, field="to_date")
        if start:
            query["fromDate"] = start
        if end:
            query["toDate"] = end
        if min_size is not None or max_size is not None:
            size: dict[str, int] = {}
            if min_size is not None:
                size["min"] = min_size
            if max_size is not None:
                size["max"] = max_size
            query["size"] = size
        if not query:
            raise UsageError(
                "Give at least one filter — a bare search over every folder would be "
                "slow and useless. `full_text` is usually what you want."
            )

        result = await call(
            "messages.query",
            {
                "query": query,
                "limit": clamp(limit, default=25, minimum=1, maximum=200, field="limit"),
                "cursor": cursor,
                "sort": one_of(sort, ("newest", "oldest", "none"), field="sort", default="newest"),
            },
            timeout=90.0,
        )
        # A null field reads to a model as a fact about the mailbox, so anything
        # the add-on did not send is left out rather than sent as null. `scope`
        # comes with a first page only; a continuation is by definition the same
        # search.
        reported = {
            key: result[key] for key in ("scope", "indexNote") if result.get(key) is not None
        }
        return page(
            [message_summary(m) for m in result.get("messages", [])],
            cursor=result.get("cursor"),
            total=result.get("totalAvailable"),
            **reported,
        )

    @reg.read_tool(title="List mail")
    async def mail_list(
        folder_id: str | None = None,
        limit: int = 25,
        cursor: str | None = None,
        sort_by: Literal["date", "subject", "author", "size", "read", "flagged"] = "date",
        descending: bool = True,
        special_use: SpecialUse | None = None,
    ) -> dict[str, Any]:
        """List one folder or every folder of a type, newest first by default.

        `special_use="inbox"` lists the newest messages of every account's inbox
        in one call — the answer to "what is my newest email". Use `folder_list`
        to discover one folder's id. For selective queries, use `mail_search`.
        """
        if bool(folder_id) == bool(special_use):
            raise UsageError("Give exactly one of folder_id or special_use.")
        result = await call(
            "messages.list",
            {
                **({"folderId": folder_id} if folder_id else {"specialUse": special_use}),
                "limit": clamp(limit, default=25, minimum=1, maximum=200, field="limit"),
                "cursor": cursor,
                "sortType": sort_by,
                "sortOrder": "descending" if descending else "ascending",
            },
            timeout=60.0,
        )
        return page(
            [message_summary(m) for m in result.get("messages", [])],
            cursor=result.get("cursor"),
            total=result.get("totalAvailable"),
            **({"folderId": folder_id} if folder_id else {}),
            **({"folderIds": result["folderIds"]} if "folderIds" in result else {}),
        )

    # ------------------------------------------------------------------- reading

    @reg.read_tool(title="Read a message", meta=large_output())
    async def mail_get(
        message_id: int,
        detail: Literal["summary", "text", "full"] = "text",
        decrypt: bool = True,
    ) -> dict[str, Any]:
        """Read one message. `text` gives headers plus the plain-text body.

        `summary` skips the body entirely; `full` adds the MIME part tree and every
        header. Encrypted mail is decrypted when Thunderbird can.
        """
        detail = one_of(detail, ("summary", "text", "full"), field="detail", default="text")
        result = await call(
            "messages.read",
            {"messageId": message_id, "detail": detail, "decrypt": decrypt},
            timeout=60.0,
        )
        body, truncated = trim(result.get("body"))
        payload: dict[str, Any] = {
            **message_summary(result.get("header") or {}),
            "headerMessageId": (result.get("header") or {}).get("headerMessageId"),
        }
        if detail != "summary":
            payload["body"] = body
            payload["bodyIsHtml"] = result.get("bodyIsHtml", False)
            if truncated:
                payload["bodyTruncated"] = True
        if detail == "full":
            payload["headers"] = result.get("headers")
            payload["parts"] = result.get("parts")
            payload["decryptionStatus"] = result.get("decryptionStatus")
        payload["attachments"] = result.get("attachments") or []
        return payload

    @reg.read_tool(title="Read several messages at once", meta=large_output())
    async def mail_get_many(
        message_ids: list[int],
        detail: Literal["summary", "text"] = "summary",
    ) -> dict[str, Any]:
        """Read up to 50 messages in one round trip — for triaging a search result."""
        ids = require_ids(message_ids)
        if len(ids) > 50:
            raise UsageError("mail_get_many takes at most 50 ids; page through instead.")
        result = await call(
            "messages.readMany",
            {"messageIds": ids, "detail": detail},
            timeout=120.0,
        )
        return page(result.get("messages", []), failures=result.get("failures") or [])

    @reg.read_tool(title="Get raw message source", meta=large_output())
    async def mail_get_source(message_id: int, decrypt: bool = False) -> dict[str, Any]:
        """Fetch a message's raw RFC 5322 source, for header forensics.

        On IMAP this needs the message to be available offline; the tool says so
        rather than returning a partial.
        """
        result = await call(
            "messages.raw", {"messageId": message_id, "decrypt": decrypt}, timeout=120.0
        )
        source, truncated = trim(result.get("source"))
        return {
            "messageId": message_id,
            "source": source,
            "truncated": truncated,
            "bytes": result.get("bytes"),
        }

    register_actions(reg)
