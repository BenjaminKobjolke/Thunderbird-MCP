"""Attachment, triage, and tag operations for the mail toolset."""

# NOTE: no `from __future__ import annotations` in toolset modules. Tool signatures
# must evaluate at definition time so `Gate(...)` produces a real
# `Annotated[Consent, Resolve(...)]` rather than a string the SDK has to re-evaluate.

from typing import Any

from ..errors import UsageError
from ..policy import grants
from ..safety import DESTRUCTIVE, IDEMPOTENT_WRITE, MUTATING, Gate, guard_write, require
from ..server import Registrar
from ._common import call, changed, dry_run, page, require_ids


def register_actions(reg: Registrar) -> None:
    @reg.read_tool(title="List attachments")
    async def mail_attachments(message_id: int) -> dict[str, Any]:
        """List a message's attachments with part names, sizes and content types."""
        result = await call("messages.listAttachments", {"messageId": message_id})
        return page(result.get("attachments", []), messageId=message_id)

    @reg.write_tool(title="Save an attachment", annotations=MUTATING, interactive=False)
    async def mail_save_attachment(
        message_id: int,
        part_name: str,
        directory: str,
        filename: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Write one attachment to a directory on this machine.

        `part_name` comes from `mail_attachments`. Refuses to clobber an existing
        file unless `overwrite=true`.
        """
        guard_write("save attachments")
        result = await call(
            "messages.saveAttachment",
            {
                "messageId": message_id,
                "partName": part_name,
                "directory": directory,
                "filename": filename,
                "overwrite": overwrite,
            },
            timeout=120.0,
        )
        return {"saved": True, **result}

    # ------------------------------------------------------------------ triaging

    @reg.write_tool(title="Mark messages", annotations=IDEMPOTENT_WRITE, interactive=False)
    async def mail_mark(
        message_ids: list[int],
        read: bool | None = None,
        flagged: bool | None = None,
        junk: bool | None = None,
        add_tags: list[str] | None = None,
        remove_tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Set read/flagged/junk state or adjust tags on one or more messages.

        Cheap and reversible, so no confirmation is required. Tag keys come from
        `mail_tags`.
        The reply reports each updated message's earlier flags and tags for undo.
        """
        guard_write("change message flags")
        ids = require_ids(message_ids)
        if all(v is None for v in (read, flagged, junk)) and not add_tags and not remove_tags:
            raise UsageError(
                "Nothing to change — set read, flagged, junk, add_tags or remove_tags."
            )
        result = await call(
            "messages.mark",
            {
                "messageIds": ids,
                "read": read,
                "flagged": flagged,
                "junk": junk,
                "addTags": add_tags or [],
                "removeTags": remove_tags or [],
            },
            timeout=90.0,
        )
        return changed(
            "messages.mark",
            before={"messages": result.get("previous") or []},
            after={
                **{
                    key: value
                    for key, value in (("read", read), ("flagged", flagged), ("junk", junk))
                    if value is not None
                },
                "addTags": add_tags or [],
                "removeTags": remove_tags or [],
            },
            updated=result.get("updated", len(ids)),
            failures=result.get("failures") or [],
        )

    @reg.write_tool(
        title="Move messages",
        annotations=MUTATING,
        interactive=not grants(reg.settings.folder_rules, "move_in", "move_out"),
    )
    async def mail_move(
        message_ids: list[int],
        destination_folder_id: str,
        confirm: bool = False,
        consent: Gate("move these messages to another folder", move_policy=True) = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Move messages into another folder.

        On IMAP the move is asynchronous — the tool waits for Thunderbird to confirm
        before returning, so a following search reflects the change.
        Moves allowed in the tbmcp config skip confirmation.
        The reply reports each message's source and any observed landing folder for undo.
        """
        guard_write("move messages")
        ids = require_ids(message_ids)
        if dry_run_only:
            return dry_run(
                "messages.move", {"messageIds": ids, "destinationFolderId": destination_folder_id}
            )
        require(consent, "move these messages")
        result = await call(
            "messages.move",
            {"messageIds": ids, "destinationFolderId": destination_folder_id},
            timeout=180.0,
        )
        return changed(
            "messages.move",
            before={
                "messageIds": ids,
                "folderIds": result.get("sourceFolderIds"),
                "messages": result.get("previous") or [],
            },
            after={"folderId": destination_folder_id, "messages": result.get("landed") or []},
            moved=result.get("moved", len(ids)),
            note="Message ids change after a move; re-query to get the new ones.",
        )

    @reg.write_tool(title="Copy messages", annotations=MUTATING)
    async def mail_copy(
        message_ids: list[int],
        destination_folder_id: str,
        confirm: bool = False,
        consent: Gate("copy these messages to another folder") = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Copy messages into another folder, leaving the originals in place.

        The reply reports each source and any observed copy location for undo.
        """
        guard_write("copy messages")
        ids = require_ids(message_ids)
        require(consent, "copy these messages")
        result = await call(
            "messages.copy",
            {"messageIds": ids, "destinationFolderId": destination_folder_id},
            timeout=180.0,
        )
        return changed(
            "messages.copy",
            before={"messages": result.get("previous") or []},
            after={"folderId": destination_folder_id, "messages": result.get("landed") or []},
            copied=result.get("copied", len(ids)),
            destinationFolderId=destination_folder_id,
        )

    @reg.write_tool(title="Archive messages", annotations=MUTATING)
    async def mail_archive(
        message_ids: list[int],
        confirm: bool = False,
        consent: Gate("archive these messages") = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Archive messages using each account's configured archive layout.

        The reply reports each source and any observed archive location for undo.
        """
        guard_write("archive messages")
        ids = require_ids(message_ids)
        require(consent, "archive these messages")
        result = await call("messages.archive", {"messageIds": ids}, timeout=180.0)
        return changed(
            "messages.archive",
            before={"messages": result.get("previous") or []},
            after={"messages": result.get("landed") or []},
            archived=result.get("archived", len(ids)),
        )

    @reg.write_tool(title="Delete messages", annotations=DESTRUCTIVE)
    async def mail_delete(
        message_ids: list[int],
        permanent: bool = False,
        confirm: bool = False,
        consent: Gate("delete these messages") = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Delete messages. Moves to Trash unless `permanent=true`.

        A permanent delete cannot be undone from Thunderbird, so prefer the default
        and let the user empty Trash themselves.
        """
        guard_write("delete messages")
        ids = require_ids(message_ids)
        if dry_run_only:
            return dry_run("messages.delete", {"messageIds": ids, "permanent": permanent})
        require(consent, "delete these messages")
        result = await call(
            "messages.delete", {"messageIds": ids, "permanent": permanent}, timeout=180.0
        )
        return changed(
            "messages.delete",
            before={"messageIds": ids},
            after={"deleted": result.get("deleted", len(ids)), "permanent": permanent},
            recoverable=not permanent,
        )

    @reg.read_tool(title="List message tags")
    async def mail_tags() -> dict[str, Any]:
        """List the tags defined in Thunderbird, with keys, labels and colours."""
        result = await call("tags.list")
        return page(result.get("tags", []))

    @reg.write_tool(title="Create or update a tag", annotations=IDEMPOTENT_WRITE)
    async def mail_tag_upsert(
        label: str,
        key: str | None = None,
        color: str | None = None,
        confirm: bool = False,
        consent: Gate("add or change a message tag") = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Create a tag, or recolour/rename an existing one.

        `color` is `#RRGGBB`. Omit `key` to create a new tag; pass an existing key to
        update it.
        """
        guard_write("change tags")
        require(consent, "change message tags")
        result = await call(
            "tags.upsert", {"key": key, "label": label, "color": color}, timeout=30.0
        )
        return changed("tags", before=result.get("previous"), after=result.get("current"))

    @reg.write_tool(title="Delete a tag", annotations=DESTRUCTIVE)
    async def mail_tag_delete(
        key: str,
        confirm: bool = False,
        consent: Gate("delete a message tag") = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Remove a tag definition. Messages keep the raw keyword but lose the label."""
        guard_write("delete tags")
        require(consent, "delete this tag")
        result = await call("tags.delete", {"key": key}, timeout=30.0)
        return changed("tags", before=result.get("previous"), after=None)
