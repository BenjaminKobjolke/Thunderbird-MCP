"""Build and write Codex's TOML client configuration."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import tomlkit

from .client_config_files import _read, _with_backup, _write
from .client_model import (
    APPROVE_TOOLS,
    CHILD_ENV,
    CODEX_STARTUP_TIMEOUT,
    CODEX_TOOL_TIMEOUT,
    SERVER_NAME,
    Ctx,
    Outcome,
    SetupError,
)


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def _plain(value: Any) -> Any:
    """tomlkit item -> ordinary Python, for comparison."""
    unwrap = getattr(value, "unwrap", None)
    return unwrap() if callable(unwrap) else value


def _toml_value(value: Any) -> Any:
    """Convert to a TOML item, spelling Windows paths as literal strings.

    `"C:\\Users\\..."` is not a valid TOML basic string — `\\U` starts a unicode
    escape — so anything with a backslash goes in single quotes.
    """
    if isinstance(value, str):
        literal = "\\" in value and "'" not in value and "\n" not in value
        return tomlkit.string(value, literal=literal)
    if isinstance(value, (list, tuple)):
        array = tomlkit.array()
        for item in value:
            array.append(_toml_value(item))
        return array
    if isinstance(value, dict):
        inline = tomlkit.inline_table()
        for key, item in value.items():
            inline[key] = _toml_value(item)
        return inline
    return value


def _codex(command: str, args: Sequence[str], ctx: Ctx) -> Outcome:
    """`[mcp_servers.thunderbird]` in `config.toml`, edited through tomlkit.

    tomlkit keeps the rest of the document byte-for-byte — comments, key order,
    blank lines — which matters because `config.toml` is a file people write by hand.
    Only our own table is rebuilt.
    """
    if ctx.scope == "project":
        return Outcome(
            "codex",
            "skipped",
            str(_codex_home() / "config.toml"),
            "Codex has no per-project config; re-run with --scope user.",
        )

    path = _codex_home() / "config.toml"
    text = _read(path) or ""
    try:
        doc = tomlkit.parse(text)
    except Exception as exc:  # tomlkit raises a family of ParseError subclasses
        raise SetupError(
            f"{path} is not valid TOML ({exc}). Fix it, then run setup again."
        ) from exc

    servers = doc.get("mcp_servers")
    existing = _plain(servers.get(SERVER_NAME)) if isinstance(servers, dict) else None
    existing = existing if isinstance(existing, dict) else None

    tools = dict(existing.get("tools") or {}) if existing else {}
    tools.update({name: {"approval_mode": "approve"} for name in APPROVE_TOOLS})
    wanted: dict[str, Any] = {
        "command": command,
        "args": list(args),
        "env": dict(CHILD_ENV),
        "startup_timeout_sec": CODEX_STARTUP_TIMEOUT,
        "tool_timeout_sec": CODEX_TOOL_TIMEOUT,
    }
    if existing is None or "enabled" not in existing:
        wanted["enabled"] = True
    # Only these keys pass Codex's deny_unknown_fields check; anything else it sees
    # here makes it reject the whole file, not just this server.
    wanted["default_tools_approval_mode"] = "writes"
    wanted["tools"] = tools

    block = _codex_block(wanted)
    if existing is not None and all(existing.get(k) == v for k, v in wanted.items()):
        return Outcome("codex", "unchanged", str(path))
    status = "updated" if existing is not None else "added"
    if ctx.preview:
        return Outcome("codex", status, str(path), "", block, planned=True)

    # Rebuilt rather than patched key by key: a table's sub-tables are rendered after
    # its scalars, so adding a scalar to a table that already has
    # `[mcp_servers.thunderbird.tools.x]` would place the new key under that header —
    # silently reassigning it to the wrong table.
    table = tomlkit.table()
    for key, value in wanted.items():
        if key == "tools":
            continue
        table[key] = _toml_value(value)
    for key, value in (existing or {}).items():
        # Keep anything the user set that we do not manage, e.g. enabled = false.
        if key not in wanted:
            table[key] = _toml_value(value)
    table["tools"] = _codex_tools(tools)

    if not isinstance(doc.get("mcp_servers"), dict):
        doc["mcp_servers"] = tomlkit.table(True)
    doc["mcp_servers"][SERVER_NAME] = table  # type: ignore[index]
    backup = _write(path, tomlkit.dumps(doc))
    return Outcome("codex", status, str(path), _with_backup("", backup), "")


def _codex_tools(tools: dict[str, Any]) -> Any:
    """`[mcp_servers.thunderbird.tools.<name>]` sub-tables, in a stable order."""
    parent = tomlkit.table(True)
    for name in sorted(tools):
        child = tomlkit.table()
        for key, value in (tools[name] or {}).items():
            child[key] = _toml_value(value)
        parent[name] = child
    return parent


def _codex_block(wanted: dict[str, Any]) -> str:
    """The same table, rendered on its own, for `--print-config`."""
    doc = tomlkit.document()
    doc["mcp_servers"] = tomlkit.table(True)
    table = tomlkit.table()
    for key, value in wanted.items():
        if key != "tools":
            table[key] = _toml_value(value)
    table["tools"] = _codex_tools(wanted.get("tools") or {})
    doc["mcp_servers"][SERVER_NAME] = table  # type: ignore[index]
    return tomlkit.dumps(doc).strip()
