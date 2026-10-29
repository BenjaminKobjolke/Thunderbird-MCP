"""The folder tree: browsing it, reshaping it, and emptying it out.

`folder_list` is the entry point for most other tools — folder ids are opaque and
only valid while Thunderbird stays open, so anything that needs one comes here
first. Its output is deliberately terse for that reason.
"""

# NOTE: no `from __future__ import annotations` in toolset modules. Tool signatures
# must evaluate at definition time so `Gate(...)` produces a real
# `Annotated[Consent, Resolve(...)]` rather than a string the SDK has to re-evaluate.

from typing import Any, Literal

from ..errors import UsageError
from ..policy import grants
from ..safety import (
    MUTATING,
    FolderPolicy,
    Gate,
    guard_write,
    require,
)
from ..server import Registrar
from ._common import call, changed, clamp, dry_run, page
from .folders_maintenance import register_maintenance

SpecialUse = Literal["inbox", "drafts", "sent", "trash", "templates", "archives", "junk"]

#: Depth 1 is account roots plus their top-level folders — enough to orient a model
#: without paying for a 400-folder IMAP tree it did not ask for.
DEFAULT_DEPTH = 1
MAX_DEPTH = 20


def _slim(folder: dict[str, Any]) -> dict[str, Any]:
    """Drop the fields that carry no information, and recurse into `children`.

    `folder_list` runs on nearly every conversation, so a key that is always null
    or always empty is a key not worth spending the model's context on.
    """
    out = {
        key: value
        for key, value in folder.items()
        if key != "children" and value is not None and value != [] and value != ""
    }
    children = folder.get("children")
    if children:
        out["children"] = [_slim(child) for child in children]
    return out


def register(reg: Registrar) -> None:
    # ------------------------------------------------------------------- browsing

    @reg.read_tool(title="List folders")
    async def folder_list(
        account_id: str | None = None,
        name: str | None = None,
        parent_id: str | None = None,
        special_use: SpecialUse | None = None,
        is_virtual: bool | None = None,
        is_tag: bool | None = None,
        is_unified: bool | None = None,
        is_favorite: bool | None = None,
        is_root: bool | None = None,
        has_unread_messages: bool | None = None,
        depth: int | None = None,
        tree: bool = False,
        include_counts: bool = True,
        limit: int = 200,
    ) -> dict[str, Any]:
        """List mail folders with their ids and message counts.

        With no filters this browses: every account's root folder plus one level
        below it. Raise `depth` to walk further, or give a filter — `name` is a
        case-insensitive substring — and the whole tree is searched instead. Pass
        `tree=true` to get folders nested under `children` rather than flat.

        Counts come from Thunderbird's own folder database. On IMAP that database
        can lag until the folder has been selected once in Thunderbird, so treat
        unread and total counts as close rather than exact.
        """
        query: dict[str, Any] = {}
        if account_id:
            query["accountId"] = account_id
        if name:
            query["name"] = name
        if parent_id:
            query["folderId"] = parent_id
            query["recursive"] = True
        if special_use:
            query["specialUse"] = [special_use]
        for key, value in (
            ("isVirtual", is_virtual),
            ("isTag", is_tag),
            ("isUnified", is_unified),
            ("isFavorite", is_favorite),
            ("isRoot", is_root),
            ("hasUnreadMessages", has_unread_messages),
        ):
            if value is not None:
                query[key] = value

        if depth is not None:
            depth = clamp(depth, default=DEFAULT_DEPTH, minimum=0, maximum=MAX_DEPTH, field="depth")
        result = await call(
            "folders.query",
            {
                "query": query,
                "depth": depth,
                "tree": tree,
                "includeCounts": include_counts,
                "limit": clamp(limit, default=200, minimum=1, maximum=500, field="limit"),
            },
            timeout=90.0,
        )
        return page(
            [_slim(folder) for folder in result.get("folders", [])],
            truncated=bool(result.get("truncated")),
            searchedWholeTree=result.get("searched"),
        )

    @reg.read_tool(title="Get a folder")
    async def folder_get(folder_id: str, include_subfolders: bool = False) -> dict[str, Any]:
        """Get one folder: counts, special use, flags and IMAP quota.

        Use this to re-check a count after a move or a delete; `folder_list` is the
        cheaper way to find the id in the first place.
        """
        result = await call(
            "folders.get",
            {"folderId": folder_id, "includeSubFolders": include_subfolders},
            timeout=60.0,
        )
        return _slim(result.get("folder") or {})

    @reg.read_tool(title="Get folder capabilities")
    async def folder_capabilities(folder_id: str) -> dict[str, Any]:
        """Report what may be done to a folder before attempting it.

        Answers whether the folder can hold messages, take subfolders, be renamed,
        be deleted, or have messages deleted from it. Worth a call before offering
        the user a plan that a server would refuse.
        """
        result = await call("folders.capabilities", {"folderId": folder_id}, timeout=30.0)
        return {"folderId": folder_id, **(result.get("capabilities") or {})}

    @reg.read_tool(title="Get a unified folder")
    async def folder_get_unified(
        folder_type: SpecialUse, include_subfolders: bool = False
    ) -> dict[str, Any]:
        """Get the unified folder that spans every account, e.g. all inboxes at once.

        Its id works with `mail_list`. For one call that finds and lists all inboxes,
        use `mail_list(special_use="inbox")`.
        """
        result = await call(
            "folders.getUnified",
            {"type": folder_type, "includeSubFolders": include_subfolders},
            timeout=60.0,
        )
        return _slim(result.get("folder") or {})

    # ------------------------------------------------------------------- reshaping

    @reg.write_tool(
        title="Create a folder",
        annotations=MUTATING,
        interactive=not grants(reg.settings.folder_rules, "create_subfolders"),
    )
    async def folder_create(
        name: str,
        parent_id: str | None = None,
        account_id: str | None = None,
        confirm: bool = False,
        consent: Gate(
            "create a folder", folder_policy=FolderPolicy("create_subfolders", on_parent=True)
        ) = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Create a folder inside another folder, or at the top of an account.

        Folders allowed in the tbmcp config skip confirmation.
        """
        guard_write("create folders")
        if not parent_id and not account_id:
            raise UsageError(
                "Give parent_id to nest the folder inside another, or account_id to "
                "create it at the top level of an account."
            )
        require(consent, "create this folder")
        result = await call(
            "folders.create",
            {"name": name, "parentId": parent_id, "accountId": account_id},
            timeout=60.0,
        )
        return changed("folders.create", before=None, after=_slim(result.get("folder") or {}))

    @reg.write_tool(
        title="Rename a folder",
        annotations=MUTATING,
        interactive=not grants(reg.settings.folder_rules, "rename_subfolders"),
    )
    async def folder_rename(
        folder_id: str,
        new_name: str,
        confirm: bool = False,
        consent: Gate(
            "rename a folder", folder_policy=FolderPolicy("rename_subfolders", strictly_below=True)
        ) = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Rename a folder, keeping its messages and subfolders.

        Folders allowed in the tbmcp config skip confirmation.
        """
        guard_write("rename folders")
        require(consent, "rename this folder")
        result = await call(
            "folders.rename", {"folderId": folder_id, "newName": new_name}, timeout=120.0
        )
        return changed(
            "folders.rename",
            before=_slim(result.get("previous") or {}),
            after=_slim(result.get("folder") or {}),
            note="A folder's id encodes its path, so it changes on rename. Use the new id.",
        )

    @reg.write_tool(title="Move a folder", annotations=MUTATING)
    async def folder_move(
        folder_id: str,
        destination_id: str,
        confirm: bool = False,
        consent: Gate("move a folder and everything in it") = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Move a folder under a different parent, with its subfolders.

        `destination_id` is the new parent folder — use an account's root folder to
        move it to the top level. Across accounts this copies then deletes, which on
        IMAP can take a while.
        """
        guard_write("move folders")
        if dry_run_only:
            return dry_run("folders.move", {"folderId": folder_id, "destinationId": destination_id})
        require(consent, "move this folder")
        result = await call(
            "folders.move",
            {"folderId": folder_id, "destinationId": destination_id},
            timeout=300.0,
        )
        return changed(
            "folders.move",
            before=_slim(result.get("previous") or {}),
            after=_slim(result.get("folder") or {}),
            note="Folder and message ids below the moved folder all change; re-query.",
        )

    @reg.write_tool(title="Copy a folder", annotations=MUTATING)
    async def folder_copy(
        folder_id: str,
        destination_id: str,
        confirm: bool = False,
        consent: Gate("copy a folder and everything in it") = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Copy a folder and its contents under another parent, leaving the original."""
        guard_write("copy folders")
        require(consent, "copy this folder")
        result = await call(
            "folders.copy",
            {"folderId": folder_id, "destinationId": destination_id},
            timeout=300.0,
        )
        return {
            "copied": True,
            "sourceFolderId": result.get("sourceFolderId", folder_id),
            "folder": _slim(result.get("folder") or {}),
        }

    register_maintenance(reg, _slim)
