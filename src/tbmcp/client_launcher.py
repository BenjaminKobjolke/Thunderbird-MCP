"""Find and describe the command a desktop client should launch."""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .client_model import CHILD_ENV
from .config import ALL_TOOLSETS, DEFAULT_TOOLSETS, Settings


def _probe_exit(argv: list[str]) -> int:
    done = subprocess.run(argv, capture_output=True, timeout=2)
    return done.returncode


def _runnable(path: Path) -> bool:
    """Does this launcher actually start?

    `is_file()` is not enough. Windows Application Control blocks pip's generated
    `.exe` shims, and the failure only shows up in the client as a timeout with no
    stated cause. Two seconds of `--help` here buys a config that works.
    """
    try:
        return _probe_exit([str(path), "--help"]) == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _console_script() -> Path | None:
    """Absolute path to the installed `thunderbird-mcp` launcher, if there is one.

    A `.cmd`/`.bat` shim is deliberately rejected: `shutil.which` will happily find
    one, but the clients that spawn without a shell cannot execute it. Falling back
    to `python -m tbmcp` is slower to type and always works.
    """
    candidates: list[Path] = []
    found = shutil.which("thunderbird-mcp")
    if found:
        candidates.append(Path(found))
    # A venv or `uv tool` install is frequently not on the PATH of the shell that
    # happens to be running setup, so look beside the interpreter too.
    here = Path(sys.executable).resolve().parent
    candidates += [
        here / "thunderbird-mcp.exe",
        here / "thunderbird-mcp",
        here / "Scripts" / "thunderbird-mcp.exe",
        here / "bin" / "thunderbird-mcp",
    ]
    for path in candidates:
        if path.is_file() and path.suffix.lower() not in (".cmd", ".bat", ".ps1"):
            resolved = path.resolve()
            if _runnable(resolved):
                return resolved
    return None


def _serve_args(settings: Settings) -> list[str]:
    """The flags that reproduce this invocation's settings in a spawned server.

    Baked into `args` rather than `env` on purpose: `args` is the one channel every
    client passes through untouched.
    """
    args = ["serve"]
    toolsets = tuple(settings.toolsets)
    if toolsets != DEFAULT_TOOLSETS:
        args += ["--toolsets", "all" if toolsets == ALL_TOOLSETS else ",".join(toolsets)]
    if settings.read_only:
        args.append("--read-only")
    if settings.send_mode == "send":
        args.append("--send")
    if settings.unsafe_prefs:
        args.append("--unsafe-prefs")
    if settings.yolo:
        # Carried through because it was asked for explicitly, and called out in the
        # summary: a registration that quietly skips every gate is worth noticing.
        args.append("--yolo")
    if settings.profile:
        args += ["--profile", settings.profile]
    if abs(settings.default_timeout - 30.0) > 0.01:
        args += ["--timeout", f"{settings.default_timeout:g}"]
    return args


def server_command(settings: Settings) -> tuple[str, list[str]]:
    """`(command, args)` as it must appear in a client config."""
    script = _console_script()
    serve = _serve_args(settings)
    if script is not None:
        return str(script), serve
    return str(Path(sys.executable).resolve()), ["-m", "tbmcp", *serve]


def _entry_for(client: str, command: str, args: Sequence[str]) -> dict[str, Any]:
    """The per-client server entry. Key order is the order it lands in the file."""
    entry: dict[str, Any] = {}
    if client in ("claude-code", "vscode"):
        entry["type"] = "stdio"
    if client == "zed":
        # A hand-written Zed entry without this is silently ignored — it is how Zed
        # tells its own extension-provided servers from yours.
        entry["source"] = "custom"
    entry["command"] = command
    entry["args"] = list(args)
    entry["env"] = dict(CHILD_ENV)
    return entry
