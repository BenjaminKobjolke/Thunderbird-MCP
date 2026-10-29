"""Pure checks used by the live smoke-search script and its unit tests."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from itertools import pairwise
from typing import Any

MIN_FOLDER = 20
MIN_SHARED = 3
MIN_WORD = 4
WORD = re.compile(r"[A-Za-z0-9]+")


def biggest_folder(folders: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The fullest real folder, or None when nothing holds MIN_FOLDER messages.

    Saved searches are skipped: a virtual folder counts messages it does not hold
    and cannot be paged like a real one.
    """
    best: dict[str, Any] | None = None
    for folder in folders:
        if folder.get("isVirtual") or folder.get("isRoot"):
            continue
        count = folder.get("totalMessageCount") or 0
        if count < MIN_FOLDER:
            continue
        if best is None or count > (best.get("totalMessageCount") or 0):
            best = folder
    return best


def common_term(messages: list[dict[str, Any]]) -> str | None:
    """A word (>= MIN_WORD letters) that at least MIN_SHARED subjects share.

    Case-insensitive when counting, but the spelling returned is one that was
    actually seen: `subject` is a case-sensitive substring match on the far side,
    so the live search has to ask for a string that exists. One subject counts
    once however often it repeats the word.
    """
    counts: Counter[str] = Counter()
    seen_as: dict[str, str] = {}
    first_seen: dict[str, int] = {}
    for message in messages:
        # In subject order, so a tie between two words is settled by which one a
        # reader met first — deterministic, unlike set iteration.
        words = [w for w in WORD.findall(message.get("subject") or "") if len(w) >= MIN_WORD]
        for word in dict.fromkeys(w.lower() for w in words):
            counts[word] += 1
            first_seen.setdefault(word, len(first_seen))
        for word in words:
            seen_as.setdefault(word.lower(), word)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))
    for word, n in ranked:
        if n >= MIN_SHARED:
            return seen_as[word]
    return None


def paging_gap(walked: list[int], straight: list[int]) -> str | None:
    """Why a cursor walk differs from one straight call, or None when it matches."""
    if walked == straight:
        return None
    missing = [i for i in straight if i not in walked]
    invented = [i for i in walked if i not in straight]
    if missing or invented:
        parts = []
        if missing:
            parts.append(f"{len(missing)} missing ({missing[:6]})")
        if invented:
            parts.append(f"ids the walk invented: {invented[:6]}")
        return "; ".join(parts)
    return f"same ids in a different order: walked {walked[:6]}, straight {straight[:6]}"


def invalid_folder_ids(items: list[dict[str, Any]], known: set[str] | None) -> list[str]:
    """Items whose folderId is not one the profile knows (shape only without a list)."""
    bad: list[str] = []
    for item in items:
        folder_id = item.get("folderId")
        if not isinstance(folder_id, str) or ":/" not in folder_id:
            bad.append(f"{folder_id!r} is not a folder id")
        elif known is not None and folder_id not in known:
            bad.append(f"{folder_id} is not a folder this profile lists")
    return bad


def out_of_order(dates: list[str | None]) -> str | None:
    """The first pair of dates that goes backwards, or None when ascending."""
    for earlier, later in pairwise(dates):
        if earlier and later and later < earlier:
            return f"{later} comes after {earlier}"
    return None


class Report:
    """Prints one line per check and remembers which ones failed."""

    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(
        self,
        name: str,
        result: Any,
        summary: str = "",
        *,
        verify: Callable[[Any], str | None] | None = None,
    ) -> Any:
        """PASS/FAIL one call. Returns the payload on success, None on failure."""
        if getattr(result, "is_error", False):
            text = " ".join(getattr(block, "text", "") for block in result.content).strip()
            print(f"  FAIL  {name:<34} {text[:160] or 'the tool returned an error'}")
            self.failures.append(name)
            return None
        payload = result.structured_content
        problem = verify(payload) if verify else None
        if problem:
            print(f"  FAIL  {name:<34} {problem[:160]}")
            self.failures.append(name)
            return None
        print(f"  PASS  {name:<34} {summary}")
        return payload

    def skip(self, name: str, why: str) -> None:
        print(f"  SKIP  {name:<34} {why}")
