"""Saved-search vocabulary, validation, and tools."""

from typing import Any

from ..errors import UsageError
from ..safety import DESTRUCTIVE, IDEMPOTENT_WRITE, MUTATING, Gate, guard_write, require
from ..server import Registrar
from ._common import call, changed, dry_run, page

#: The search-term vocabulary the privileged half accepts. Repeated here so the
#: error message can list it without a round trip.
ATTRIBUTES = (
    "subject",
    "from",
    "to",
    "cc",
    "toOrCc",
    "allAddresses",
    "body",
    "anyText",
    "date",
    "ageInDays",
    "size",
    "priority",
    "status",
    "hasAttachment",
    "tag",
    "junkStatus",
    "junkScore",
    "messageId",
    "otherHeader",
    "folder",
)
OPERATORS = (
    "contains",
    "doesntContain",
    "is",
    "isnt",
    "isEmpty",
    "isntEmpty",
    "beginsWith",
    "endsWith",
    "isBefore",
    "isAfter",
    "isHigherThan",
    "isLowerThan",
    "isGreaterThan",
    "isLessThan",
)


def _check_terms(terms: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Validate terms here so a typo costs no round trip to Thunderbird."""
    if not terms:
        raise UsageError(
            "terms is required — a saved search with no conditions would match "
            "everything. Each term is {attribute, operator, value, matchAll?}."
        )
    for index, term in enumerate(terms):
        if not isinstance(term, dict):
            raise UsageError(f"terms[{index}] must be an object, not {type(term).__name__}.")
        attribute = term.get("attribute")
        operator = term.get("operator")
        if attribute not in ATTRIBUTES:
            raise UsageError(
                f"terms[{index}].attribute must be one of: {', '.join(ATTRIBUTES)}; "
                f"got {attribute!r}."
            )
        if operator not in OPERATORS:
            raise UsageError(
                f"terms[{index}].operator must be one of: {', '.join(OPERATORS)}; got {operator!r}."
            )
        if operator not in ("isEmpty", "isntEmpty") and term.get("value") in (None, ""):
            raise UsageError(f"terms[{index}].value is required for operator {operator!r}.")
    return terms


def register_saved(reg: Registrar) -> None:

    @reg.read_tool(title="List saved searches")
    async def search_saved_list() -> dict[str, Any]:
        """List the saved searches (virtual folders) and what each one matches."""
        result = await call("x.vfolders.list", timeout=45.0)
        return page(
            result.get("savedSearches") or [],
            hint="Pass a saved search's name or uri to the other search_saved_* tools.",
        )

    @reg.write_tool(title="Create a saved search", annotations=MUTATING)
    async def search_saved_create(
        name: str,
        search_folder_ids: list[str],
        terms: list[dict[str, Any]],
        match_all: bool = True,
        online_search: bool = False,
        parent_folder_id: str | None = None,
        confirm: bool = False,
        consent: Gate("create a saved search in Thunderbird") = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Create a saved search that appears in the folder pane.

        `search_folder_ids` are folders to look in (from `folder_list`). Each term is
        `{"attribute": "subject", "operator": "contains", "value": "invoice"}`;
        attributes and operators are listed in the error message if you get one
        wrong. `match_all=false` makes the terms OR together. `online_search` asks
        the IMAP server to run the search instead of using the local database.

        Nothing is copied or moved — a saved search is a stored query.
        """
        guard_write("create saved searches")
        if not search_folder_ids:
            raise UsageError(
                "search_folder_ids is required — name at least one folder to search "
                "(folder_list gives the ids)."
            )
        checked = _check_terms(terms)
        for term in checked:
            term.setdefault("matchAll", match_all)
        params = {
            "name": name,
            "searchFolderIds": search_folder_ids,
            "terms": checked,
            "onlineSearch": online_search,
            "parentFolderId": parent_folder_id,
        }
        if dry_run_only:
            return dry_run("x.vfolders.create", params)
        require(consent, "create this saved search")
        result = await call("x.vfolders.create", params, timeout=60.0)
        return changed(
            f"saved search {name}",
            before=None,
            after=result.get("savedSearch"),
        )

    @reg.write_tool(title="Change a saved search", annotations=IDEMPOTENT_WRITE)
    async def search_saved_update(
        saved_search: str,
        search_folder_ids: list[str] | None = None,
        terms: list[dict[str, Any]] | None = None,
        online_search: bool | None = None,
        confirm: bool = False,
        consent: Gate("change a saved search") = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Redefine an existing saved search, by name or uri.

        Only what you pass is replaced; `terms` replaces the whole condition list
        rather than merging, because a partial merge has no sensible meaning for a
        boolean query.
        """
        guard_write("change saved searches")
        params: dict[str, Any] = {"savedSearch": saved_search}
        if search_folder_ids is not None:
            if not search_folder_ids:
                raise UsageError("search_folder_ids cannot be empty; omit it to leave it alone.")
            params["searchFolderIds"] = search_folder_ids
        if terms is not None:
            params["terms"] = _check_terms(terms)
        if online_search is not None:
            params["onlineSearch"] = online_search
        if len(params) == 1:
            raise UsageError("Nothing to change — pass search_folder_ids, terms or online_search.")
        if dry_run_only:
            return dry_run("x.vfolders.update", params)
        require(consent, "change this saved search")
        result = await call("x.vfolders.update", params, timeout=60.0)
        return changed(
            f"saved search {saved_search}",
            before=result.get("previous"),
            after=result.get("current"),
        )

    @reg.write_tool(title="Delete a saved search", annotations=DESTRUCTIVE)
    async def search_saved_delete(
        saved_search: str,
        confirm: bool = False,
        consent: Gate("delete a saved search") = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Remove a saved search. The messages it listed are not touched.

        A saved search only stores a query, so deleting one loses the query and
        nothing else — but the query itself is not recoverable, hence the prompt.
        """
        guard_write("delete saved searches")
        require(consent, "delete this saved search")
        result = await call("x.vfolders.delete", {"savedSearch": saved_search}, timeout=60.0)
        return changed(
            f"saved search {saved_search}",
            before=result.get("previous"),
            after=None,
            messagesAffected=result.get("messagesAffected", 0),
        )
