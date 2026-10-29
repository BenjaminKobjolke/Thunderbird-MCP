"""Shared settings and reply helpers for account tools."""

from typing import Any, Literal

from ..errors import UsageError
from ._common import changed

SERVER_SETTINGS: tuple[str, ...] = (
    "hostname",
    "port",
    "username",
    "socketType",
    "authMethod",
    "prettyName",
    "doBiff",
    "biffMinutes",
    "downloadOnBiff",
    "loginAtStartUp",
    "limitOfflineMessageSize",
    "maxMessageSize",
    "emptyTrashOnExit",
    "useIdle",
    "maximumConnectionsNumber",
    "forceSelect",
    "cleanupInboxOnExit",
)


JUNK_SETTINGS: tuple[str, ...] = (
    "level",
    "moveOnSpam",
    "moveTargetMode",
    "actionTargetAccount",
    "actionTargetFolder",
    "purge",
    "purgeInterval",
    "useWhiteList",
    "whiteListAbURI",
    "manualMark",
    "markAsReadOnSpam",
)


FOLDER_SETTINGS: tuple[str, ...] = (
    "fccFolder",
    "doFcc",
    "draftFolder",
    "stationeryFolder",
    "archiveFolder",
    "archiveEnabled",
    "archiveGranularity",
    "archiveKeepFolderStructure",
)


SYNC_SETTINGS: tuple[str, ...] = (
    "offlineDownload",
    "autoSyncOfflineStores",
    "downloadBodiesOnGetNewMail",
    "limitOfflineMessageSize",
    "maxMessageSize",
    "offlineSupportLevel",
    "doBiff",
    "biffMinutes",
    "downloadOnBiff",
    "useIdle",
    "leaveMessagesOnServer",
    "deleteMailLeftOnServer",
    "numDaysToLeaveOnServer",
    "headersOnly",
)


SocketType = Literal["plain", "starttls", "tls"]


AuthMethod = Literal["none", "password-cleartext", "password-encrypted", "gssapi", "ntlm", "oauth2"]


def _setting(value: str, allowed: tuple[str, ...], *, field: str = "setting") -> str:
    """Resolve a setting name, forgiving the casing a model is likely to invent.

    The wire names are the XPCOM property names, which are camelCase; models reach
    for `login_at_start_up` about half the time.
    """
    wanted = str(value or "").replace("_", "").replace("-", "").lower()
    for name in allowed:
        if name.lower() == wanted:
            return name
    raise UsageError(
        f"{field} must be one of: {', '.join(allowed)}; got {value!r}. "
        "The matching get tool shows the current values."
    )


def _rows(result: Any, *keys: str) -> list[dict[str, Any]]:
    """Pull the list out of a bridge reply without pinning one key name."""
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in keys:
            value = result.get(key)
            if isinstance(value, list):
                return value
    return []


def _changes(**fields: Any) -> dict[str, Any]:
    """Only what the caller actually supplied — omitted fields must stay untouched.

    An empty string is a real value (it clears a field), so `None` is the only
    "leave alone" marker.
    """
    return {name: value for name, value in fields.items() if value is not None}


def _applied(target: str, result: dict[str, Any] | None, **extra: Any) -> dict[str, Any]:
    """Write envelope for the privileged setters, which all report previous/current."""
    payload = result or {}
    out = changed(target, before=payload.get("previous"), after=payload.get("current"), **extra)
    for name in ("restartRequired", "note", "warnings"):
        value = payload.get(name)
        if value:
            out[name] = value
    return out


def _account_row(account: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    """The official view of one account, keyed the way the privileged half keys it.

    `MailAccount.id` and `nsIMsgAccount.key` are the same string ("account1"), which
    is what makes the merge below a plain dictionary update.
    """
    key = account.get("id") or account.get("key")
    return key, {
        "key": key,
        "name": account.get("name"),
        "type": account.get("type"),
        "identities": [
            {
                "key": identity.get("id") or identity.get("key"),
                "email": identity.get("email"),
                "name": identity.get("name"),
            }
            for identity in account.get("identities") or []
        ],
    }
