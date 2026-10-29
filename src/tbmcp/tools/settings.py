"""Preferences, junk filtering and OpenPGP keys.

These are the widest-blast-radius tools in the server: a preference write reaches
`Services.prefs` with the system principal behind it. Three things narrow it —
`config.PREF_ALLOWLIST` decides what an agent may touch at all, `config.PREF_DENYLIST`
refuses credentials and transport security even under `--unsafe-prefs`, and the
privileged module re-checks its own hard denylist because that is the only layer with
real privilege. Checking here as well is not redundant: it turns a bridge round trip
into an immediate, explainable refusal.
"""

# NOTE: no `from __future__ import annotations` in toolset modules. Tool signatures
# must evaluate at definition time so `Gate(...)` produces a real
# `Annotated[Consent, Resolve(...)]` rather than a string the SDK has to re-evaluate.

from typing import Any

from ..errors import UsageError
from ..safety import (
    current_settings,
    large_output,
)
from ..server import Registrar
from ._common import call, clamp, page
from .settings_junk import register_junk
from .settings_map_app import _SETTINGS_MAP_APP
from .settings_map_reading import _SETTINGS_MAP_READING
from .settings_write import _clean_name, register_write

_SETTINGS_MAP = _SETTINGS_MAP_READING + _SETTINGS_MAP_APP


def register(reg: Registrar) -> None:
    # ---------------------------------------------------------------- reading prefs

    @reg.read_tool(title="Read a preference")
    async def pref_get(name: str) -> dict[str, Any]:
        """Read one Thunderbird preference.

        Reports the value, its type, whether the user has changed it from the default,
        and whether an enterprise policy has locked it. `writable` says whether this
        server would let you change it. Use `settings_describe` to find the name.
        """
        result = await call("x.prefs.get", {"name": _clean_name(name)})
        allowed, reason = current_settings().pref_writable(name)
        payload: dict[str, Any] = {**result, "writable": allowed}
        if not allowed:
            payload["whyNotWritable"] = reason
        return payload

    @reg.read_tool(title="Read several preferences")
    async def pref_get_many(names: list[str]) -> dict[str, Any]:
        """Read up to 100 preferences in one round trip.

        Preferences that do not exist come back with `exists: false` rather than
        failing the whole call, and are listed again under `missing`.
        """
        if not names:
            raise UsageError("names was empty — pass at least one preference name.")
        if len(names) > 100:
            raise UsageError(
                "pref_get_many takes at most 100 names; use pref_list with a prefix "
                "for anything broader."
            )
        result = await call("x.prefs.getMany", {"names": [_clean_name(n) for n in names]})
        prefs = result.get("prefs", [])
        return page(prefs, missing=[p.get("name") for p in prefs if not p.get("exists")])

    @reg.read_tool(title="List preferences by prefix", meta=large_output())
    async def pref_list(
        prefix: str = "",
        only_user_set: bool = False,
        limit: int = 200,
    ) -> dict[str, Any]:
        """List preferences under a branch, e.g. `prefix="mail.biff."`.

        A bare prefix matches over five thousand preferences on a normal profile, so
        pass a branch and keep `limit` modest. `only_user_set=true` narrows it to what
        differs from the shipped defaults.
        """
        result = await call(
            "x.prefs.list",
            {
                "prefix": prefix,
                "onlyUserSet": only_user_set,
                "limit": clamp(limit, default=200, minimum=1, maximum=2000, field="limit"),
            },
            timeout=60.0,
        )
        return page(
            result.get("prefs", []),
            total=result.get("matched"),
            truncated=bool(result.get("omitted")),
            prefix=result.get("prefix", prefix),
            omitted=result.get("omitted", 0),
        )

    @reg.read_tool(title="List changed preferences", meta=large_output())
    async def pref_user_set(prefix: str = "") -> dict[str, Any]:
        """Everything the user has changed from the shipped defaults.

        The honest answer to "what is my Thunderbird configured like": a normal profile
        has a few hundred of these, dominated by per-account `mail.server.*` and
        `mail.identity.*` entries. Anything that looks like a credential is redacted
        before it leaves Thunderbird.
        """
        result = await call("x.prefs.userSet", {"prefix": prefix}, timeout=60.0)
        return page(result.get("prefs", []), total=result.get("count"), prefix=prefix)

    @reg.read_tool(title="Describe common settings", meta=large_output())
    async def settings_describe(
        area: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        """Map a human request onto the preference that controls it.

        Consult this instead of guessing a preference name. It costs no round trip to
        Thunderbird, so it is cheap to call first. Value meanings are for Thunderbird
        153; current values are not included because they are per-profile — read one
        with `pref_get`. `writable` reflects this server's allowlist, not Thunderbird's
        own locking.
        """
        settings = current_settings()
        needle = (search or "").strip().lower()
        wanted_area = (area or "").strip().lower()

        groups: list[dict[str, Any]] = []
        for group in _SETTINGS_MAP:
            if wanted_area and wanted_area not in group["area"].lower():
                continue
            entries: list[dict[str, Any]] = []
            for entry in group["settings"]:
                if needle and needle not in f"{entry['pref']} {entry['what']}".lower():
                    continue
                allowed, reason = settings.pref_writable(entry["pref"])
                described: dict[str, Any] = {**entry, "writable": allowed}
                if not allowed:
                    described["whyNotWritable"] = reason
                entries.append(described)
            if entries:
                groups.append({**group, "settings": entries})

        known = [group["area"] for group in _SETTINGS_MAP]
        if not groups:
            raise UsageError(
                "Nothing matched. Areas are: "
                + "; ".join(known)
                + ". Drop `area`/`search` for the whole map, or use pref_list with a "
                "prefix to explore preferences this map does not cover."
            )
        return page(
            groups,
            settingsCount=sum(len(group["settings"]) for group in groups),
            areas=known,
            note="Curated, not exhaustive. pref_get(name) is the way to see a current "
            "value; pref_list(prefix=...) finds names this map omits.",
        )

    register_write(reg)

    register_junk(reg)
