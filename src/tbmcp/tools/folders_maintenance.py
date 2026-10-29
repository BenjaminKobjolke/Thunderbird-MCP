"""Folder deletion, triage, emptying and maintenance tools."""

from collections.abc import Callable
from typing import Any

from ..errors import UsageError
from ..policy import grants
from ..safety import (
    DESTRUCTIVE,
    IDEMPOTENT_WRITE,
    MUTATING,
    FolderPolicy,
    Gate,
    guard_write,
    require,
)
from ..server import Registrar
from ._common import call, changed, dry_run


def register_maintenance(reg: Registrar, _slim: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
    @reg.write_tool(
        title="Delete a folder",
        annotations=DESTRUCTIVE,
        interactive=not grants(reg.settings.folder_rules, "delete_subfolders"),
    )
    async def folder_delete(
        folder_id: str,
        confirm: bool = False,
        consent: Gate(
            "delete a folder and everything in it",
            folder_policy=FolderPolicy("delete_subfolders", strictly_below=True, need_empty=True),
        ) = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Delete a folder, its subfolders and every message in them.

        Thunderbird moves the folder to Trash unless it is already inside Trash, in
        which case it goes for good. Check `folder_get` first if the count matters —
        the reply reports what was removed, but cannot put it back.
        Empty subfolders allowed in the tbmcp config skip confirmation.
        """
        guard_write("delete folders")
        if dry_run_only:
            return dry_run("folders.delete", {"folderId": folder_id})
        require(consent, "delete this folder")
        result = await call("folders.delete", {"folderId": folder_id}, timeout=300.0)
        permanent = bool(result.get("permanent"))
        return changed(
            "folders.delete",
            before=_slim(result.get("deleted") or {}),
            after=None,
            subFolderCount=result.get("subFolderCount"),
            permanent=permanent,
            recoverable=not permanent,
        )

    # -------------------------------------------------------------------- triaging

    @reg.write_tool(title="Mark a folder read", annotations=IDEMPOTENT_WRITE)
    async def folder_mark_read(
        folder_id: str,
        include_subfolders: bool = False,
        confirm: bool = False,
        consent: Gate("mark a whole folder as read") = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Mark every message in a folder as read.

        There is no per-message undo for this, which is why it asks first. On IMAP
        the flags are pushed to the server.
        """
        guard_write("mark folders as read")
        require(consent, "mark this folder as read")
        result = await call(
            "folders.markAsRead",
            {"folderId": folder_id, "includeSubFolders": include_subfolders},
            timeout=180.0,
        )
        return changed(
            "folders.markAsRead",
            before={"unreadMessageCount": result.get("previousUnread")},
            after={"unreadMessageCount": result.get("unread")},
            folderId=folder_id,
            foldersAffected=result.get("folders"),
            failures=result.get("failures") or [],
        )

    @reg.write_tool(title="Favourite a folder", annotations=IDEMPOTENT_WRITE, interactive=False)
    async def folder_set_favorite(folder_id: str, favorite: bool = True) -> dict[str, Any]:
        """Add or remove a folder from the user's favourites.

        Cosmetic and reversible — it only affects the folder pane's Favourites view,
        so it is not gated. Prompting for something this harmless would only train
        the user to click through the prompts that do matter.
        """
        guard_write("change folder favourites")
        result = await call(
            "folders.update", {"folderId": folder_id, "isFavorite": favorite}, timeout=30.0
        )
        return changed(
            "folders.update",
            before=_slim(result.get("previous") or {}),
            after=_slim(result.get("folder") or {}),
        )

    # ---------------------------------------------------------------- emptying out

    @reg.write_tool(title="Empty Trash", annotations=DESTRUCTIVE)
    async def folder_empty_trash(
        account_id: str | None = None,
        folder_id: str | None = None,
        remove_subfolders: bool = True,
        confirm: bool = False,
        consent: Gate("permanently delete everything in Trash") = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Permanently delete everything in one account's Trash.

        This is not recoverable from Thunderbird. Give `account_id` and the account's
        configured Trash is used; give `folder_id` to empty a specific one. Subfolders
        of Trash are removed too unless `remove_subfolders=false`.
        """
        guard_write("empty Trash")
        if not account_id and not folder_id:
            raise UsageError(
                "Give account_id (its configured Trash) or folder_id (a specific Trash "
                "folder) — emptying every account's Trash at once is not offered."
            )
        target = {
            "accountId": account_id,
            "folderId": folder_id,
            "removeSubFolders": remove_subfolders,
        }
        if dry_run_only:
            return dry_run("folders.emptyTrash", target)
        require(consent, "permanently delete everything in Trash")
        result = await call("folders.emptyTrash", target, timeout=600.0)
        return changed(
            "folders.emptyTrash",
            before={"path": result.get("path"), "messages": result.get("deleted")},
            after={"messages": 0},
            deleted=result.get("deleted"),
            foldersRemoved=result.get("foldersRemoved"),
            recoverable=False,
        )

    @reg.write_tool(title="Empty Junk", annotations=DESTRUCTIVE)
    async def folder_empty_junk(
        account_id: str | None = None,
        folder_id: str | None = None,
        confirm: bool = False,
        consent: Gate("permanently delete everything in Junk") = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Permanently delete everything in one account's Junk folder.

        Subfolders of Junk are emptied but kept, since they are usually filter
        targets the user set up deliberately. Not recoverable.
        """
        guard_write("empty Junk")
        if not account_id and not folder_id:
            raise UsageError(
                "Give account_id (its configured Junk folder) or folder_id (a specific one)."
            )
        target = {"accountId": account_id, "folderId": folder_id}
        if dry_run_only:
            return dry_run("folders.emptyJunk", target)
        require(consent, "permanently delete everything in Junk")
        result = await call("folders.emptyJunk", target, timeout=600.0)
        return changed(
            "folders.emptyJunk",
            before={"path": result.get("path"), "messages": result.get("deleted")},
            after={"messages": 0},
            deleted=result.get("deleted"),
            subFoldersEmptied=result.get("subFoldersEmptied"),
            recoverable=False,
        )

    # ---------------------------------------------------------------- housekeeping

    @reg.write_tool(title="Download a folder for offline use", annotations=MUTATING)
    async def folder_sync_offline(
        folder_id: str,
        include_subfolders: bool = False,
        confirm: bool = False,
        consent: Gate("download a folder for offline use") = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Fetch an IMAP folder's message bodies so they are available offline.

        This is what `mail_get_source` needs before it can return raw source for an
        IMAP message. A large folder means a long download and real network traffic.
        """
        guard_write("download folders for offline use")
        require(consent, "download this folder for offline use")
        result = await call(
            "x.admin.downloadForOffline",
            {"folderId": folder_id, "includeSubFolders": include_subfolders},
            timeout=600.0,
        )
        return {"folderId": folder_id, "started": True, **(result or {})}

    @reg.write_tool(title="Compact folders", annotations=MUTATING)
    async def folder_compact(
        folder_id: str | None = None,
        account_id: str | None = None,
        confirm: bool = False,
        consent: Gate("compact folders and rewrite their message stores") = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Reclaim the disk space left behind by deleted messages.

        Deleted mail stays in the message store until the folder is compacted. Pass
        `folder_id` for one folder or `account_id` for all of an account's. Nothing
        readable is lost, but the store is rewritten, so do not interrupt it.
        """
        guard_write("compact folders")
        if not folder_id and not account_id:
            raise UsageError("Give folder_id for one folder, or account_id for a whole account.")
        require(consent, "compact these folders")
        result = await call(
            "x.admin.compactFolders",
            {"folderId": folder_id, "accountId": account_id},
            timeout=600.0,
        )
        return {"compacted": True, **(result or {})}
