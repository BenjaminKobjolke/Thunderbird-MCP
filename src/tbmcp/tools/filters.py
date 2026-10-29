"""Message filters — Thunderbird's own rule engine.

Filters live per incoming server, are ordered, and stop at the first rule whose
actions say so. That ordering is the part callers get wrong, so every tool here
reports the index of what it touched and `filter_list` always returns rules in
execution order.

A filter is `{name, enabled, runWhen[], searchTerms[], actions[]}`. Terms and
actions use friendly names rather than the XPCOM integers; the privileged half
translates and, when a name is unknown, answers with the full list of valid ones.
"""

from typing import Any

from ..errors import UsageError
from ..safety import DESTRUCTIVE, IDEMPOTENT_WRITE, MUTATING, Gate, guard_write, require
from ..server import Registrar
from ._common import call, changed, dry_run, page
from .filters_rules import _validate_actions, _validate_run_when, _validate_terms


def register(reg: Registrar) -> None:
    @reg.read_tool(title="List message filters")
    async def filter_list(account_key: str | None = None) -> dict[str, Any]:
        """List filters in execution order, with their conditions and actions.

        Omit `account_key` for every account. `index` is the position in the list and
        is what the other `filter_*` tools take — it shifts when filters are added,
        removed or reordered, so re-list before acting on a stale index.
        """
        result = await call("x.filters.list", {"accountKey": account_key}, timeout=45.0)
        return page(
            result.get("filters") or [],
            accounts=result.get("accounts"),
            hint="Filters run top to bottom; a stopExecution action ends the chain.",
        )

    @reg.read_tool(title="Get one filter")
    async def filter_get(account_key: str, index: int) -> dict[str, Any]:
        """Read one filter in full, by account and index."""
        result = await call("x.filters.list", {"accountKey": account_key}, timeout=45.0)
        for entry in result.get("filters") or []:
            if entry.get("index") == index:
                return entry
        available = [e.get("index") for e in result.get("filters") or []]
        raise UsageError(
            f"account {account_key} has no filter at index {index} "
            f"(present: {available or 'none'})."
        )

    @reg.write_tool(title="Create a filter", annotations=MUTATING)
    async def filter_create(
        account_key: str,
        name: str,
        search_terms: list[dict[str, Any]],
        actions: list[dict[str, Any]],
        run_when: list[str] | None = None,
        match_all: bool = True,
        enabled: bool = True,
        position: int | None = None,
        confirm: bool = False,
        consent: Gate("create a message filter") = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Create a filter. It is appended, so existing rules keep their order.

        Conditions: `[{"attribute": "from", "operator": "contains", "value": "bank"}]`.
        Actions: `[{"type": "moveToFolder", "folderId": "account1://Bank"}]`.
        `run_when` defaults to `["inbox", "manual"]` — on new mail, and when filters
        are applied by hand. `match_all=false` makes the conditions OR together.

        Filters act on real mail automatically from now on, so check the conditions
        carefully; `dry_run_only=true` shows what would be created.
        """
        guard_write("create filters")
        terms = _validate_terms(search_terms, required=True)
        checked_actions = _validate_actions(actions, required=True)
        for term in terms or []:
            term.setdefault("matchAll", match_all)
        params: dict[str, Any] = {
            "accountKey": account_key,
            "name": name,
            "searchTerms": terms,
            "actions": checked_actions,
            "enabled": enabled,
        }
        run = _validate_run_when(run_when)
        if run:
            params["runWhen"] = run
        if position is not None:
            params["position"] = position
        if dry_run_only:
            return dry_run("x.filters.create", params)
        require(consent, "create this filter")
        result = await call("x.filters.create", params, timeout=60.0)
        return changed(
            f"filter {name}", before=None, after=result.get("filter"), accountKey=account_key
        )

    @reg.write_tool(title="Change a filter", annotations=IDEMPOTENT_WRITE)
    async def filter_update(
        account_key: str,
        index: int,
        name: str | None = None,
        search_terms: list[dict[str, Any]] | None = None,
        actions: list[dict[str, Any]] | None = None,
        run_when: list[str] | None = None,
        enabled: bool | None = None,
        confirm: bool = False,
        consent: Gate("change a message filter") = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Change a filter in place. Only what you pass is touched.

        `search_terms` and `actions` each replace the whole list rather than merging —
        a partial merge of a boolean condition set has no sensible meaning. Read the
        current definition with `filter_get` first.
        """
        guard_write("change filters")
        params: dict[str, Any] = {"accountKey": account_key, "index": index}
        if name is not None:
            params["name"] = name
        terms = _validate_terms(search_terms, required=False)
        if terms is not None:
            params["searchTerms"] = terms
        checked_actions = _validate_actions(actions, required=False)
        if checked_actions is not None:
            params["actions"] = checked_actions
        run = _validate_run_when(run_when)
        if run is not None:
            params["runWhen"] = run
        if enabled is not None:
            params["enabled"] = enabled
        if len(params) == 2:
            raise UsageError(
                "Nothing to change — pass name, search_terms, actions, run_when or "
                "enabled. Use filter_set_enabled for a simple on/off."
            )
        if dry_run_only:
            return dry_run("x.filters.update", params)
        require(consent, "change this filter")
        result = await call("x.filters.update", params, timeout=60.0)
        return changed(
            f"filter {index} on {account_key}",
            before=result.get("previous"),
            after=result.get("current"),
        )

    @reg.write_tool(title="Enable or disable a filter", annotations=IDEMPOTENT_WRITE)
    async def filter_set_enabled(
        account_key: str,
        index: int,
        enabled: bool,
        confirm: bool = False,
        consent: Gate("enable or disable a message filter") = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Turn one filter on or off without changing its definition.

        Safer than deleting when you only want to stop a rule for now.
        """
        guard_write("enable or disable filters")
        require(consent, "change whether this filter runs")
        result = await call(
            "x.filters.setEnabled",
            {"accountKey": account_key, "index": index, "enabled": enabled},
            timeout=45.0,
        )
        return changed(
            f"filter {index} on {account_key}",
            before={"enabled": result.get("previous")},
            after={"enabled": result.get("current")},
            name=result.get("name"),
        )

    @reg.write_tool(title="Reorder a filter", annotations=MUTATING)
    async def filter_reorder(
        account_key: str,
        index: int,
        to_index: int,
        confirm: bool = False,
        consent: Gate("change the order message filters run in") = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Move a filter to a different position in the execution order.

        `to_index` is the position it should end up at. Order matters whenever a rule
        uses `stopExecution`, or when two rules would both move the same message.
        """
        guard_write("reorder filters")
        require(consent, "reorder these filters")
        result = await call(
            "x.filters.reorder",
            {"accountKey": account_key, "index": index, "toIndex": to_index},
            timeout=45.0,
        )
        return changed(
            f"filter order on {account_key}",
            before={"index": index},
            after={"index": result.get("index", to_index)},
            filters=result.get("filters"),
        )

    @reg.write_tool(title="Delete a filter", annotations=DESTRUCTIVE)
    async def filter_delete(
        account_key: str,
        index: int,
        confirm: bool = False,
        consent: Gate("delete a message filter") = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Delete a filter. Thunderbird keeps no history, so the rule is gone.

        The returned `previous` block is the full definition — keep it if you might
        want to recreate the rule. Consider `filter_set_enabled` instead.
        """
        guard_write("delete filters")
        params = {"accountKey": account_key, "index": index}
        if dry_run_only:
            return dry_run("x.filters.delete", params)
        require(consent, "delete this filter")
        result = await call("x.filters.delete", params, timeout=45.0)
        return changed(
            f"filter {index} on {account_key}",
            before=result.get("previous"),
            after=None,
            remaining=result.get("remaining"),
            note="The definition above is the only copy; nothing else can restore it.",
        )

    @reg.write_tool(title="Run filters now", annotations=MUTATING)
    async def filter_run(
        account_key: str,
        folder_ids: list[str] | None = None,
        filter_indexes: list[int] | None = None,
        confirm: bool = False,
        consent: Gate("run message filters over a folder") = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Apply filters to folders on demand, as "Run Filters on Folder" does.

        Defaults to every enabled filter on the account's Inbox. This really moves,
        tags and deletes mail according to the rules, so it is gated — and it is the
        right way to check a new filter against existing messages.
        """
        guard_write("run filters")
        require(consent, "run these filters")
        params: dict[str, Any] = {"accountKey": account_key}
        if folder_ids:
            params["folderIds"] = folder_ids
        if filter_indexes:
            params["filterIndexes"] = filter_indexes
        result = await call("x.filters.run", params, timeout=600.0)
        return {
            "ran": True,
            "accountKey": account_key,
            "filtersApplied": result.get("filtersApplied") or result.get("filters"),
            "foldersProcessed": result.get("foldersProcessed") or result.get("folders"),
            "messagesMatched": result.get("messagesMatched"),
            "note": result.get("note"),
        }
