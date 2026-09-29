"""Folder-scoped permissions loaded from a user's TOML file."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import ipc

ACTIONS = ("create_subfolders", "rename_subfolders", "delete_subfolders", "move_in", "move_out")


@dataclass(frozen=True)
class FolderRule:
    path: str
    allow: frozenset[str]
    account: str | None = None


def load_rules(path: Path) -> tuple[FolderRule, ...]:
    if not path.exists():
        return ()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise SystemExit(f"{path}: {exc}") from exc
    entries = data.get("folders", [])
    if not isinstance(entries, list):
        raise SystemExit(f"{path}: folders must be an array of tables")
    rules = []
    for number, entry in enumerate(entries, 1):
        label = f"{path}: folders[{number}]"
        if not isinstance(entry, dict):
            raise SystemExit(f"{label}: expected a table")
        folder = entry.get("path")
        if not isinstance(folder, str) or not folder.strip():
            raise SystemExit(f"{label}: path must be a nonempty string")
        allowed = entry.get("allow")
        if not isinstance(allowed, list) or any(not isinstance(a, str) for a in allowed):
            raise SystemExit(f"{label}: allow must be a list of action names")
        unknown = set(allowed) - set(ACTIONS)
        if unknown:
            raise SystemExit(
                f"{label}: unknown allow action {sorted(unknown)[0]!r}; choose from: {', '.join(ACTIONS)}"
            )
        account = entry.get("account")
        if account is not None and (not isinstance(account, str) or not account.strip()):
            raise SystemExit(f"{label}: account must be a nonempty string")
        rules.append(FolderRule("/" + folder.strip().strip("/"), frozenset(allowed), account))
    return tuple(rules)


def grants(rules: tuple[FolderRule, ...], *actions: str) -> bool:
    return any(rule.allow.intersection(actions) for rule in rules)


def allows(
    rules: tuple[FolderRule, ...], action: str, folder: dict, *, strictly_below: bool = False
) -> bool:
    path = folder.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        return False
    return any(
        action in rule.allow
        and (rule.account is None or rule.account == folder.get("accountId"))
        and (not strictly_below or path != rule.path)
        and (path == rule.path or path.startswith(rule.path.rstrip("/") + "/"))
        for rule in rules
    )


def default_config_path() -> Path:
    return (
        Path(os.environ["TBMCP_CONFIG"])
        if os.environ.get("TBMCP_CONFIG")
        else ipc.state_dir() / "config.toml"
    )
