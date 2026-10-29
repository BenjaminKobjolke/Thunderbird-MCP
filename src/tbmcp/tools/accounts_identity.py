"""Identity listing and update tools."""

from typing import Any

from ..errors import UsageError
from ..safety import IDEMPOTENT_WRITE, Gate, guard_write, require
from ..server import Registrar
from ._common import call, dry_run, page
from .accounts_common import _applied, _changes, _rows


def register(reg: Registrar) -> None:
    @reg.read_tool(title="List identities")
    async def identity_list(account_key: str | None = None) -> dict[str, Any]:
        """List the sending identities, across every account or just one.

        An identity is a from-address with its own signature, outgoing server and
        filing folders. Its `key` is what `identity_get` and `identity_set` take.
        """
        result = await call("x.accounts.identities", {"accountKey": account_key}, timeout=30.0)
        return page(_rows(result, "identities", "items"), accountKey=account_key)

    @reg.read_tool(title="Get an identity")
    async def identity_get(identity_key: str) -> dict[str, Any]:
        """Read one identity in full: addresses, signature, outgoing server, filing folders.

        Credentials are not part of this: the outgoing server is reported by key, and
        its password stays in the login manager.
        """
        result = await call("x.identities.get", {"identityKey": identity_key}, timeout=30.0)
        return {"identityKey": identity_key, **(result or {})}

    @reg.write_tool(title="Update an identity", annotations=IDEMPOTENT_WRITE)
    async def identity_set(
        identity_key: str,
        full_name: str | None = None,
        email: str | None = None,
        reply_to: str | None = None,
        organization: str | None = None,
        compose_html: bool | None = None,
        attach_vcard: bool | None = None,
        smtp_server_key: str | None = None,
        do_bcc: bool | None = None,
        bcc_list: str | None = None,
        catch_all: bool | None = None,
        confirm: bool = False,
        consent: Gate("change one of the user's sending identities") = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Change an identity's addresses and composition defaults.

        Only the fields you pass are touched; pass an empty string to clear one.
        `email` is the from-address the recipient sees, so changing it can break
        replies and any filter that matches on it. `smtp_server_key` comes from
        `smtp_list`. Signatures go through `identity_set_signature`, and the sent or
        drafts folders through `account_set_folders`. Passwords are never written.
        """
        guard_write("change identities")
        fields = _changes(
            fullName=full_name,
            email=email,
            replyTo=reply_to,
            organization=organization,
            composeHtml=compose_html,
            attachVCard=attach_vcard,
            smtpServerKey=smtp_server_key,
            doBcc=do_bcc,
            doBccList=bcc_list,
            catchAll=catch_all,
        )
        if not fields:
            raise UsageError(
                "Nothing to change — pass at least one of full_name, email, reply_to, "
                "organization, compose_html, attach_vcard, smtp_server_key, do_bcc, "
                "bcc_list or catch_all."
            )
        params = {"identityKey": identity_key, **fields}
        if dry_run_only:
            return dry_run("x.identities.set", params)
        require(consent, "change this identity")
        result = await call("x.identities.set", params, timeout=60.0)
        return _applied(f"identity {identity_key}", result, identityKey=identity_key)

    @reg.write_tool(title="Set an identity's signature", annotations=IDEMPOTENT_WRITE)
    async def identity_set_signature(
        identity_key: str,
        signature: str | None = None,
        is_html: bool = False,
        file_path: str | None = None,
        attach: bool = True,
        below_quote: bool = True,
        confirm: bool = False,
        consent: Gate("replace an identity's signature") = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Replace an identity's signature text, or point it at a file.

        Pass `signature` for inline text (set `is_html` when it contains markup), or
        `file_path` for a signature file on this machine — the two are mutually
        exclusive in Thunderbird. `attach=false` keeps the text but stops appending
        it; `below_quote` controls whether the signature sits under a quoted reply.
        """
        guard_write("change signatures")
        if signature is not None and file_path:
            raise UsageError(
                "Pass either signature (inline text) or file_path, not both — "
                "Thunderbird stores one or the other."
            )
        if signature is None and not file_path and attach:
            raise UsageError(
                "Nothing to set — pass signature, or file_path, or attach=false to "
                "stop appending the existing signature."
            )
        params = {
            "identityKey": identity_key,
            "signature": signature,
            "isHtml": is_html,
            "filePath": file_path,
            "attach": attach,
            "belowQuote": below_quote,
        }
        if dry_run_only:
            return dry_run("x.identities.setSignature", params)
        require(consent, "replace this signature")
        result = await call("x.identities.setSignature", params, timeout=60.0)
        return _applied(f"identity {identity_key} signature", result, identityKey=identity_key)
