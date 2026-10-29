"""Shared compose field validation and result formatting."""

# Tool signatures must evaluate at definition time so Gate(...) produces a real
# Annotated[Consent, Resolve(...)] rather than a string the SDK re-evaluates.

import re
from typing import Any, Literal

from ..errors import UsageError
from ..safety import current_settings
from ._common import one_of

MODES = ("draft", "send", "later")

#: A recipient must look like `local@domain.tld`, optionally wrapped in a display
#: name. Deliberately strict: a typo that reaches the SMTP server is a bounce with
#: the user's name on it, and a wrong-but-valid address is worse than a refusal.
_MAILBOX = re.compile(r"^[^\s@<>,;]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}$")

#: Thunderbird's own restriction on ComposeDetails.customHeaders, checked here so the
#: model gets a fixable message instead of an ExtensionError from inside the add-on.
_HEADER_NAME = re.compile(r"^(?:X-(?!Mozilla-)[A-Za-z0-9-]+|MSIP_Labels)$", re.IGNORECASE)
_INLINE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

Priority = Literal["lowest", "low", "normal", "high", "highest"]
DeliveryFormat = Literal["auto", "plaintext", "html", "both"]


def _mode(mode: str | None) -> str:
    """Resolve the effective send mode.

    An unset `mode` means "use the server's policy", which is `draft` unless the
    operator started with `--send`. That decision belongs to whoever launched the
    server, not to the model composing the message.
    """
    if mode is not None:
        return one_of(mode, MODES, field="mode", default="draft")
    return "send" if current_settings().send_mode == "send" else "draft"


def _addresses(values: list[str] | None, *, field: str, required: bool = False) -> list[str] | None:
    """Validate a recipient list, one recipient per entry."""
    items = [str(v).strip() for v in (values or []) if str(v).strip()]
    if not items:
        if required:
            raise UsageError(
                f"{field} is empty — a message needs at least one recipient. Pass a list "
                'like ["someone@example.com"].'
            )
        return None
    clean: list[str] = []
    for item in items:
        inner = item
        if item.endswith(">") and "<" in item:
            inner = item[item.rindex("<") + 1 : -1].strip()
        if _MAILBOX.match(inner):
            clean.append(item)
            continue
        # `List <List>` is how the compose API addresses an address-book mailing list;
        # anything else without an @ is a mistake worth stopping here.
        if inner and "@" not in inner and item.endswith(">") and "<" in item:
            clean.append(item)
            continue
        raise UsageError(
            f"{field} entry {item!r} is not an email address. Use one entry per recipient, "
            'either "someone@example.com" or "Their Name <someone@example.com>"; an '
            'address-book mailing list is written "List Name <List Name>".'
        )
    return clean


def _attachments(paths: list[str] | None) -> list[dict[str, str]] | None:
    """Turn local file paths into the add-on's attachment shape.

    The paths travel as paths: the privileged half reads them with `x.files.read`, so
    a large file never has to be base64'd through a JSON frame on the bridge.
    """
    if not paths:
        return None
    clean: list[dict[str, str]] = []
    for path in paths:
        text = str(path).strip()
        if not text:
            raise UsageError("attachments must be non-empty file paths.")
        clean.append({"path": text})
    return clean


def _inline_images(
    images: dict[str, str] | None, *, body: str | None, is_html: bool
) -> list[dict[str, str]] | None:
    if not images:
        return None
    if not is_html:
        raise UsageError("inline_images needs is_html=true and an HTML body.")
    clean = []
    for name, path in images.items():
        if not _INLINE_NAME.fullmatch(name):
            raise UsageError(
                f"inline_images name {name!r} must use letters, digits, dots, underscores or hyphens."
            )
        if not str(path).strip():
            raise UsageError(f"inline_images[{name!r}] needs a non-empty file path.")
        if not body or (f'"cid:{name}"' not in body and f"'cid:{name}'" not in body):
            raise UsageError(f'put <img src="cid:{name}"> in body where the picture belongs.')
        clean.append({"cid": name, "path": str(path).strip()})
    return clean


def _headers(headers: dict[str, str] | None) -> list[dict[str, str]] | None:
    if not headers:
        return None
    out: list[dict[str, str]] = []
    for name, value in headers.items():
        if not _HEADER_NAME.match(str(name)):
            raise UsageError(
                f"custom_headers cannot set {name!r}. Thunderbird only allows X-* headers "
                "(and not X-Mozilla-*); use `priority`, `return_receipt` or `reply_to_"
                "message_id` for the standard ones."
            )
        out.append({"name": str(name), "value": "" if value is None else str(value)})
    return out


def _details(
    *,
    to: list[str] | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    subject: str | None = None,
    body: str | None = None,
    is_html: bool = False,
    attachments: list[str] | None = None,
    inline_images: dict[str, str] | None = None,
    identity_id: str | None = None,
    priority: str | None = None,
    return_receipt: bool = False,
    delivery_format: str | None = None,
    custom_headers: dict[str, str] | None = None,
    require_recipients: bool = False,
) -> dict[str, Any]:
    """The parameter block every compose method shares.

    Keys that are `None` are omitted rather than sent: on a reply the add-on treats a
    present key as "replace what Thunderbird derived", and clearing an auto-filled To
    line is never what the caller meant.
    """
    params: dict[str, Any] = {
        "to": _addresses(to, field="to", required=require_recipients),
        "cc": _addresses(cc, field="cc"),
        "bcc": _addresses(bcc, field="bcc"),
        "subject": subject,
        "body": body,
        "isHtml": bool(is_html),
        "attachments": _attachments(attachments),
        "inlineImages": _inline_images(inline_images, body=body, is_html=is_html),
        "identityId": identity_id,
        "priority": priority,
        "deliveryFormat": delivery_format,
        "customHeaders": _headers(custom_headers),
    }
    if return_receipt:
        params["returnReceipt"] = True
    return {key: value for key, value in params.items() if value is not None}


def _result(raw: dict[str, Any], mode: str, **extra: Any) -> dict[str, Any]:
    """One envelope for every outcome, with the draft case spelled out in words.

    The difference between a helpful default and a surprising one is whether the
    caller can tell, without reading the code, that nothing was sent.
    """
    folder = raw.get("folderPath") or raw.get("folderId")
    payload: dict[str, Any] = {
        "mode": mode,
        "sent": mode == "send",
        "queued": mode == "later",
        "messageId": raw.get("messageId"),
        "headerMessageId": raw.get("headerMessageId"),
        "folderId": raw.get("folderId"),
        "folderPath": raw.get("folderPath"),
        "copies": raw.get("copies") or [],
        "via": raw.get("transport"),
    }
    if mode in ("draft", "template"):
        payload["saved"] = True
        payload["draftId"] = raw.get("messageId")
        location = (
            f" in {folder}"
            if folder
            else "; Thunderbird did not report where. Find it with mail_search by subject"
        )
        payload["note"] = (
            f"Nothing was sent. The message is saved as a {mode}{location}. "
            "Tell the user it is waiting for them to read over, and repeat the call "
            'with mode="send" only once they have agreed.'
        )
    elif mode == "later":
        payload["note"] = (
            "Queued in the Outbox and not yet delivered. Thunderbird sends it on the "
            "next 'Send Unsent Messages'; check with mail_send_status."
        )
    else:
        payload["note"] = "Sent. It has left the machine and cannot be recalled."
    payload.update(extra)
    return payload
