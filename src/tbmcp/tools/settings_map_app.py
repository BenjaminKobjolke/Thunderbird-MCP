"""Curated Thunderbird preference maps used by settings_describe."""

from typing import Any

_SETTINGS_MAP_APP: tuple[dict[str, Any], ...] = (
    {
        "area": "Spelling",
        "settings": (
            {
                "pref": "mail.spellcheck.inline",
                "type": "bool",
                "what": "Check spelling as the user types.",
                "values": "true or false",
                "restartNeeded": False,
            },
            {
                "pref": "spellchecker.dictionary",
                "type": "string",
                "what": "Which dictionary to spell-check against.",
                "values": 'a locale code such as "en-GB"; recent builds accept a '
                "comma-separated list",
                "restartNeeded": False,
                "note": "Only installed dictionaries take effect — an unknown locale "
                "silently disables checking.",
            },
        ),
    },
    {
        "area": "New-mail notification",
        "note": "How often each account checks is per-server, not global: "
        "mail.server.<key>.check_time (minutes) and mail.server.<key>.check_new_mail. "
        "Use account_get_server / account_set_server for those.",
        "settings": (
            {
                "pref": "mail.biff.show_alert",
                "type": "bool",
                "what": "Show an alert when new mail arrives.",
                "values": "true or false",
                "restartNeeded": False,
            },
            {
                "pref": "mail.biff.use_system_alert",
                "type": "bool",
                "what": "Use the operating system's notifications instead of Thunderbird's "
                "own alert window.",
                "values": "true or false",
                "restartNeeded": False,
                "note": "On Windows this routes new-mail alerts through Action Centre, "
                "so Focus Assist can then suppress them.",
            },
            {
                "pref": "mail.biff.play_sound",
                "type": "bool",
                "what": "Play a sound for new mail.",
                "values": "true or false",
                "restartNeeded": False,
            },
            {
                "pref": "mail.biff.play_sound.type",
                "type": "int",
                "what": "Which sound that is.",
                "values": "0 = system default, 1 = the file named by mail.biff.play_sound.url",
                "restartNeeded": False,
            },
            {
                "pref": "mail.biff.alert.show_preview",
                "type": "bool",
                "what": "Include a snippet of the message body in the alert.",
                "values": "true or false",
                "restartNeeded": False,
            },
        ),
    },
    {
        "area": "Return receipts",
        "note": "The per-case response policy lives under mail.mdn.report.* — list it "
        'with pref_list(prefix="mail.mdn.") rather than guessing the sub-names.',
        "settings": (
            {
                "pref": "mail.mdn.report.enabled",
                "type": "bool",
                "what": "Whether Thunderbird responds to return-receipt (MDN) requests.",
                "values": "true or false",
                "restartNeeded": False,
            },
            {
                "pref": "mail.identity.default.request_return_receipt",
                "type": "bool",
                "what": "Ask for a receipt on messages this user sends.",
                "values": "true or false",
                "restartNeeded": False,
            },
        ),
    },
    {
        "area": "Calendar",
        "settings": (
            {
                "pref": "calendar.timezone.local",
                "type": "string",
                "what": "Time zone every event is displayed in.",
                "values": 'an IANA zone id, e.g. "Europe/Istanbul" or "UTC"',
                "restartNeeded": True,
                "note": "Existing views keep the old zone until Thunderbird restarts.",
            },
            {
                "pref": "calendar.week.start",
                "type": "int",
                "what": "First day of the week in calendar views.",
                "values": "0 = Sunday through 6 = Saturday",
                "restartNeeded": False,
            },
            {
                "pref": "calendar.event.defaultlength",
                "type": "int",
                "what": "Length of a new event, in minutes.",
                "values": "whole minutes",
                "restartNeeded": False,
            },
            {
                "pref": "calendar.alarms.onforevents",
                "type": "int",
                "what": "Whether new events get a reminder by default.",
                "values": "0 = no reminder, 1 = add one",
                "restartNeeded": False,
                "note": 'An int, not a bool — writing type="bool" is refused.',
            },
            {
                "pref": "calendar.alarms.eventalarmlen",
                "type": "int",
                "what": "How long before an event that default reminder fires.",
                "values": "a count of the unit in calendar.alarms.eventalarmunit",
                "restartNeeded": False,
            },
            {
                "pref": "calendar.alarms.eventalarmunit",
                "type": "string",
                "what": "Unit for the default event reminder.",
                "values": '"minutes", "hours" or "days"',
                "restartNeeded": False,
            },
            {
                "pref": "calendar.alarms.onfortodos",
                "type": "int",
                "what": "Same default reminder, for tasks.",
                "values": "0 = no reminder, 1 = add one (length in calendar.alarms.todoalarmlen)",
                "restartNeeded": False,
            },
            {
                "pref": "calendar.view.daystarthour",
                "type": "int",
                "what": "First hour shown in day and week views.",
                "values": "0 to 23",
                "restartNeeded": False,
            },
            {
                "pref": "calendar.view.dayendhour",
                "type": "int",
                "what": "Last hour shown in day and week views.",
                "values": "0 to 23, above daystarthour",
                "restartNeeded": False,
            },
        ),
    },
    {
        "area": "Updates",
        "settings": (
            {
                "pref": "app.update.auto",
                "type": "bool",
                "what": "Install updates without asking.",
                "values": "true installs automatically, false asks first",
                "restartNeeded": False,
                "note": "Applies at the next update check, not immediately. A managed "
                "install can lock it — pref_get reports locked:true when that is the case.",
            },
        ),
    },
    {
        "area": "Startup",
        "note": "There is no preference that pins a folder to open at startup: "
        "Thunderbird restores whatever was selected when it last closed. Whether an "
        "account fetches mail at launch is per-server — "
        "mail.server.<key>.login_at_startup, via account_get_server.",
        "settings": (
            {
                "pref": "mailnews.start_page.enabled",
                "type": "bool",
                "what": "Show the start page in the message pane at launch.",
                "values": "true or false",
                "restartNeeded": False,
            },
            {
                "pref": "mailnews.start_page.url",
                "type": "string",
                "what": "Which page that is.",
                "values": "an absolute URL",
                "restartNeeded": False,
            },
        ),
    },
)
