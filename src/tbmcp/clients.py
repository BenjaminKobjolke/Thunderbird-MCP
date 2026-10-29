"""Registering this server with the MCP clients people actually use.

Every client keeps stdio servers in a different file, under a different key, with a
different idea of what a command is. The differences that matter here are all failure
modes hit on a real Windows box:

- the command must be an **absolute path to a real executable**. Node-based clients
  spawn it with no shell, so a bare name or a `.cmd` shim dies with `spawn ENOENT`.
  Wrapping it in `cmd /c` is worse: the pipes are not inherited cleanly and a clear
  error turns into a startup timeout.
- `PYTHONUTF8` and `PYTHONUNBUFFERED` go into every per-server `env`. Codex does not
  hand its own environment to stdio servers (it assembles a fixed core set), and
  Claude Code ignores `settings.json` `env` for MCP children — so the only reliable
  channel is the server entry itself.
- Codex ignores tool annotations and `_meta`, so its config file is the only place
  the safety model can be expressed there at all; hence the approval modes below.

Writes are idempotent — read, build the wanted entry, compare, write only on a real
difference — backed up once per file and swapped in with `os.replace`. Where a config
is really JSONC (VS Code and Zed both ship theirs full of comments) we refuse to
reserialise it and print the block for the user to paste instead. Losing someone's
comments to save them one paste is not a trade worth making.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import client_codex, client_json_writers, client_launcher
from .client_model import CHILD_ENV, CLIENTS, SERVER_NAME, Ctx, Outcome, SetupError
from .config import Settings


@dataclass
class _Plan:
    command: str
    args: list[str]
    outcomes: list[Outcome] = field(default_factory=list)


def _print_report(plan: _Plan, ctx: Ctx) -> None:
    print("thunderbird-mcp setup\n")
    print(f"  command  {plan.command}")
    print(f"  args     {' '.join(plan.args)}")
    print(f"  env      {' '.join(f'{k}={v}' for k, v in CHILD_ENV.items())}")
    print(f"  scope    {ctx.scope}")
    if ctx.preview:
        print("\n  --- nothing was written ---")
    print()

    width = max((len(o.client) for o in plan.outcomes), default=12)
    for outcome in plan.outcomes:
        print(f"  {outcome.client:<{width}}  {outcome.label:<12}  {outcome.target}")
        if outcome.detail:
            print(f"  {'':<{width}}  {'':<12}  {outcome.detail}")
        if outcome.block and (outcome.planned or outcome.status in ("skipped", "failed")):
            for line in outcome.block.splitlines():
                print(f"        {line}" if line.strip() else "")
            print()

    touched = {o.client for o in plan.outcomes if o.status in ("added", "updated")}
    if not ctx.preview and touched:
        print("\nVerify:")
        if "claude-code" in touched:
            print(f"  claude mcp get {SERVER_NAME}")
        if "codex" in touched:
            print(f"  codex mcp get {SERVER_NAME} --json")
        print("  tbmcp doctor")
    if any(o.status == "skipped" for o in plan.outcomes):
        print("\nSkipped configs were left untouched. Paste the block above into each.")
    if "--yolo" in plan.args:
        print(
            "\nThis registration includes --yolo, so every confirmation gate is off "
            "for these clients. Re-run setup without it to put them back."
        )
    if plan.args[:2] == ["-m", "tbmcp"]:
        print(
            "\nNo `thunderbird-mcp` executable was found, so this registers "
            f"`{Path(plan.command).name} -m tbmcp` instead. `uv tool install "
            "thunderbird-mcp` (or pipx) gives you a launcher that does not depend on "
            "this interpreter staying put."
        )


def installed_clients() -> list[str]:
    """Which of `CLIENTS` actually look present on this machine, in `CLIENTS` order.

    "Present" is judged from the same locations the writers themselves use, on the
    theory that whatever the writer would touch is also good evidence the client
    exists — no new paths are invented here. `claude-code`, `codex`, and `vscode`
    each have a CLI that their own writer treats as the *primary* way to register
    (`_claude_code`, `_codex`, and `_vscode` all try `claude`/`codex`/`code` before
    ever touching a config file), so detection trusts that same CLI being on PATH:
    all three write their config lazily, on first use, and a fresh install with the
    CLI present but never yet used would otherwise read as absent. Every other
    client here is a GUI app whose writer only ever touches a config path, with no
    CLI fallback to mirror — for those, that file or its parent directory existing
    is the best signal available without something OS-specific like querying an app
    registry.
    """
    checks: dict[str, bool] = {
        "claude-code": shutil.which("claude") is not None
        or client_json_writers._claude_code_path().exists(),
        "codex": shutil.which("codex") is not None or client_codex._codex_home().exists(),
        "claude-desktop": client_json_writers._claude_desktop_path().exists()
        or client_json_writers._claude_desktop_path().parent.exists(),
        "cursor": client_json_writers._cursor_path().exists()
        or client_json_writers._cursor_path().parent.exists(),
        "vscode": (
            shutil.which("code") is not None
            or client_json_writers._vscode_user_path().exists()
            or client_json_writers._vscode_user_path().parent.exists()
        ),
        "gemini": client_json_writers._gemini_path().exists()
        or client_json_writers._gemini_path().parent.exists(),
        "zed": client_json_writers._zed_path().exists()
        or client_json_writers._zed_path().parent.exists(),
    }
    return [client for client in CLIENTS if checks[client]]


def resolve_clients(requested: Sequence[str] | None) -> list[str]:
    """Expand `all`, drop duplicates, and reject anything we do not know."""
    names = [str(name).strip().lower() for name in (requested or []) if str(name).strip()]
    if not names:
        names = ["claude-code", "codex"]
    unknown = [name for name in names if name not in CLIENTS and name != "all"]
    if unknown:
        raise SystemExit(
            f"unknown client {', '.join(repr(u) for u in unknown)}; "
            f"choose from: {', '.join(CLIENTS)}, all"
        )
    if "all" in names:
        return list(CLIENTS)
    return [name for name in CLIENTS if name in names]


def run_setup(
    clients: Sequence[str] | None = None,
    scope: str = "user",
    dry_run: bool = False,
    print_only: bool = False,
    settings: Settings | None = None,
) -> int:
    """Register this server with each named client. Returns a process exit code."""
    settings = settings or Settings()
    ctx = Ctx(scope=scope, dry_run=dry_run, print_only=print_only)
    command, args = client_launcher.server_command(settings)
    plan = _Plan(command=command, args=args)

    for client in resolve_clients(clients):
        entry = client_launcher._entry_for(client, command, args)
        try:
            if client == "claude-code":
                outcome = client_json_writers._claude_code(entry, ctx)
            elif client == "codex":
                outcome = client_codex._codex(command, args, ctx)
            elif client == "claude-desktop":
                outcome = client_json_writers._claude_desktop(entry, ctx)
            elif client == "cursor":
                outcome = client_json_writers._cursor(entry, ctx)
            elif client == "vscode":
                outcome = client_json_writers._vscode(entry, ctx)
            elif client == "gemini":
                outcome = client_json_writers._gemini(entry, ctx)
            else:
                outcome = client_json_writers._zed(entry, ctx)
        except SetupError as exc:
            outcome = Outcome(client, "failed", detail=str(exc))
        plan.outcomes.append(outcome)

    _print_report(plan, ctx)
    if ctx.preview:
        return 0
    return 1 if any(o.status == "failed" for o in plan.outcomes) else 0
