"""Calendar task listing and editing tools."""

from typing import Any

from ..errors import UsageError
from ..safety import MUTATING, Gate, guard_write, require
from ..server import Registrar
from ._common import call, changed, clamp, page
from .calendar_common import TaskStatus, _present, _priority, _when


def register(reg: Registrar) -> None:
    @reg.read_tool(title="List tasks")
    async def task_list(
        calendar_id: str | None = None,
        include_completed: bool = False,
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int = 50,
        timezone: str | None = None,
    ) -> dict[str, Any]:
        """List tasks, soonest due first. Completed ones are hidden by default.

        Unlike `event_list` this is not windowed by default: a task with no due
        date falls outside every range, and those are exactly the ones people
        forget. Pass `from_date`/`to_date` to narrow it.
        """
        result = await call(
            "x.calendar.listItems",
            {
                "kind": "task",
                "includeCompleted": include_completed,
                "limit": clamp(limit, default=50, minimum=1, maximum=500, field="limit"),
                **_present(
                    calendarId=calendar_id,
                    timezone=timezone,
                    **{
                        "from": _when(from_date, field="from_date"),
                        "to": _when(to_date, field="to_date"),
                    },
                ),
            },
            timeout=90.0,
        )
        return page(
            result.get("items", []),
            total=result.get("matched"),
            truncated=bool(result.get("truncated")),
            failures=result.get("failures") or [],
        )

    @reg.write_tool(title="Create a task", annotations=MUTATING)
    async def task_create(
        title: str,
        due: str | None = None,
        start: str | None = None,
        all_day: bool = False,
        calendar_id: str | None = None,
        timezone: str | None = None,
        description: str | None = None,
        location: str | None = None,
        categories: list[str] | None = None,
        percent_complete: int | None = None,
        completed: bool | None = None,
        status: TaskStatus | None = None,
        priority: int | None = None,
        recurrence_rule: str | None = None,
        confirm: bool = False,
        consent: Gate("create a task") = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Create a task. Everything but the title is optional.

        `start` is the entry date and `due` the deadline, both ISO-8601. A task
        with neither still appears in Thunderbird's task list, which is where
        undated to-dos belong.
        """
        guard_write("create tasks")
        if not str(title).strip():
            raise UsageError("title is required.")
        require(consent, "create this task")
        result = await call(
            "x.calendar.createTask",
            {
                "title": title,
                "allDay": all_day,
                **_present(
                    due=_when(due, field="due", all_day=all_day),
                    start=_when(start, field="start", all_day=all_day),
                    calendarId=calendar_id,
                    timezone=timezone,
                    description=description,
                    location=location,
                    categories=categories,
                    percentComplete=percent_complete,
                    completed=completed,
                    status=status,
                    priority=_priority(priority),
                    recurrenceRule=recurrence_rule,
                ),
            },
            timeout=90.0,
        )
        return changed(
            "task", before=None, after=result.get("item"), calendarId=result.get("calendarId")
        )

    @reg.write_tool(title="Change a task", annotations=MUTATING)
    async def task_update(
        item_id: str,
        calendar_id: str | None = None,
        occurrence_date: str | None = None,
        title: str | None = None,
        due: str | None = None,
        start: str | None = None,
        all_day: bool | None = None,
        timezone: str | None = None,
        description: str | None = None,
        location: str | None = None,
        categories: list[str] | None = None,
        percent_complete: int | None = None,
        completed: bool | None = None,
        status: TaskStatus | None = None,
        priority: int | None = None,
        recurrence_rule: str | None = None,
        confirm: bool = False,
        consent: Gate("change a task") = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Change a task, or tick it off with `completed=true`.

        Only the fields you pass are touched. `completed=true` also stamps the
        completion time and sets progress to 100%. For a repeating task this
        rewrites the series unless `occurrence_date` names one instance.
        """
        guard_write("change tasks")
        fields = _present(
            title=title,
            due=_when(due, field="due", all_day=bool(all_day)),
            start=_when(start, field="start", all_day=bool(all_day)),
            allDay=all_day,
            calendarId=calendar_id,
            timezone=timezone,
            description=description,
            location=location,
            categories=categories,
            percentComplete=percent_complete,
            completed=completed,
            status=status,
            priority=_priority(priority),
            recurrenceRule=recurrence_rule,
        )
        if not {k: v for k, v in fields.items() if k not in ("calendarId", "timezone")}:
            raise UsageError(
                "Nothing to change — pass at least one of title, due, start, completed, "
                "percent_complete, status, priority, description or categories."
            )
        if percent_complete is not None and not (
            isinstance(percent_complete, int) and 0 <= percent_complete <= 100
        ):
            raise UsageError("percent_complete must be an integer from 0 to 100.")
        require(consent, "change this task")
        result = await call(
            "x.calendar.updateTask",
            {
                "itemId": item_id,
                **fields,
                **_present(occurrenceDate=_when(occurrence_date, field="occurrence_date")),
            },
            timeout=90.0,
        )
        return changed(
            "task",
            before=result.get("previous"),
            after=result.get("item"),
            addressed=result.get("addressed"),
            note=result.get("note"),
        )
