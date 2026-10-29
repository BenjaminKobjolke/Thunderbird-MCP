"""Draft, compose-window and send-status tools."""

from typing import Any, Literal

from ..errors import UsageError
from ..safety import MUTATING, guard_write
from ..server import Registrar
from ._common import call, clamp, message_summary, one_of, page
from .compose_fields import _details, _result


def register(reg: Registrar) -> None:
    @reg.write_tool(title="Save a draft or template", annotations=MUTATING, interactive=False)
    async def mail_draft_save(
        subject: str | None = None,
        body: str | None = None,
        to: list[str] | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        kind: Literal["draft", "template"] = "draft",
        is_html: bool = False,
        attachments: list[str] | None = None,
        inline_images: dict[str, str] | None = None,
        identity_id: str | None = None,
    ) -> dict[str, Any]:
        """Save a message without sending it, as a draft or a template.

        Nothing leaves the machine, so this is not gated — a draft is exactly the
        thing to produce when you want the user to review before anything is sent.
        A template is the reusable kind: Thunderbird keeps it in Templates and opens
        a copy when the user picks it. Recipients are optional here, unlike a send.
        `inline_images` maps names to image paths; place `<img src="cid:NAME">` in
        an HTML body and set `is_html=true`.
        """
        guard_write("save drafts")
        params = _details(
            to=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body=body,
            is_html=is_html,
            attachments=attachments,
            inline_images=inline_images,
            identity_id=identity_id,
        )
        params["mode"] = one_of(kind, ("draft", "template"), field="kind", default="draft")
        result = await call("compose.save", params, timeout=120.0)
        return _result(result, params["mode"])

    @reg.write_tool(title="Open a compose window", annotations=MUTATING, interactive=False)
    async def mail_compose_open(
        to: list[str] | None = None,
        subject: str | None = None,
        body: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        is_html: bool = False,
        attachments: list[str] | None = None,
        inline_images: dict[str, str] | None = None,
        identity_id: str | None = None,
        reply_to_message_id: int | None = None,
        forward_message_id: int | None = None,
        reply_all: bool = False,
        quote_original: bool = True,
    ) -> dict[str, Any]:
        """Open a populated compose window for the user to finish by hand.

        The right answer whenever the wording matters more than the automation, or
        when the user declined a send: they get the draft in front of them with the
        cursor in it. Nothing is sent or saved, and the user sees the window appear,
        so this is not gated.
        `inline_images` maps names to image paths; place `<img src="cid:NAME">` in
        an HTML body and set `is_html=true`.
        """
        guard_write("open compose windows")
        if reply_to_message_id is not None and forward_message_id is not None:
            raise UsageError(
                "Pass either reply_to_message_id or forward_message_id, not both — one "
                "window cannot be a reply and a forward at once."
            )
        params = _details(
            to=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body=body,
            is_html=is_html,
            attachments=attachments,
            inline_images=inline_images,
            identity_id=identity_id,
        )
        if reply_to_message_id is not None:
            params["replyToMessageId"] = reply_to_message_id
            params["replyType"] = "replyToAll" if reply_all else "replyToSender"
        if forward_message_id is not None:
            params["forwardMessageId"] = forward_message_id
        params["quoteOriginal"] = quote_original
        result = await call("compose.open", params, timeout=120.0)
        return {
            "opened": True,
            "sent": False,
            "tabId": result.get("tabId"),
            "windowId": result.get("windowId"),
            "canSendNow": result.get("canSendNow"),
            "note": (
                "The window is on the user's screen with this content in it. Ask them to "
                "review and send it themselves; nothing has been sent or saved."
            ),
        }

    @reg.read_tool(title="Check what is waiting to be sent")
    async def mail_send_status(limit: int = 25) -> dict[str, Any]:
        """List messages sitting in the Outbox, unsent.

        An empty list is the normal answer. Anything here was queued with
        `mode="later"`, or written while Thunderbird was offline, and will go out on
        the next 'Send Unsent Messages'.
        """
        result = await call(
            "compose.status",
            {"limit": clamp(limit, default=25, minimum=1, maximum=200, field="limit")},
            timeout=60.0,
        )
        return page(
            [message_summary(m) for m in result.get("messages", [])],
            total=result.get("queued"),
            folders=result.get("folders") or [],
            queued=result.get("queued", 0),
            note=result.get("note"),
        )
