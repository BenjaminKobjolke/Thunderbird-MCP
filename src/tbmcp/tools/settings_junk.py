"""Junk filter and OpenPGP key tools."""

from typing import Any, Literal

from ..errors import UsageError
from ..safety import IDEMPOTENT_WRITE, MUTATING, Gate, guard_write, require
from ..server import Registrar
from ._common import call, changed, dry_run, one_of, page


def register_junk(reg: Registrar) -> None:

    @reg.read_tool(title="Get junk filter settings")
    async def junk_get() -> dict[str, Any]:
        """Read the global junk (bayesian) filter settings.

        `userHasClassified` is the one to check first: an untrained filter scores
        nothing usefully, however the rest is configured. Per-account junk behaviour —
        which folder spam moves to, whitelists, purge age — lives in `account_get_junk`.
        """
        result = await call("x.junk.getSettings", timeout=30.0)
        return {
            **result,
            "note": "Per-account junk handling is separate; see account_get_junk.",
        }

    @reg.write_tool(title="Change junk filter settings", annotations=IDEMPOTENT_WRITE)
    async def junk_set(
        manual_mark: bool | None = None,
        manual_mark_mode: Literal["move", "delete"] | None = None,
        mark_as_read_on_spam: bool | None = None,
        logging_enabled: bool | None = None,
        confirm: bool = False,
        consent: Gate("change Thunderbird's junk filter settings") = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Change the global junk filter settings. Only the fields you pass are touched.

        `manual_mark_mode="delete"` makes marking a message as junk delete it outright,
        which is a real data-loss setting — prefer `"move"` unless the user asks for
        deletion by name.
        """
        guard_write("change junk settings")
        payload: dict[str, Any] = {}
        for key, value in (
            ("manualMark", manual_mark),
            ("markAsReadOnSpam", mark_as_read_on_spam),
            ("loggingEnabled", logging_enabled),
        ):
            if value is not None:
                payload[key] = value
        if manual_mark_mode is not None:
            payload["manualMarkMode"] = one_of(
                manual_mark_mode, ("move", "delete"), field="manual_mark_mode", default="move"
            )
        if not payload:
            raise UsageError(
                "Nothing to change — pass manual_mark, manual_mark_mode, "
                "mark_as_read_on_spam or logging_enabled."
            )
        require(consent, "change junk settings")
        result = await call("x.junk.setSettings", payload, timeout=30.0)
        return changed("junk.settings", before=result.get("previous"), after=result.get("current"))

    @reg.write_tool(title="Train the junk filter", annotations=MUTATING)
    async def junk_train(
        header_message_ids: list[str],
        classification: Literal["junk", "good"],
        confirm: bool = False,
        consent: Gate("train Thunderbird's junk filter on these messages") = None,  # type: ignore[valid-type]
        dry_run_only: bool = False,
    ) -> dict[str, Any]:
        """Teach the junk filter that these messages are junk, or are not.

        Ids are RFC 5322 Message-ID header values (`headerMessageId` from `mail_get`),
        not the integer ids from `mail_search`: those are only valid while Thunderbird
        stays open, and training data outlives the session. Training is cumulative and
        biased corpora are hard to undo, so train on messages the user has actually
        judged rather than on a whole search result.
        """
        guard_write("train the junk filter")
        ids = [str(i).strip() for i in (header_message_ids or []) if str(i).strip()]
        if not ids:
            raise UsageError(
                "header_message_ids was empty — pass Message-ID header values, which "
                "mail_get returns as headerMessageId."
            )
        if len(ids) > 200:
            raise UsageError("junk_train takes at most 200 ids per call; batch them.")
        kind = one_of(classification, ("junk", "good"), field="classification", default="junk")
        if dry_run_only:
            return dry_run("junk.train", {"headerMessageIds": ids, "classification": kind})
        require(consent, "train the junk filter")
        result = await call(
            "x.junk.train",
            {"headerMessageIds": ids, "classification": kind},
            timeout=180.0,
        )
        return {
            "trained": result.get("trained", len(ids)),
            "classification": kind,
            "failures": result.get("failures") or [],
            "userHasClassified": result.get("userHasClassified"),
        }

    # ---------------------------------------------------------------------- openpgp

    @reg.read_tool(title="List OpenPGP keys")
    async def openpgp_list_keys(
        search: str | None = None,
        only_secret: bool = False,
    ) -> dict[str, Any]:
        """List the OpenPGP keys in Thunderbird's keyring.

        Metadata only — fingerprints, user ids, validity, expiry, and whether a secret
        key is present. Private key material is never exported through this server, and
        no tool here can export it; a user who wants a backup should use Thunderbird's
        own End-to-End Encryption settings. `search` matches an email address, key id or
        fingerprint; `only_secret=true` narrows to keys this user can sign with.
        """
        result = await call(
            "x.openpgp.listKeys",
            {"search": search, "onlySecret": only_secret},
            timeout=60.0,
        )
        return page(
            result.get("keys", []),
            total=result.get("totalAvailable"),
            secretKeysNote="Private key material is not exported.",
        )
