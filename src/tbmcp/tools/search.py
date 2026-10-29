"""Global search and saved searches.

Two different search engines live behind this toolset, and choosing wrongly is the
main way an agent wastes turns here:

- `search_global` asks Thunderbird's **global index** (Gloda). It is ranked, spans
  every indexed folder and account at once, and understands conversations. It only
  knows about messages the indexer has already processed.
- `mail_search` (the `mail` toolset) filters folder databases directly. It is exact,
  works on anything Thunderbird can see whether indexed or not, and takes structured
  predicates.

Rule of thumb: a vague question about content goes to `search_global`; a precise
question about a known folder, flag, sender or date range goes to `mail_search`.
"""

from typing import Any

from ..errors import UsageError
from ..safety import (
    large_output,
)
from ..server import Registrar
from ._common import call, clamp, page
from .search_saved import register_saved


def register(reg: Registrar) -> None:
    # ------------------------------------------------------------- global index

    @reg.read_tool(title="Search everything", meta=large_output())
    async def search_global(
        query: str,
        limit: int = 25,
        offset: int = 0,
        folder_id: str | None = None,
    ) -> dict[str, Any]:
        """Ranked full-corpus search across every indexed folder and account.

        Best for open questions — "what did we agree about the shipment", "anything
        from the accountant about VAT". Results carry a relevance score and a
        conversation id you can pass to `search_conversation`. For precise filters
        (one folder, unread only, a date range) use `mail_search` instead.

        If this returns nothing unexpectedly, call `search_index_status`: the global
        indexer can be disabled or still catching up.

        `matched` is how many of the retrieved messages matched, and it is the
        total only when `truncated` is absent; a truncated search ranked as deep
        as it could and there may be more below. `unmatchableTerms` names words
        the index cannot look up at all — anything that breaks into pieces of
        fewer than three characters, like "2.0" — and because every term has to
        match, one of those is enough to empty the result.
        """
        if not query or not query.strip():
            raise UsageError("query is required — pass the words to search for.")
        result = await call(
            "x.gloda.search",
            {
                "query": query,
                "limit": clamp(limit, default=25, minimum=1, maximum=200, field="limit"),
                "offset": max(0, offset),
                "folderId": folder_id,
            },
            timeout=120.0,
        )
        hits = result.get("hits") or result.get("messages") or []
        truncated = bool(result.get("truncated"))
        # A null field reads to a model as a fact about the mailbox, so anything
        # the add-on did not send is left out rather than sent as null.
        reported = {
            key: result[key]
            for key in ("matched", "retrieved", "indexEnabled", "unmatchableTerms", "note")
            if result.get(key) is not None
        }
        return page(
            hits,
            # `matched` counts what the ranking saw. Once the retrieval was cut
            # short that is no longer a total of anything.
            total=None if truncated else result.get("matched"),
            truncated=truncated,
            query=query,
            **reported,
        )

    @reg.read_tool(title="Read a whole conversation", meta=large_output())
    async def search_conversation(
        message_id: int | None = None,
        header_message_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Every message in one thread, oldest first, across folders and accounts.

        Give either a `message_id` from `mail_search` or an RFC `header_message_id`.
        This is how you reconstruct a discussion that spans Inbox, Sent and an
        archive folder without three separate searches.
        """
        if message_id is None and not header_message_id:
            raise UsageError("Pass message_id or header_message_id.")
        params: dict[str, Any] = {
            "limit": clamp(limit, default=100, minimum=1, maximum=500, field="limit")
        }
        if message_id is not None:
            params["messageId"] = message_id
        else:
            params["headerMessageId"] = header_message_id
        result = await call("x.gloda.conversation", params, timeout=120.0)
        known = {
            key: result[key]
            for key in ("conversationId", "subject", "participants")
            if result.get(key) is not None
        }
        return page(
            result.get("messages") or [],
            # The thread can be longer than the page asked for.
            total=result.get("total"),
            **known,
        )

    @reg.read_tool(title="Global index status")
    async def search_index_status() -> dict[str, Any]:
        """Whether Thunderbird's global index is enabled, and how far along it is.

        Call this to explain an empty `search_global` result. When indexing is off,
        `mail_search` with `subject`/`author`/`body` filters still works.
        """
        result = await call("x.gloda.stats", timeout=30.0)
        return result or {}

    register_saved(reg)
