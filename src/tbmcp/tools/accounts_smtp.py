"""Outgoing mail server tools."""

from typing import Any

from ..errors import UsageError
from ..safety import DESTRUCTIVE, IDEMPOTENT_WRITE, MUTATING, Gate, guard_write, require
from ..server import Registrar
from ._common import call, changed, dry_run, page
from .accounts_common import AuthMethod, SocketType, _applied, _changes, _rows


def register(reg: Registrar) -> None:
    @reg.read_tool(title="List outgoing servers")
    async def smtp_list() -> dict[str, Any]:
        """List the SMTP servers, and which one is the default.

        Each server's `key` is what the other `smtp_*` tools take. Passwords are
        never read — only whether Thunderbird has one stored.
        """
        result = await call("x.smtp.list", timeout=30.0)
        default_key = None
        if isinstance(result, dict):
            default_key = result.get("defaultServerKey") or result.get("defaultKey")
        return page(_rows(result, "servers", "items"), defaultServerKey=default_key)

    @reg.write_tool(title="Add an outgoing server", annotations=MUTATING)
    async def smtp_create(
        hostname: str,
        port: int | None = None,
        username: str | None = None,
        socket_type: SocketType = "starttls",
        auth_method: AuthMethod = "password-cleartext",
        description: str | None = None,
        make_default: bool = False,
        confirm: bool = False,
        consent: Gate("add an outgoing mail server") = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Add an SMTP server. Nothing sends through it until an identity points at it.

        Omit `port` to take the default for the chosen security (587 for STARTTLS,
        465 for TLS). No password is stored: Thunderbird prompts the user the first
        time the server is used, and this bridge never writes to the login manager.
        """
        guard_write("add outgoing servers")
        params = {
            "hostname": hostname,
            "port": port,
            "username": username,
            "socketType": socket_type,
            "authMethod": auth_method,
            "description": description,
            "makeDefault": make_default,
        }
        if dry_run_only:
            return dry_run("x.smtp.create", params)
        require(consent, "add this outgoing server")
        result = await call("x.smtp.create", params, timeout=60.0)
        created = result.get("server") if isinstance(result, dict) else None
        return changed(
            "smtp server",
            before=None,
            after=created or result,
            note="Point an identity at it with identity_set(smtp_server_key=...).",
        )

    @reg.write_tool(title="Update an outgoing server", annotations=IDEMPOTENT_WRITE)
    async def smtp_update(
        server_key: str,
        hostname: str | None = None,
        port: int | None = None,
        username: str | None = None,
        socket_type: SocketType | None = None,
        auth_method: AuthMethod | None = None,
        description: str | None = None,
        confirm: bool = False,
        consent: Gate("change an outgoing mail server") = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Change an existing SMTP server. Only the fields you pass are touched.

        Get `server_key` from `smtp_list`. Changing the hostname or username usually
        invalidates the stored password, and the user will be asked for it on the next
        send — this tool neither reads nor writes it.
        """
        guard_write("change outgoing servers")
        fields = _changes(
            hostname=hostname,
            port=port,
            username=username,
            socketType=socket_type,
            authMethod=auth_method,
            description=description,
        )
        if not fields:
            raise UsageError(
                "Nothing to change — pass at least one of hostname, port, username, "
                "socket_type, auth_method or description."
            )
        params = {"serverKey": server_key, **fields}
        if dry_run_only:
            return dry_run("x.smtp.update", params)
        require(consent, "change this outgoing server")
        result = await call("x.smtp.update", params, timeout=60.0)
        return _applied(f"smtp server {server_key}", result, serverKey=server_key)

    @reg.write_tool(title="Delete an outgoing server", annotations=DESTRUCTIVE)
    async def smtp_delete(
        server_key: str,
        confirm: bool = False,
        consent: Gate("delete an outgoing mail server") = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Remove an SMTP server. Identities using it will be left unable to send.

        Thunderbird has no undo for this, and the settings are not recoverable from
        the UI, so the reply repeats them — check `smtp_list` for identities pointing
        at this key first.
        """
        guard_write("delete outgoing servers")
        params = {"serverKey": server_key}
        if dry_run_only:
            return dry_run("x.smtp.delete", params)
        require(consent, "delete this outgoing server")
        result = await call("x.smtp.delete", params, timeout=60.0)
        payload = result if isinstance(result, dict) else {}
        return changed(
            f"smtp server {server_key}",
            before=payload.get("previous") or payload.get("server"),
            after=None,
            serverKey=server_key,
            recoverable=False,
            affectedIdentities=payload.get("affectedIdentities") or [],
        )

    @reg.write_tool(title="Set the default outgoing server", annotations=IDEMPOTENT_WRITE)
    async def smtp_set_default(
        server_key: str,
        confirm: bool = False,
        consent: Gate("change the default outgoing mail server") = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Make one SMTP server the default for identities that have none of their own."""
        guard_write("change the default outgoing server")
        params = {"serverKey": server_key}
        if dry_run_only:
            return dry_run("x.smtp.setDefault", params)
        require(consent, "change the default outgoing server")
        result = await call("x.smtp.setDefault", params, timeout=60.0)
        return _applied("default smtp server", result, serverKey=server_key)
