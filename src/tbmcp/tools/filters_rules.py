"""Rule vocabulary and validation for message filters."""

from typing import Any

from ..errors import UsageError

#: When a filter runs. `inbox` means on newly arrived mail, `manual` means when the
#: user (or `filter_run`) applies filters by hand.
RUN_WHEN = ("inbox", "manual", "postPlugin", "postOutgoing", "archive", "periodic")

ATTRIBUTES = (
    "subject",
    "from",
    "to",
    "cc",
    "toOrCc",
    "allAddresses",
    "body",
    "anyText",
    "date",
    "ageInDays",
    "size",
    "priority",
    "status",
    "hasAttachment",
    "tag",
    "junkStatus",
    "junkScore",
    "messageId",
    "otherHeader",
)
OPERATORS = (
    "contains",
    "doesntContain",
    "is",
    "isnt",
    "isEmpty",
    "isntEmpty",
    "beginsWith",
    "endsWith",
    "isBefore",
    "isAfter",
    "isHigherThan",
    "isLowerThan",
    "isGreaterThan",
    "isLessThan",
)
ACTIONS = (
    "moveToFolder",
    "copyToFolder",
    "markRead",
    "markUnread",
    "markFlagged",
    "markUnflagged",
    "addTag",
    "setPriority",
    "delete",
    "deleteFromServer",
    "fetchBodyFromPop3Server",
    "stopExecution",
    "junkScore",
    "forwardTo",
    "reply",
    "changePriority",
    "ignoreThread",
    "ignoreSubthread",
    "watchThread",
)


def _validate_terms(
    terms: list[dict[str, Any]] | None, *, required: bool
) -> list[dict[str, Any]] | None:
    if terms is None:
        if required:
            raise UsageError(
                "search_terms is required — a filter with no conditions would match "
                'every message. Each term is {"attribute": "from", "operator": '
                '"contains", "value": "bank.example"}.'
            )
        return None
    if not terms:
        raise UsageError("search_terms cannot be empty; omit it to leave it unchanged.")
    for index, term in enumerate(terms):
        if not isinstance(term, dict):
            raise UsageError(f"search_terms[{index}] must be an object.")
        if term.get("attribute") not in ATTRIBUTES:
            raise UsageError(
                f"search_terms[{index}].attribute must be one of: "
                f"{', '.join(ATTRIBUTES)}; got {term.get('attribute')!r}."
            )
        if term.get("operator") not in OPERATORS:
            raise UsageError(
                f"search_terms[{index}].operator must be one of: "
                f"{', '.join(OPERATORS)}; got {term.get('operator')!r}."
            )
        if term.get("operator") not in ("isEmpty", "isntEmpty") and term.get("value") in (None, ""):
            raise UsageError(f"search_terms[{index}].value is required for that operator.")
    return terms


def _validate_actions(
    actions: list[dict[str, Any]] | None, *, required: bool
) -> list[dict[str, Any]] | None:
    if actions is None:
        if required:
            raise UsageError(
                "actions is required — a filter that matches but does nothing is a "
                'no-op. Each action is {"type": "moveToFolder", "folderId": "..."}.'
            )
        return None
    if not actions:
        raise UsageError("actions cannot be empty; omit it to leave it unchanged.")
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            raise UsageError(f"actions[{index}] must be an object.")
        kind = action.get("type")
        if kind not in ACTIONS:
            raise UsageError(
                f"actions[{index}].type must be one of: {', '.join(ACTIONS)}; got {kind!r}."
            )
        if kind in ("moveToFolder", "copyToFolder") and not action.get("folderId"):
            raise UsageError(
                f"actions[{index}] is {kind}, so it needs folderId (from folder_list)."
            )
        if kind == "addTag" and not action.get("tag"):
            raise UsageError(f"actions[{index}] is addTag, so it needs tag (from mail_tags).")
        if kind in ("forwardTo", "reply") and not action.get("value"):
            raise UsageError(
                f"actions[{index}] is {kind}, so it needs value — an address to forward "
                "to, or the template message uri to reply with."
            )
    return actions


def _validate_run_when(run_when: list[str] | None) -> list[str] | None:
    if run_when is None:
        return None
    unknown = [item for item in run_when if item not in RUN_WHEN]
    if unknown:
        raise UsageError(f"run_when may contain {', '.join(RUN_WHEN)}; got {', '.join(unknown)}.")
    return run_when
