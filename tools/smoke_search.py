#!/usr/bin/env python3
"""Live acceptance run for the search tools, against a running Thunderbird.

    python tools/smoke_search.py

Read-only: it searches, lists and reads, and changes nothing. It exists because
the mocked suite cannot see the add-on, and every one of the 1.2.0 search defects
passed a green run — this is the check that would have caught them. The corpus
is discovered from whatever mail the profile has, so nothing here is tied to one
mailbox; a check the corpus cannot support is skipped and says why.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mcp import Client
from smoke_search_checks import (
    MIN_FOLDER,
    Report,
    biggest_folder,
    common_term,
    invalid_folder_ids,
    out_of_order,
    paging_gap,
)

from tbmcp.config import Settings
from tbmcp.server import build_server

# ------------------------------------------------------------------- live run


def _error_text(result: Any) -> str:
    return " ".join(getattr(block, "text", "") for block in result.content)


async def _walk(call: Any, tool: str, args: dict[str, Any], step: int, pages: int) -> list[int]:
    ids: list[int] = []
    cursor = None
    for _ in range(pages):
        result = await call(tool, {**args, "limit": step, **({"cursor": cursor} if cursor else {})})
        if result.is_error:
            raise RuntimeError(_error_text(result))
        ids += [m["id"] for m in result.structured_content["items"]]
        cursor = result.structured_content.get("nextCursor")
        if not cursor:
            break
    return ids


async def main() -> int:
    settings = Settings().merged_with(toolsets=("mail", "folders", "search", "admin"))
    report = Report()
    async with Client(build_server(settings)) as client:
        call = client.call_tool

        status = report.check(
            "tb_status connected",
            await call("tb_status", {}),
            verify=lambda got: None if got.get("connected") else "Thunderbird is not attached",
        )
        if status is None:
            return 1
        print(
            f"        add-on {status.get('addonVersion')}, {status.get('app', {}).get('version')}"
        )

        folders = (await call("folder_list", {"limit": 200})).structured_content["items"]
        known = {f["id"] for f in folders}
        folder = biggest_folder(folders)
        term = None
        if folder is None:
            # Every check that needs a corpus is skipped one by one below, saying so;
            # the ones that do not — index status, the bad folder id — still run.
            report.skip("corpus", f"no real folder holds {MIN_FOLDER}+ messages")
        else:
            listed = (
                await call("mail_list", {"folder_id": folder["id"], "limit": 50})
            ).structured_content
            term = common_term(listed["items"])
            print(
                f"        corpus: {folder['id']} ({folder.get('totalMessageCount')} messages), "
                f"term {term!r}\n"
            )

        # ---- substring search
        if folder is None:
            report.skip("mail_search subject", "no corpus")
        elif term is None:
            report.skip("mail_search subject", "no word shared by three subjects")
        else:
            found = report.check(
                "mail_search subject",
                await call("mail_search", {"subject": term, "limit": 5}),
                "matches",
                verify=lambda got: (
                    "no matches"
                    if not got.get("count")
                    else "; ".join(invalid_folder_ids(got["items"], known)) or None
                ),
            )
            scoped = report.check(
                "mail_search subject + folder",
                await call("mail_search", {"subject": term, "folder_id": folder["id"], "limit": 5}),
                "only that folder, scope reported",
                verify=lambda got: (
                    "no scope block"
                    if not got.get("scope")
                    else (
                        "a hit outside the folder"
                        if {m["folderId"] for m in got["items"]} - {folder["id"]}
                        else None
                    )
                ),
            )
            del found, scoped

        # ---- ranked search
        ranked = None
        if term is not None:
            result = await call("search_global", {"query": term, "limit": 5})
            if not result.is_error and not result.structured_content.get("count"):
                report.skip("search_global ranked", f"the index has no hits for {term!r}")
            else:
                ranked = report.check(
                    "search_global ranked",
                    result,
                    "score and date on every hit",
                    verify=lambda got: (
                        "a hit without score or date"
                        if any(h.get("score") is None or not h.get("date") for h in got["items"])
                        else None
                    ),
                )
        if ranked:
            hit = ranked["items"][0]
            report.check(
                "search_global folder scoped",
                await call(
                    "search_global", {"query": term, "limit": 20, "folder_id": hit["folderId"]}
                ),
                "only that folder",
                verify=lambda got: (
                    "a hit outside the folder"
                    if {h["folderId"] for h in got["items"]} - {hit["folderId"]}
                    else None
                ),
            )
            thread = report.check(
                "search_conversation by id",
                await call("search_conversation", {"message_id": hit["id"]}),
                "oldest first, participants named",
                verify=lambda got: (
                    out_of_order([m.get("date") for m in got["items"]])
                    or (None if got.get("participants") else "no participants")
                ),
            )
            if thread:
                report.check(
                    "search_conversation by header id",
                    await call(
                        "search_conversation", {"header_message_id": hit["headerMessageId"]}
                    ),
                    "same conversation",
                    verify=lambda got: (
                        None
                        if got.get("conversationId") == thread.get("conversationId")
                        else "a different conversation"
                    ),
                )

        # ---- index and terms
        report.check(
            "search_index_status",
            await call("search_index_status", {}),
            "indexedMessages is a number",
            verify=lambda got: (
                None
                if not got.get("enabled") or isinstance(got.get("indexedMessages"), int)
                else f"indexedMessages is {got.get('indexedMessages')!r}"
            ),
        )
        if term is None:
            report.skip("search_global unmatchable term", "no term")
        else:
            report.check(
                "search_global unmatchable term",
                await call("search_global", {"query": f"{term} 2.0", "limit": 5}),
                "names 2.0",
                verify=lambda got: (
                    None
                    if got.get("unmatchableTerms") == ["2.0"]
                    else f"unmatchableTerms={got.get('unmatchableTerms')!r}"
                ),
            )
        bad = await call("search_global", {"query": "anything", "folder_id": "accountZZ://nope"})
        text = _error_text(bad)
        if bad.is_error and "accountZZ" in text and "unexpected error" not in text.lower():
            print(f"  PASS  {'search_global bad folder id':<34} the error names the folder")
        else:
            print(f"  FAIL  {'search_global bad folder id':<34} {text[:160] or 'no error at all'}")
            report.failures.append("search_global bad folder id")

        # ---- paging conservation
        if folder is None:
            report.skip("mail_list paging", "no corpus")
        else:
            straight = [
                m["id"]
                for m in (
                    await call("mail_list", {"folder_id": folder["id"], "limit": 12})
                ).structured_content["items"]
            ]
            if len(straight) < 12:
                report.skip("mail_list paging", "fewer than 12 messages")
            else:
                gap = paging_gap(
                    await _walk(call, "mail_list", {"folder_id": folder["id"]}, 3, 4), straight
                )
                _verdict(report, "mail_list paging 3x4 == 12", gap)
        if term is None:
            report.skip("mail_search paging", "no term")
        else:
            straight = [
                m["id"]
                for m in (
                    await call("mail_search", {"subject": term, "limit": 12})
                ).structured_content["items"]
            ]
            if len(straight) < 12:
                report.skip("mail_search paging", "fewer than 12 matches")
            else:
                gap = paging_gap(
                    await _walk(call, "mail_search", {"subject": term}, 3, 4), straight
                )
                _verdict(report, "mail_search paging 3x4 == 12", gap)

        # ---- a wide answer must not drop the connection
        if term is None:
            report.skip("search_global wide answer", "no term")
        else:
            wide = await call("search_global", {"query": term, "limit": 200})
            after = (await call("tb_status", {})).structured_content
            if wide.is_error or not after.get("connected"):
                print(
                    f"  FAIL  {'search_global wide answer':<34} {_error_text(wide)[:120] or 'the bridge dropped'}"
                )
                report.failures.append("search_global wide answer")
            else:
                print(
                    f"  PASS  {'search_global wide answer':<34} {wide.structured_content.get('count')} hits, still connected"
                )

    print()
    if report.failures:
        print(f"{len(report.failures)} check(s) failed: {', '.join(report.failures)}")
        return 1
    print("every search check passed against the live Thunderbird")
    return 0


def _verdict(report: Report, name: str, gap: str | None) -> None:
    if gap is None:
        print(f"  PASS  {name:<34} same ids, same order")
    else:
        print(f"  FAIL  {name:<34} {gap}")
        report.failures.append(name)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
