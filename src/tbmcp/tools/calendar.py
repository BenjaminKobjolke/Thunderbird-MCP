"""Calendars, events and tasks.

Thunderbird exposes no WebExtension calendar API, so every tool here talks to the
privileged half through `x.calendar.*`.

Two conventions run through the whole toolset. Dates are ISO-8601 in and out, and
every date that comes back carries the timezone it is stored in plus an `allDay`
flag. Recurring items are addressed as a whole series unless a tool is given
`occurrence_date`, and the result always says which of the two it acted on.
"""

from typing import Any

from ..errors import UsageError
from ..safety import DESTRUCTIVE, IDEMPOTENT_WRITE, MUTATING, Gate, guard_write, require
from ..server import Registrar
from ._common import call, changed, dry_run, page
from .calendar_common import CalendarType, _present
from .calendar_events import register as register_events
from .calendar_tasks import register as register_tasks


def register(reg: Registrar) -> None:
    @reg.read_tool(title="List calendars")
    async def calendar_list() -> dict[str, Any]:
        """List the user's calendars, with ids, types and whether each is writable.

        Every other tool here takes one of these ids. A calendar marked
        `readOnly` refuses writes; `disabled` ones are skipped when listing items.
        """
        result = await call("x.calendar.listCalendars", timeout=30.0)
        return page(
            result.get("calendars", []),
            defaultTimezone=result.get("defaultTimezone"),
        )

    @reg.write_tool(title="Create a calendar", annotations=MUTATING)
    async def calendar_create(
        name: str,
        type: CalendarType = "storage",
        url: str | None = None,
        color: str | None = None,
        confirm: bool = False,
        consent: Gate("create a calendar") = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Create a calendar and register it with Thunderbird.

        `storage` keeps events in the local profile and needs no `url`; `ics` and
        `caldav` need the file or collection URL. `color` is `#RRGGBB`.
        """
        guard_write("create calendars")
        if type in ("ics", "caldav") and not url:
            raise UsageError(f"a {type} calendar needs url — the ICS file or CalDAV collection.")
        require(consent, "create this calendar")
        result = await call(
            "x.calendar.createCalendar",
            {"name": name, "type": type, **_present(uri=url, color=color)},
            timeout=60.0,
        )
        return changed("calendar", before=None, after=result.get("calendar"))

    @reg.write_tool(title="Change a calendar", annotations=IDEMPOTENT_WRITE)
    async def calendar_update(
        calendar_id: str,
        name: str | None = None,
        color: str | None = None,
        read_only: bool | None = None,
        disabled: bool | None = None,
        confirm: bool = False,
        consent: Gate("change a calendar's settings") = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Rename or recolour a calendar, or toggle read-only and disabled.

        Only the fields you pass are touched. Nothing here affects the events
        inside the calendar.
        """
        guard_write("change calendars")
        fields = _present(name=name, color=color, readOnly=read_only, disabled=disabled)
        if not fields:
            raise UsageError("Nothing to change — pass name, color, read_only or disabled.")
        require(consent, "change this calendar")
        result = await call(
            "x.calendar.updateCalendar", {"calendarId": calendar_id, **fields}, timeout=45.0
        )
        return changed("calendar", before=result.get("previous"), after=result.get("current"))

    @reg.write_tool(title="Delete a calendar", annotations=DESTRUCTIVE)
    async def calendar_delete(
        calendar_id: str,
        unregister_only: bool = False,
        confirm: bool = False,
        consent: Gate("delete a calendar and everything in it") = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Remove a calendar. Deletes its events and tasks with it.

        `unregister_only=true` is the reversible half: the calendar disappears
        from Thunderbird but its data is left where it is, ready to be added
        again. Prefer it unless the user asked for the data to go too.
        """
        guard_write("delete calendars")
        if dry_run_only:
            return dry_run(
                "x.calendar.deleteCalendar",
                {"calendarId": calendar_id, "unregisterOnly": unregister_only},
            )
        require(consent, "delete this calendar")
        result = await call(
            "x.calendar.deleteCalendar",
            {"calendarId": calendar_id, "unregisterOnly": unregister_only},
            timeout=90.0,
        )
        return changed(
            "calendar",
            before=result.get("previous"),
            after=None,
            dataDeleted=result.get("dataDeleted"),
            recoverable=not result.get("dataDeleted", True),
        )

    register_events(reg)

    register_tasks(reg)
