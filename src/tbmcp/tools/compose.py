"""Composing mail — the one toolset whose output leaves the machine.

Everything here defaults to a *draft*. A draft is reviewable, deletable, and costs
the user nothing; a send is none of those. The default is only overridden by the
operator (`tbmcp serve --send`), never by the model, so an agent that forgets to
think about `mode` still lands in the safe place.

The gates are the same four layers as everywhere else, but sends also carry
`OUTBOUND` annotations, which mark them destructive *and* open-world so a host that
reads annotations prompts even when its policy would otherwise auto-approve.
"""

# Tool signatures must evaluate at definition time so Gate(...) produces a real
# Annotated[Consent, Resolve(...)] rather than a string the SDK re-evaluates.

from typing import Any, Literal

from ..safety import OUTBOUND, Gate, guard_write, require
from ..server import Registrar
from ._common import call, dry_run
from .compose_drafts import register as register_drafts
from .compose_fields import DeliveryFormat, Priority, _details, _mode, _result


def register(reg: Registrar) -> None:
    @reg.write_tool(title="Send or draft a message", annotations=OUTBOUND)
    async def mail_send(
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        is_html: bool = False,
        attachments: list[str] | None = None,
        inline_images: dict[str, str] | None = None,
        identity_id: str | None = None,
        mode: Literal["draft", "send", "later"] | None = None,
        reply_to_message_id: int | None = None,
        priority: Priority | None = None,
        return_receipt: bool = False,
        delivery_format: DeliveryFormat | None = None,
        custom_headers: dict[str, str] | None = None,
        confirm: bool = False,
        consent: Gate("send this message from the user's account") = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Write a message. Saves a reviewable draft unless `mode="send"`.

        `mode="later"` queues it in the Outbox instead. Recipients are one address per
        list entry. `attachments` are paths to files on this machine. A draft still
        asks for confirmation, because the identical call with `mode="send"` would
        deliver it. Set `reply_to_message_id` to thread the message under an existing
        one — but `mail_reply` is usually what you want, since it also quotes.
        `inline_images` maps names to image paths; place `<img src="cid:NAME">` in
        an HTML body and set `is_html=true`.
        """
        guard_write("send mail")
        effective = _mode(mode)
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
            priority=priority,
            return_receipt=return_receipt,
            delivery_format=delivery_format,
            custom_headers=custom_headers,
            require_recipients=True,
        )
        params["mode"] = effective
        if reply_to_message_id is not None:
            params["replyToMessageId"] = reply_to_message_id
            params["quoteOriginal"] = False
        if dry_run_only:
            return dry_run("compose.send", params)
        require(consent, "send this message")
        result = await call("compose.send", params, timeout=240.0)
        return _result(result, effective, recipients=params.get("to"), subject=subject)

    @reg.write_tool(title="Reply to a message", annotations=OUTBOUND)
    async def mail_reply(
        message_id: int,
        body: str,
        reply_all: bool = False,
        reply_to_list: bool = False,
        quote_original: bool = True,
        is_html: bool = False,
        subject: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        attachments: list[str] | None = None,
        inline_images: dict[str, str] | None = None,
        identity_id: str | None = None,
        mode: Literal["draft", "send", "later"] | None = None,
        confirm: bool = False,
        consent: Gate("send this reply from the user's account") = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Reply to a message. Saves a reviewable draft unless `mode="send"`.

        Thunderbird derives the recipients, the subject and the quoted original;
        `body` goes above the quote. `reply_all` copies everyone, `reply_to_list`
        answers the mailing list. Passing `cc` replaces the addresses Thunderbird
        derived, so leave it unset unless that is the intent.
        `inline_images` maps names to image paths; place `<img src="cid:NAME">` in
        an HTML body and set `is_html=true`.
        """
        guard_write("send mail")
        effective = _mode(mode)
        params = _details(
            cc=cc,
            bcc=bcc,
            subject=subject,
            body=body,
            is_html=is_html,
            attachments=attachments,
            inline_images=inline_images,
            identity_id=identity_id,
        )
        params.update(
            {
                "messageId": message_id,
                "mode": effective,
                "replyType": "replyToList"
                if reply_to_list
                else ("replyToAll" if reply_all else "replyToSender"),
                "quoteOriginal": quote_original,
            }
        )
        require(consent, "send this reply")
        result = await call("compose.reply", params, timeout=240.0)
        return _result(result, effective, repliedTo=message_id, replyType=params["replyType"])

    @reg.write_tool(title="Forward a message", annotations=OUTBOUND)
    async def mail_forward(
        message_id: int,
        to: list[str],
        body: str | None = None,
        forward_as: Literal["inline", "attachment"] = "inline",
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        subject: str | None = None,
        is_html: bool = False,
        attachments: list[str] | None = None,
        inline_images: dict[str, str] | None = None,
        identity_id: str | None = None,
        mode: Literal["draft", "send", "later"] | None = None,
        confirm: bool = False,
        consent: Gate("forward this message to someone else") = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Forward a message. Saves a reviewable draft unless `mode="send"`.

        `inline` quotes the original in the body; `attachment` attaches it as a
        `.eml`, which preserves the headers a recipient may need. `body` is your
        covering note and goes above the forwarded text.
        `inline_images` maps names to image paths; place `<img src="cid:NAME">` in
        an HTML body and set `is_html=true`.
        """
        guard_write("send mail")
        effective = _mode(mode)
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
            require_recipients=True,
        )
        params.update(
            {
                "messageId": message_id,
                "mode": effective,
                "forwardType": "forwardInline" if forward_as == "inline" else "forwardAsAttachment",
            }
        )
        require(consent, "forward this message")
        result = await call("compose.forward", params, timeout=240.0)
        return _result(result, effective, forwarded=message_id, forwardAs=forward_as)

    register_drafts(reg)
