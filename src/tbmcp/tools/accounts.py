"""Accounts, identities and outgoing servers — Thunderbird's account configuration.

Opt-in toolset. Only `account_list` degrades to the official API; everything else
needs the privileged half of the add-on, because the official MailExtension API
exposes account names and identities and nothing about servers.

No tool here reads or writes a credential. Passwords stay in Thunderbird's login
manager, which this bridge never touches — after changing a hostname, port or
username the user may have to re-authenticate in Thunderbird itself.
"""

# Tool signatures must evaluate at definition time so Gate(...) produces a real
# Annotated[Consent, Resolve(...)] rather than a string the SDK re-evaluates.

from typing import Any

from ..errors import TbmcpError
from ..safety import IDEMPOTENT_WRITE, Gate, guard_write, require
from ..server import Registrar
from ._common import call, dry_run, page
from .accounts_common import (
    FOLDER_SETTINGS,
    JUNK_SETTINGS,
    SERVER_SETTINGS,
    SYNC_SETTINGS,
    _account_row,
    _applied,
    _rows,
    _setting,
)
from .accounts_identity import register as register_identity
from .accounts_smtp import register as register_smtp


def register(reg: Registrar) -> None:
    @reg.read_tool(title="List accounts")
    async def account_list(include_settings: bool = True) -> dict[str, Any]:
        """List the mail accounts and how each one is configured.

        The official API supplies names, types and identities; the privileged half
        adds host, port, security and check-for-mail settings. The `key` of each
        account (`account1`) is what every other tool in this toolset takes. No
        password is read.
        """
        official = await call(
            # Folder trees belong to folder_list; asking for them here would dwarf
            # the settings the caller came for.
            "accounts.list",
            {"includeFolders": False},
            timeout=45.0,
        )
        rows: dict[str | None, dict[str, Any]] = {}
        for account in _rows(official, "accounts", "items"):
            key, row = _account_row(account)
            rows[key] = row

        note: str | None = None
        if include_settings:
            try:
                settings = await call("x.accounts.list", timeout=45.0)
            except TbmcpError as exc:
                # Half an answer beats none: names and identities are still enough to
                # pick an account key and try again once the add-on is fixed.
                note = (
                    f"Server settings are unavailable ({exc}), so this is the official "
                    "view only. Run `tbmcp doctor` to check the privileged half of the "
                    "add-on."
                )
            else:
                for extra in _rows(settings, "accounts", "items"):
                    key = extra.get("key") or extra.get("id")
                    row = rows.setdefault(key, {"key": key})
                    for name, value in extra.items():
                        if name not in ("key", "id"):
                            # setdefault, not update: the official name and type are
                            # the ones the user sees in Thunderbird's UI.
                            row.setdefault(name, value)

        return page(list(rows.values()), **({"note": note} if note else {}))

    @reg.read_tool(title="Get incoming server settings")
    async def account_get_server(account_key: str) -> dict[str, Any]:
        """Read one account's incoming server settings.

        Host, port, connection security, authentication method and the
        check-for-new-mail settings. Passwords are never read; the login manager is
        out of reach of this bridge.
        """
        result = await call("x.accounts.serverSettings", {"accountKey": account_key}, timeout=30.0)
        return {"accountKey": account_key, **(result or {})}

    @reg.write_tool(title="Change an incoming server setting", annotations=IDEMPOTENT_WRITE)
    async def account_set_server(
        account_key: str,
        setting: str,
        value: str | int | bool,
        confirm: bool = False,
        consent: Gate("change an incoming mail server setting") = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Change one incoming server setting. Getting the connection wrong stops mail.

        `setting` is one of: `hostname`, `port`, `username`, `socketType`,
        `authMethod`, `prettyName` (the account's display name), `doBiff`,
        `biffMinutes`, `downloadOnBiff`, `loginAtStartUp`,
        `limitOfflineMessageSize`, `maxMessageSize`, `emptyTrashOnExit`, and for IMAP
        `useIdle`, `maximumConnectionsNumber`, `forceSelect`, `cleanupInboxOnExit`.

        `socketType` takes `plain`, `starttls` or `tls`; `authMethod` takes `none`,
        `password-cleartext`, `password-encrypted`, `gssapi`, `ntlm` or `oauth2`. The
        account type itself cannot be changed. Passwords are never written: after a
        hostname or username change Thunderbird will ask the user to sign in again.
        """
        guard_write("change server settings")
        name = _setting(setting, SERVER_SETTINGS)
        params = {"accountKey": account_key, "setting": name, "value": value}
        if dry_run_only:
            return dry_run("x.accounts.setServerSetting", params)
        require(consent, "change this server setting")
        result = await call("x.accounts.setServerSetting", params, timeout=60.0)
        return _applied(
            f"account {account_key} server.{name}", result, accountKey=account_key, setting=name
        )

    @reg.read_tool(title="Get junk mail settings")
    async def account_get_junk(account_key: str) -> dict[str, Any]:
        """Read one account's junk-mail handling: level, whitelist, move and purge rules."""
        result = await call("x.accounts.junkSettings", {"accountKey": account_key}, timeout=30.0)
        return {"accountKey": account_key, **(result or {})}

    @reg.write_tool(title="Change a junk mail setting", annotations=IDEMPOTENT_WRITE)
    async def account_set_junk(
        account_key: str,
        setting: str,
        value: str | int | bool,
        confirm: bool = False,
        consent: Gate("change an account's junk mail settings") = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Change one junk-mail setting for an account.

        `setting` is one of: `level`, `moveOnSpam`, `moveTargetMode`,
        `actionTargetAccount`, `actionTargetFolder`, `purge`, `purgeInterval`,
        `useWhiteList`, `whiteListAbURI`, `manualMark`, `markAsReadOnSpam`.
        Folder settings take a folder URI, which `account_get_junk` reports.
        """
        guard_write("change junk settings")
        name = _setting(setting, JUNK_SETTINGS)
        params = {"accountKey": account_key, "setting": name, "value": value}
        if dry_run_only:
            return dry_run("x.accounts.setJunkSetting", params)
        require(consent, "change these junk settings")
        result = await call("x.accounts.setJunkSetting", params, timeout=60.0)
        return _applied(
            f"account {account_key} junk.{name}", result, accountKey=account_key, setting=name
        )

    @reg.read_tool(title="Get copies and folders settings")
    async def account_get_folders(
        account_key: str, identity_key: str | None = None
    ) -> dict[str, Any]:
        """Read where an identity files sent mail, drafts, templates and archives.

        These live on the identity, not the server, so an account with two identities
        can file its mail in two places. Omit `identity_key` for the account's default
        identity.
        """
        result = await call(
            "x.accounts.copiesAndFolders",
            {"accountKey": account_key, "identityKey": identity_key},
            timeout=30.0,
        )
        return {"accountKey": account_key, "identityKey": identity_key, **(result or {})}

    @reg.write_tool(title="Change a copies and folders setting", annotations=IDEMPOTENT_WRITE)
    async def account_set_folders(
        account_key: str,
        setting: str,
        value: str | int | bool,
        identity_key: str | None = None,
        confirm: bool = False,
        consent: Gate("change where an account files sent mail and drafts") = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Change where an identity files sent mail, drafts, templates or archives.

        `setting` is one of: `fccFolder` (sent), `doFcc`, `draftFolder`,
        `stationeryFolder` (templates), `archiveFolder`, `archiveEnabled`,
        `archiveGranularity`, `archiveKeepFolderStructure`. Folder settings take a
        folder URI as reported by `account_get_folders`, not a folder id.
        """
        guard_write("change copies and folders settings")
        name = _setting(setting, FOLDER_SETTINGS)
        params = {
            "accountKey": account_key,
            "identityKey": identity_key,
            "setting": name,
            "value": value,
        }
        if dry_run_only:
            return dry_run("x.accounts.setCopiesAndFolders", params)
        require(consent, "change these folder settings")
        result = await call("x.accounts.setCopiesAndFolders", params, timeout=60.0)
        return _applied(
            f"account {account_key} folders.{name}",
            result,
            accountKey=account_key,
            identityKey=identity_key,
            setting=name,
        )

    @reg.read_tool(title="Get synchronisation settings")
    async def account_get_sync(account_key: str) -> dict[str, Any]:
        """Read an account's offline and synchronisation settings."""
        result = await call("x.accounts.syncSettings", {"accountKey": account_key}, timeout=30.0)
        return {"accountKey": account_key, **(result or {})}

    @reg.write_tool(title="Change a synchronisation setting", annotations=IDEMPOTENT_WRITE)
    async def account_set_sync(
        account_key: str,
        setting: str,
        value: str | int | bool,
        confirm: bool = False,
        consent: Gate("change an account's synchronisation settings") = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Change one offline or synchronisation setting for an account.

        `setting` is one of: `offlineDownload`, `autoSyncOfflineStores`,
        `downloadBodiesOnGetNewMail`, `limitOfflineMessageSize`, `maxMessageSize`,
        `offlineSupportLevel`, `doBiff`, `biffMinutes`, `downloadOnBiff`, `useIdle`,
        and for POP3 `leaveMessagesOnServer`, `deleteMailLeftOnServer`,
        `numDaysToLeaveOnServer`, `headersOnly`.

        Turning offline storage on does not download anything by itself — use
        `folder_sync_offline` for that.
        """
        guard_write("change synchronisation settings")
        name = _setting(setting, SYNC_SETTINGS)
        params = {"accountKey": account_key, "setting": name, "value": value}
        if dry_run_only:
            return dry_run("x.accounts.setSyncSetting", params)
        require(consent, "change these synchronisation settings")
        result = await call("x.accounts.setSyncSetting", params, timeout=60.0)
        return _applied(
            f"account {account_key} sync.{name}", result, accountKey=account_key, setting=name
        )

    register_identity(reg)

    register_smtp(reg)
