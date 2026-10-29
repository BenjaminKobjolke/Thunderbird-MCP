"""Calendar event listing and editing tools."""

from typing import Any

from ..errors import UsageError
from ..safety import DESTRUCTIVE, MUTATING, Gate, guard_write, require
from ..server import Registrar
from ._common import call, changed, clamp, dry_run, page
from .calendar_common import EventStatus, Privacy, _attendees, _present, _priority, _when, _window


def register(reg: Registrar) -> None:
    @reg.read_tool(title="List calendar events")
    async def event_list(
        calendar_id: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        days: int = 30,
        include_occurrences: bool = True,
        limit: int = 50,
        timezone: str | None = None,
    ) -> dict[str, Any]:
        """List events in a time window, soonest first.

        Defaults to the next 30 days across every enabled calendar; pass
        `from_date`/`to_date` (ISO-8601) for anything else, or `days` to widen the
        default window. `include_occurrences` expands recurring series into the
        individual meetings that fall inside the window, which is what you want
        for "what is on next week".
        """
        start, end = _window(from_date, to_date, clamp(days, default=30, minimum=1, maximum=1095))
        result = await call(
            "x.calendar.listItems",
            {
                "kind": "event",
                "from": start,
                "to": end,
                "limit": clamp(limit, default=50, minimum=1, maximum=500, field="limit"),
                "expandOccurrences": include_occurrences,
                **_present(calendarId=calendar_id, timezone=timezone),
            },
            timeout=90.0,
        )
        return page(
            result.get("items", []),
            total=result.get("matched"),
            truncated=bool(result.get("truncated")),
            window=result.get("window"),
            failures=result.get("failures") or [],
        )

    @reg.read_tool(title="Read one event or task")
    async def event_get(
        item_id: str,
        calendar_id: str | None = None,
        occurrence_date: str | None = None,
        timezone: str | None = None,
    ) -> dict[str, Any]:
        """Read one event or task in full, including attendees and recurrence.

        Searches every calendar unless `calendar_id` narrows it. For a recurring
        series this returns the series plus its next few start times; pass
        `occurrence_date` to read a single occurrence instead.
        """
        result = await call(
            "x.calendar.getItem",
            {
                "itemId": item_id,
                **_present(
                    calendarId=calendar_id,
                    occurrenceDate=_when(occurrence_date, field="occurrence_date"),
                    timezone=timezone,
                ),
            },
            timeout=60.0,
        )
        return {
            "item": result.get("item"),
            "addressed": result.get("addressed"),
            "series": result.get("series"),
            "nextOccurrences": result.get("nextOccurrences"),
            "note": result.get("note"),
        }

    @reg.write_tool(title="Create an event", annotations=MUTATING)
    async def event_create(
        title: str,
        start: str,
        end: str | None = None,
        all_day: bool = False,
        duration_minutes: int | None = None,
        calendar_id: str | None = None,
        timezone: str | None = None,
        location: str | None = None,
        description: str | None = None,
        categories: list[str] | None = None,
        attendees: list[str] | None = None,
        organizer: str | None = None,
        recurrence_rule: str | None = None,
        url: str | None = None,
        status: EventStatus | None = None,
        privacy: Privacy | None = None,
        priority: int | None = None,
        confirm: bool = False,
        consent: Gate("create a calendar event") = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Create an event. Omit `end` for a one-hour meeting.

        `start`/`end` are ISO-8601; a time with no offset is read as wall-clock
        time in `timezone` (the calendar's default if unset). For `all_day=true`
        pass plain dates — the end is exclusive per iCalendar, so omit it for a
        single day. `recurrence_rule` is an RRULE body such as
        `FREQ=WEEKLY;BYDAY=TU;COUNT=10`. Attendees are stored but no invitations
        are sent.
        """
        guard_write("create calendar events")
        if not str(title).strip():
            raise UsageError("title is required — an untitled event is invisible in the UI.")
        require(consent, "create this event")
        result = await call(
            "x.calendar.createEvent",
            {
                "title": title,
                "start": _when(start, field="start", all_day=all_day),
                "allDay": all_day,
                **_present(
                    end=_when(end, field="end", all_day=all_day),
                    durationMinutes=duration_minutes,
                    calendarId=calendar_id,
                    timezone=timezone,
                    location=location,
                    description=description,
                    categories=categories,
                    attendees=_attendees(attendees) or None,
                    organizer=(
                        _attendees([organizer], field="organizer")[0] if organizer else None
                    ),
                    recurrenceRule=recurrence_rule,
                    url=url,
                    status=status,
                    privacy=privacy,
                    priority=_priority(priority),
                ),
            },
            timeout=90.0,
        )
        return changed(
            "event",
            before=None,
            after=result.get("item"),
            calendarId=result.get("calendarId"),
            allDayEndIsExclusive=result.get("allDayEndIsExclusive"),
            note=result.get("note"),
        )

    @reg.write_tool(title="Change an event", annotations=MUTATING)
    async def event_update(
        item_id: str,
        # CPD-OFF
        # These fields repeat the create schema so update keeps its own public input contract.
        calendar_id: str | None = None,
        occurrence_date: str | None = None,
        title: str | None = None,
        start: str | None = None,
        end: str | None = None,
        all_day: bool | None = None,
        timezone: str | None = None,
        location: str | None = None,
        description: str | None = None,
        categories: list[str] | None = None,
        attendees: list[str] | None = None,
        organizer: str | None = None,
        recurrence_rule: str | None = None,
        url: str | None = None,
        status: EventStatus | None = None,
        privacy: Privacy | None = None,
        priority: int | None = None,
        confirm: bool = False,
        consent: Gate("change a calendar event") = None,  # type: ignore[valid-type]
        # CPD-ON
    ) -> dict[str, Any]:
        """Change an event. Only the fields you pass are touched.

        For a recurring event this rewrites the **whole series**; pass
        `occurrence_date` (the start of the one you mean) to change a single
        occurrence and leave the rest alone. The result says which it did. Pass an
        empty string to clear `location`, `description` or `url`.
        """
        guard_write("change calendar events")
        fields = _present(
            title=title,
            start=_when(start, field="start", all_day=bool(all_day)),
            end=_when(end, field="end", all_day=bool(all_day)),
            allDay=all_day,
            calendarId=calendar_id,
            timezone=timezone,
            location=location,
            description=description,
            categories=categories,
            attendees=_attendees(attendees) if attendees is not None else None,
            organizer=(_attendees([organizer], field="organizer")[0] if organizer else None),
            recurrenceRule=recurrence_rule,
            url=url,
            status=status,
            privacy=privacy,
            priority=_priority(priority),
        )
        if not {k: v for k, v in fields.items() if k not in ("calendarId", "timezone")}:
            raise UsageError(
                "Nothing to change — pass at least one of title, start, end, all_day, "
                "location, description, categories, attendees, recurrence_rule, status, "
                "privacy or priority."
            )
        require(consent, "change this event")
        result = await call(
            "x.calendar.updateEvent",
            {
                "itemId": item_id,
                **fields,
                **_present(occurrenceDate=_when(occurrence_date, field="occurrence_date")),
            },
            timeout=90.0,
        )
        return changed(
            "event",
            before=result.get("previous"),
            after=result.get("item"),
            addressed=result.get("addressed"),
            note=result.get("note"),
        )

    @reg.write_tool(title="Delete an event or task", annotations=DESTRUCTIVE)
    async def event_delete(
        item_id: str,
        calendar_id: str | None = None,
        occurrence_date: str | None = None,
        timezone: str | None = None,
        confirm: bool = False,
        consent: Gate("delete a calendar item") = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Delete an event or a task. Calendars have no trash, so this is final.

        For a recurring item this deletes the **entire series**. Pass
        `occurrence_date` to cancel one occurrence and keep the rest; the result
        says which it did. Read the item with `event_get` first if you are not
        sure it recurs.
        """
        guard_write("delete calendar items")
        occurrence = _when(occurrence_date, field="occurrence_date")
        if dry_run_only:
            return dry_run(
                "x.calendar.deleteItem",
                {"itemId": item_id, "calendarId": calendar_id, "occurrenceDate": occurrence},
            )
        require(consent, "delete this calendar item")
        result = await call(
            "x.calendar.deleteItem",
            {
                "itemId": item_id,
                **_present(calendarId=calendar_id, occurrenceDate=occurrence, timezone=timezone),
            },
            timeout=90.0,
        )
        return changed(
            "calendarItem",
            before=result.get("item") or result.get("occurrence"),
            after=result.get("series"),
            addressed=result.get("addressed"),
            recoverable=False,
            note=result.get("note"),
        )
