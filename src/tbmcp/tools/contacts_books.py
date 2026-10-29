"""Address-book and mailing-list tools."""

from typing import Any

from ..errors import UsageError
from ..safety import DESTRUCTIVE, IDEMPOTENT_WRITE, MUTATING, Gate, guard_write, require
from ..server import Registrar
from ._common import call, changed, dry_run, page


def register_books(reg: Registrar) -> None:
    # -------------------------------------------------------------- address books

    @reg.read_tool(title="List address books")
    async def addressbook_list(include_counts: bool = True) -> dict[str, Any]:
        """List the address books, with how many contacts and lists each holds.

        `readOnly` books reject writes; `remote` ones are LDAP or CardDAV. Turn
        `include_counts` off if a remote book makes this slow.
        """
        result = await call("addressbooks.list", {"includeCounts": include_counts}, timeout=60.0)
        return page(result.get("books", []))

    @reg.write_tool(title="Create an address book", annotations=MUTATING)
    async def addressbook_create(
        name: str,
        confirm: bool = False,
        consent: Gate("create a new address book") = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Create an empty local address book."""
        guard_write("create address books")
        if not name.strip():
            raise UsageError("name is required — give the address book a title.")
        require(consent, "create this address book")
        result = await call("addressbooks.create", {"name": name}, timeout=30.0)
        return changed("addressbooks", before=None, after=result.get("book"))

    @reg.write_tool(title="Delete an address book", annotations=DESTRUCTIVE)
    async def addressbook_delete(
        address_book_id: str,
        confirm: bool = False,
        consent: Gate("delete this address book and its contacts") = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Delete an address book together with all its contacts and mailing lists.

        Irreversible and usually not what the user meant — deleting one contact is
        `contact_delete`. Run with `dry_run_only=true` first to see the size of it.
        """
        guard_write("delete address books")
        if dry_run_only:
            return dry_run("addressbooks.delete", {"id": address_book_id})
        require(consent, "delete this address book")
        result = await call("addressbooks.delete", {"id": address_book_id}, timeout=60.0)
        return changed("addressbooks", before=result.get("previous"), after=None, recoverable=False)

    # -------------------------------------------------------------- mailing lists

    @reg.read_tool(title="List mailing lists")
    async def mailinglist_list(
        address_book_id: str | None = None,
        include_members: bool = False,
    ) -> dict[str, Any]:
        """List address book mailing lists, with member counts.

        These are Thunderbird's own distribution lists, not mailing lists you
        subscribe to. `include_members` returns each roster in full.
        """
        result = await call(
            "mailinglists.list",
            {"addressBookId": address_book_id, "includeMembers": include_members},
            timeout=60.0,
        )
        return page(result.get("lists", []), addressBookId=address_book_id)

    @reg.write_tool(title="Create a mailing list", annotations=MUTATING)
    async def mailinglist_create(
        address_book_id: str,
        name: str,
        nickname: str | None = None,
        description: str | None = None,
        confirm: bool = False,
        consent: Gate("create a mailing list") = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Create an empty mailing list in an address book.

        `nickname` is what the user types in a compose window to address the whole
        list, so it is worth setting.
        """
        guard_write("create mailing lists")
        if not name.strip():
            raise UsageError("name is required — give the mailing list a title.")
        require(consent, "create this mailing list")
        result = await call(
            "mailinglists.create",
            {
                "addressBookId": address_book_id,
                "name": name,
                "nickName": nickname,
                "description": description,
            },
            timeout=30.0,
        )
        return changed("mailinglists", before=None, after=result.get("list"))

    @reg.write_tool(title="Add someone to a mailing list", annotations=IDEMPOTENT_WRITE)
    async def mailinglist_add_member(
        mailing_list_id: str,
        contact_id: str,
        confirm: bool = False,
        consent: Gate("add a contact to a mailing list") = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Add an existing contact to a mailing list.

        If the contact lives in a different address book it is copied into the list's
        one; the result says so via `copiedToAddressBook`.
        """
        guard_write("change mailing list membership")
        require(consent, "add this contact to the mailing list")
        result = await call(
            "mailinglists.addMember",
            {"id": mailing_list_id, "contactId": contact_id},
            timeout=30.0,
        )
        return {
            "added": True,
            "copiedToAddressBook": result.get("copiedToAddressBook", False),
            "list": result.get("list"),
            "contact": result.get("contact"),
        }

    @reg.write_tool(title="Remove someone from a mailing list", annotations=IDEMPOTENT_WRITE)
    async def mailinglist_remove_member(
        mailing_list_id: str,
        contact_id: str,
        confirm: bool = False,
        consent: Gate("remove a contact from a mailing list") = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Take a contact off a mailing list. The contact itself is left alone."""
        guard_write("change mailing list membership")
        require(consent, "remove this contact from the mailing list")
        result = await call(
            "mailinglists.removeMember",
            {"id": mailing_list_id, "contactId": contact_id},
            timeout=30.0,
        )
        return {
            "removed": True,
            "list": result.get("list"),
            "contact": result.get("contact"),
        }
