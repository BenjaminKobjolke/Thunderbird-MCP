"""Shared calendar value types and normalization helpers."""

import datetime as _dt
import re
from typing import Any, Literal

from ..errors import UsageError
from ._common import coerce_date

CalendarType = Literal["storage", "memory", "ics", "caldav"]


EventStatus = Literal["TENTATIVE", "CONFIRMED", "CANCELLED"]


TaskStatus = Literal["NEEDS-ACTION", "IN-PROCESS", "COMPLETED", "CANCELLED"]


Privacy = Literal["PUBLIC", "PRIVATE", "CONFIDENTIAL"]


_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


_NAMED_ADDRESS = re.compile(r"^(?P<name>.*?)\s*<(?P<email>[^<>]+)>$")


def _when(value: str | None, *, field: str, all_day: bool = False) -> str | None:
    """Normalise one calendar date, keeping all-day values date-only.

    `coerce_date` would expand `2026-07-29` to midnight, which the add-on cannot
    tell apart from a timed event that happens to start at 00:00.
    """
    if value is None or not str(value).strip():
        return None
    normalised = coerce_date(value, field=field)
    if normalised is None:
        return None
    if all_day or _DATE_ONLY.match(str(value).strip()):
        return normalised[:10]
    return normalised


def _attendees(values: list[str] | None, *, field: str = "attendees") -> list[dict[str, Any]]:
    """Accept `a@b.com` or `Alice <a@b.com>` and hand the bridge one shape."""
    out: list[dict[str, Any]] = []
    for entry in values or []:
        text = str(entry).strip()
        match = _NAMED_ADDRESS.match(text)
        email = (match.group("email") if match else text).strip()
        name = (match.group("name") or "").strip() if match else ""
        if "@" not in email or " " in email:
            raise UsageError(
                f"{field} entries must be email addresses, optionally as "
                f"'Alice <alice@example.com>'; got {entry!r}."
            )
        out.append({"email": email, "name": name or None})
    return out


def _priority(value: int | None) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or not 0 <= value <= 9:
        raise UsageError(
            "priority must be an integer from 0 (undefined) to 9; iCalendar treats "
            "1-4 as high, 5 as normal and 6-9 as low."
        )
    return value


def _window(from_date: str | None, to_date: str | None, days: int) -> tuple[str | None, str | None]:
    """Default a listing window to now → +`days`.

    Asking a calendar for everything it has is both slow and useless — the answer
    is dominated by last year's meetings — so an omitted window becomes a month
    from now rather than unbounded.
    """
    start = _when(from_date, field="from_date")
    end = _when(to_date, field="to_date")
    if start is None:
        start = _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()
    if end is None:
        try:
            anchor = _dt.datetime.fromisoformat(start)
        except ValueError:  # pragma: no cover - _when already validated it
            anchor = _dt.datetime.now().astimezone()
        end = (anchor + _dt.timedelta(days=days)).replace(microsecond=0).isoformat()
    return start, end


def _present(**fields: Any) -> dict[str, Any]:
    """Drop unsupplied fields: the add-on reads an absent key as "leave alone"."""
    return {key: value for key, value in fields.items() if value is not None}
