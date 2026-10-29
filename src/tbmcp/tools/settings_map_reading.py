"""Curated Thunderbird preference maps used by settings_describe."""

from typing import Any

# The curated intent → preference map behind `settings_describe`, kept as data so the
# tool body stays readable and so a wrong entry is a one-line fix.
#
# Rules for anything added here: only preferences confirmed to exist on Thunderbird
# 153, only value meanings that have been checked, and no defaults quoted as fact —
# defaults move between builds and locales, and `pref_get` is one call away. A wrong
# name sends the model off writing a preference nothing reads, which is worse than
# leaving the setting out.
_SETTINGS_MAP_READING: tuple[dict[str, Any], ...] = (
    {
        "area": "Message list and layout",
        "settings": (
            {
                "pref": "mail.pane_config.dynamic",
                "type": "int",
                "what": "Where the message pane sits relative to the message list.",
                "values": "0 = classic (list on top, message below), 1 = wide (message "
                "below, full width), 2 = vertical (message to the right)",
                "restartNeeded": False,
                "note": "Existing windows may need reopening; new ones pick it up at once.",
            },
            {
                "pref": "mail.threadpane.listview",
                "type": "int",
                "what": "Whether the message list draws as cards or as a table.",
                "values": "0 = cards, 1 = table",
                "restartNeeded": False,
                "note": "Thunderbird 115 and later only.",
            },
            {
                "pref": "mail.uidensity",
                "type": "int",
                "what": "Spacing of the whole interface.",
                "values": "0 = compact, 1 = normal, 2 = relaxed",
                "restartNeeded": False,
            },
            {
                "pref": "mail.uifontsize",
                "type": "int",
                "what": "Interface font size, independent of message text.",
                "values": "0 = follow the operating system, otherwise a size in points "
                "(roughly 9 to 30)",
                "restartNeeded": False,
            },
            {
                "pref": "mail.showCondensedAddresses",
                "type": "bool",
                "what": "Show only the display name for people who are in an address book.",
                "values": "true or false",
                "restartNeeded": False,
            },
        ),
    },
    {
        "area": "Threading and sort defaults",
        "note": "These are the defaults for folders that have no saved view state of "
        "their own. A folder the user has already sorted keeps its own setting, so "
        "changing these will look like nothing happened on familiar folders.",
        "settings": (
            {
                "pref": "mailnews.default_sort_type",
                "type": "int",
                "what": "Column newly-opened folders sort by.",
                "values": "18 = date, 19 = subject, 20 = sender, 22 = thread, 24 = status, "
                "25 = size, 26 = starred, 30 = tags, 35 = received, 36 = correspondent",
                "restartNeeded": False,
            },
            {
                "pref": "mailnews.default_sort_order",
                "type": "int",
                "what": "Direction of that sort.",
                "values": "1 = ascending, 2 = descending",
                "restartNeeded": False,
            },
            {
                "pref": "mailnews.default_view_flags",
                "type": "int",
                "what": "Whether newly-opened folders are threaded.",
                "values": "0 = unthreaded, 1 = threaded",
                "restartNeeded": False,
                "note": "A bitfield: higher bits carry grouped-by-sort and unread-only. "
                "Read the current value first and leave bits you do not recognise alone.",
            },
        ),
    },
    {
        "area": "Reading",
        "settings": (
            {
                "pref": "mailnews.mark_message_read.auto",
                "type": "bool",
                "what": "Mark a message read when it is displayed at all.",
                "values": "true or false",
                "restartNeeded": False,
                "note": "Set false when the user wants reading state to be manual only; "
                "the delay preferences below then have no effect.",
            },
            {
                "pref": "mailnews.mark_message_read.delay",
                "type": "bool",
                "what": "Wait before marking a displayed message read.",
                "values": "true or false",
                "restartNeeded": False,
            },
            {
                "pref": "mailnews.mark_message_read.delay.interval",
                "type": "int",
                "what": "How long that wait is, in seconds.",
                "values": "whole seconds; only consulted when the delay preference is true",
                "restartNeeded": False,
            },
            {
                "pref": "mailnews.message_display.disable_remote_image",
                "type": "bool",
                "what": "Block remote images and other remote content in messages.",
                "values": "true blocks (the privacy-preserving setting), false loads",
                "restartNeeded": False,
            },
            {
                "pref": "font.size.variable.x-western",
                "type": "int",
                "what": "Message text size for proportional fonts, in points.",
                "values": "roughly 9 to 72",
                "restartNeeded": False,
            },
            {
                "pref": "font.size.fixed.x-western",
                "type": "int",
                "what": "Message text size for monospace fonts, in points.",
                "values": "roughly 9 to 72",
                "restartNeeded": False,
                "note": "Only visible where plain-text mail is shown in a fixed-width font.",
            },
        ),
    },
    {
        "area": "Composing",
        "settings": (
            {
                "pref": "mail.default_html_action",
                "type": "int",
                "what": "What to do when a recipient may not accept HTML.",
                "values": "0 = ask each time, 1 = send plain text only, 2 = send HTML only, "
                "3 = send both",
                "restartNeeded": False,
            },
            {
                "pref": "mail.identity.default.compose_html",
                "type": "bool",
                "what": "Whether new messages start in HTML rather than plain text.",
                "values": "true or false",
                "restartNeeded": False,
            },
            {
                "pref": "mail.forward_message_mode",
                "type": "int",
                "what": "Whether forwards quote the original inline or attach it.",
                "values": "0 = as attachment, 2 = inline",
                "restartNeeded": False,
                "note": "1 is a legacy value; do not set it.",
            },
            {
                "pref": "mail.compose.attachment_reminder",
                "type": "bool",
                "what": "Warn when a message mentions an attachment but has none.",
                "values": "true or false",
                "restartNeeded": False,
            },
            {
                "pref": "mail.compose.attachment_reminder_keywords",
                "type": "string",
                "what": "Comma-separated words that trigger that warning.",
                "values": 'e.g. "attachment,attached,enclosed,CV" — the shipped default '
                "is localised, so read it before replacing it",
                "restartNeeded": False,
            },
        ),
    },
    {
        "area": "Quoting and replies",
        "note": "mail.identity.default.* is only the fallback. A configured identity "
        "stores its own copy as mail.identity.id<N>.<key>, so use identity_set to change "
        "one account and expect the default to look ignored.",
        "settings": (
            {
                "pref": "mail.identity.default.auto_quote",
                "type": "bool",
                "what": "Quote the original message when replying.",
                "values": "true or false",
                "restartNeeded": False,
            },
            {
                "pref": "mail.identity.default.reply_on_top",
                "type": "int",
                "what": "Where the cursor starts relative to the quote.",
                "values": "0 = reply below the quote, 1 = reply above the quote, "
                "2 = start with the quote selected",
                "restartNeeded": False,
            },
            {
                "pref": "mail.identity.default.sig_bottom",
                "type": "bool",
                "what": "Whether the signature goes below the quoted text or above it.",
                "values": "true = below the quote, false = above it",
                "restartNeeded": False,
            },
        ),
    },
)
