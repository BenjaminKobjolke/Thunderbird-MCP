"""Write each non-Codex client's server entry in its native config format."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from .client_config_files import _apply_json, _backup, _dump_json, _peek_json, _run, _with_backup
from .client_model import SERVER_NAME, Ctx, Outcome


def _claude_code_path() -> Path:
    return Path.home() / ".claude.json"


def _claude_code(entry: dict[str, Any], ctx: Ctx) -> Outcome:
    """`claude mcp add-json` for user scope; `.mcp.json` for project scope.

    `~/.claude.json` is never hand-edited: it carries OAuth state and the per-project
    trust decisions, and a well-meaning reserialisation there is how people lose their
    logins. We read it only to stay idempotent and to verify afterwards.
    """
    if ctx.scope == "project":
        return _apply_json(
            "claude-code",
            Path.cwd() / ".mcp.json",
            "mcpServers",
            entry,
            ctx,
            note="checked in with the project",
        )

    path = _claude_code_path()
    current = (_peek_json(path).get("mcpServers") or {}).get(SERVER_NAME)
    payload = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
    command = f"claude mcp add-json {SERVER_NAME} '{payload}' --scope user"

    if current == entry:
        return Outcome("claude-code", "unchanged", str(path))
    status = "updated" if isinstance(current, dict) else "added"
    if ctx.preview:
        return Outcome("claude-code", status, str(path), "via the CLI:", command, planned=True)

    claude = shutil.which("claude")
    if claude is None:
        return Outcome(
            "claude-code",
            "skipped",
            str(path),
            "the `claude` CLI is not on PATH, and this file is not safe to edit. Run:",
            command,
        )

    backup = _backup(path)
    code, output = _run([claude, "mcp", "add-json", SERVER_NAME, payload, "--scope", "user"])
    if code != 0 and isinstance(current, dict):
        # add-json refuses a name that already exists, so replace it in two steps.
        _run([claude, "mcp", "remove", SERVER_NAME, "--scope", "user"])
        code, output = _run([claude, "mcp", "add-json", SERVER_NAME, payload, "--scope", "user"])
    # Trust the file, not the exit code: on Windows `claude` is often a batch shim,
    # and cmd.exe can mangle the quoting of a JSON argument on the way through.
    written = (_peek_json(path).get("mcpServers") or {}).get(SERVER_NAME)
    if written != entry:
        return Outcome(
            "claude-code",
            "failed",
            str(path),
            f"`claude mcp add-json` did not take ({output or f'exit {code}'}). Run this yourself:",
            command,
        )
    return Outcome("claude-code", status, str(path), _with_backup("via claude CLI", backup))


def _app_data(*parts: str) -> Path:
    """Per-user config directory for a GUI app, per platform."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base.joinpath(*parts)


def _claude_desktop_path() -> Path:
    return _app_data("Claude", "claude_desktop_config.json")


def _claude_desktop(entry: dict[str, Any], ctx: Ctx) -> Outcome:
    path = _claude_desktop_path()
    note = "" if ctx.scope == "user" else "no project scope here; wrote the user config"
    return _apply_json("claude-desktop", path, "mcpServers", entry, ctx, note=note)


def _cursor_path() -> Path:
    return Path.home() / ".cursor" / "mcp.json"


def _cursor(entry: dict[str, Any], ctx: Ctx) -> Outcome:
    path = Path.cwd() / ".cursor" / "mcp.json" if ctx.scope == "project" else _cursor_path()
    return _apply_json("cursor", path, "mcpServers", entry, ctx)


def _gemini_path() -> Path:
    return Path.home() / ".gemini" / "settings.json"


def _gemini(entry: dict[str, Any], ctx: Ctx) -> Outcome:
    path = Path.cwd() / ".gemini" / "settings.json" if ctx.scope == "project" else _gemini_path()
    return _apply_json("gemini", path, "mcpServers", entry, ctx)


def _zed_path() -> Path:
    return (
        _app_data("Zed", "settings.json")
        if sys.platform == "win32"
        else Path.home() / ".config" / "zed" / "settings.json"
    )


def _zed(entry: dict[str, Any], ctx: Ctx) -> Outcome:
    """Zed's `context_servers`, which usually ends in a paste.

    Zed ships `settings.json` as a commented template and has no CLI for this, so the
    comment guard nearly always fires. That is the right outcome, not a shortcoming.
    """
    path = _zed_path()
    note = "" if ctx.scope == "user" else "context_servers is user-wide; wrote the user config"
    return _apply_json("zed", path, "context_servers", entry, ctx, note=note)


def _vscode_user_path() -> Path:
    return _app_data("Code", "User", "mcp.json")


def _vscode(entry: dict[str, Any], ctx: Ctx) -> Outcome:
    """`.vscode/mcp.json` for a project; `code --add-mcp` for the user profile."""
    if ctx.scope == "project":
        return _apply_json("vscode", Path.cwd() / ".vscode" / "mcp.json", "servers", entry, ctx)

    path = _vscode_user_path()
    payload = json.dumps({"name": SERVER_NAME, **entry}, ensure_ascii=False, separators=(",", ":"))
    current = (_peek_json(path).get("servers") or {}).get(SERVER_NAME)
    if current == entry:
        return Outcome("vscode", "unchanged", str(path))
    status = "updated" if isinstance(current, dict) else "added"
    block = _dump_json({"servers": {SERVER_NAME: entry}})
    if ctx.preview:
        return Outcome("vscode", status, str(path), "", block, planned=True)

    code = shutil.which("code")
    if code is not None:
        backup = _backup(path)
        exit_code, output = _run([code, "--add-mcp", payload])
        if (_peek_json(path).get("servers") or {}).get(SERVER_NAME) == entry:
            return Outcome("vscode", status, str(path), _with_backup("via code CLI", backup))
        detail = f"`code --add-mcp` did not take ({output or f'exit {exit_code}'})"
    else:
        detail = "the `code` CLI is not on PATH"

    # Fall back to the file, but only while it is plain JSON — VS Code writes JSONC.
    outcome = _apply_json("vscode", path, "servers", entry, ctx)
    if outcome.status == "skipped":
        outcome.detail = f"{detail}, and the file {outcome.detail}"
    return outcome
