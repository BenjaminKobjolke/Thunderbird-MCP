"""Preference write operations and their input validators."""

from typing import Any, Literal

from ..errors import BlockedError, UsageError
from ..safety import IDEMPOTENT_WRITE, Gate, current_settings, guard_write, require
from ..server import Registrar
from ._common import call, changed, dry_run


def register_write(reg: Registrar) -> None:

    @reg.write_tool(title="Change a preference", annotations=IDEMPOTENT_WRITE)
    async def pref_set(
        name: str,
        type: Literal["bool", "int", "string"],  # the wire contract's name; shadows a builtin
        value: bool | int | str,
        confirm: bool = False,
        consent: Gate("change a Thunderbird preference") = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Change one Thunderbird preference.

        `type` must match the preference's existing type — pass `"int"` for a number
        even when it reads like a boolean (`calendar.alarms.onforevents` is the classic
        trap), and the call is refused with the right type if you get it wrong. Use
        `settings_describe` for the name and the allowed values, and `dry_run_only=true`
        to see the current value alongside what you propose.
        """
        guard_write("change preferences")
        name = _clean_name(name)
        _check_value_type(type, value)

        # Ask the local policy before spending a round trip: the refusal is the same
        # one the privileged module would give, and it can say which flag would help.
        allowed, reason = current_settings().pref_writable(name)
        if not allowed:
            raise BlockedError(
                reason,
                code="PREF_NOT_WRITABLE",
                # The allowlist is a policy the user can widen; the denylist is not, so
                # do not offer a flag that will still refuse.
                needs="--unsafe-prefs"
                if "allowlist" in reason
                else "a change made by hand in Thunderbird's settings",
            )

        if dry_run_only:
            before = await call("x.prefs.get", {"name": name})
            return dry_run(
                f"pref:{name}",
                {
                    "name": name,
                    "type": type,
                    "currentValue": before.get("value"),
                    "currentType": before.get("type"),
                    "proposedValue": value,
                },
            )

        require(consent, "change this preference")
        result = await call(
            "x.prefs.set", {"name": name, "type": type, "value": value}, timeout=30.0
        )
        return changed(
            f"pref:{name}",
            before=result.get("previous"),
            after=result.get("current"),
            name=name,
            restartRequired=result.get("restartRequired", False),
            note="Restart Thunderbird for this to take effect."
            if result.get("restartRequired")
            else "Applied live.",
        )

    @reg.write_tool(title="Reset a preference", annotations=IDEMPOTENT_WRITE)
    async def pref_reset(
        name: str,
        confirm: bool = False,
        consent: Gate("reset a Thunderbird preference to its default") = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Clear a user-set preference so the shipped default applies again.

        The cleanest undo for a `pref_set` the user did not like — it removes the entry
        from `prefs.js` rather than writing the old value back. A preference already at
        its default is reported as unchanged instead of failing.
        """
        guard_write("reset preferences")
        name = _clean_name(name)
        allowed, reason = current_settings().pref_writable(name)
        if not allowed:
            raise BlockedError(reason, code="PREF_NOT_WRITABLE")
        require(consent, "reset this preference")
        result = await call("x.prefs.reset", {"name": name}, timeout=30.0)
        if not result.get("changed"):
            return {
                "changed": False,
                "name": name,
                "reason": result.get("reason", "already at its default value"),
            }
        return changed(
            f"pref:{name}",
            before=result.get("previous"),
            after=result.get("current"),
            name=name,
        )


# -------------------------------------------------------------------------- helpers


def _clean_name(name: str) -> str:
    """Reject the shapes that would otherwise reach Thunderbird as a silent no-op."""
    text = str(name or "").strip()
    if not text:
        raise UsageError("A preference name is required, e.g. mail.pane_config.dynamic.")
    if any(character.isspace() for character in text):
        raise UsageError(
            f"Preference names contain no whitespace; got {name!r}. Use the exact name "
            "from settings_describe or pref_list."
        )
    return text


def _check_value_type(declared: str, value: Any) -> None:
    """Catch a value that does not match its declared type before the bridge does.

    `bool` is a subclass of `int` in Python, so the order of these checks matters:
    accepting `true` for an int preference would write 1 and confuse everyone later.
    """
    if declared == "bool":
        if not isinstance(value, bool):
            raise UsageError(
                f'type="bool" needs true or false; got {value!r}. Pass '
                f'type="{"int" if isinstance(value, int) else "string"}" if that is '
                "really the preference's type."
            )
    elif declared == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise UsageError(f'type="int" needs a whole number, unquoted; got {value!r}.')
    elif declared == "string":
        if not isinstance(value, str):
            raise UsageError(f'type="string" needs a quoted string; got {value!r}.')
    else:
        raise UsageError(f'type must be "bool", "int" or "string"; got {declared!r}.')
