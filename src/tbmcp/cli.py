"""Command line entry point.

`tbmcp` with no arguments serves MCP over stdio, because that is how an MCP client
spawns it. Everything else is a subcommand.

Nothing here may write to stdout before the transport takes over — a stray byte
looks like a protocol violation to the client. Logging goes to stderr, always.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence

from .cli_commands import (
    _configure_logging,
    cmd_bootstrap,
    cmd_daemon,
    cmd_detect_clients,
    cmd_install_addon,
    cmd_refresh,
    cmd_serve,
    cmd_setup,
    cmd_tools,
)
from .cli_doctor import cmd_doctor
from .config import ALL_TOOLSETS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tbmcp",
        description="MCP server for Thunderbird. With no subcommand, serves over stdio.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging on stderr")
    subparsers = parser.add_subparsers(dest="command")

    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--profile", help="Thunderbird profile name or directory")
        sub.add_argument(
            "--toolsets",
            help=f"comma separated: {','.join(ALL_TOOLSETS)}, all, or +extra (default: lean set)",
        )
        sub.add_argument("--read-only", action="store_true", help="register no mutating tools")
        sub.add_argument(
            "--yolo", action="store_true", help="skip every confirmation gate (dangerous)"
        )
        sub.add_argument(
            "--unsafe-prefs",
            action="store_true",
            help="allow preference writes outside the reviewed allowlist",
        )
        sub.add_argument(
            "--send",
            action="store_true",
            help="let mail_send actually send instead of drafting by default",
        )
        sub.add_argument("--timeout", type=float, help="per-call timeout in seconds (default 30)")
        sub.add_argument("--config", help="folder rules file (default: <repo>/settings.json)")

    serve = subparsers.add_parser("serve", help="run the MCP server (default)")
    add_common(serve)
    serve.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--path", default="/mcp")
    serve.set_defaults(func=cmd_serve)

    daemon = subparsers.add_parser("daemon", help="run the broker that Thunderbird connects to")
    daemon.add_argument("--profile")
    daemon.add_argument(
        "--idle-timeout",
        type=float,
        default=900.0,
        help="exit after this many seconds with no MCP clients (0 disables)",
    )
    daemon.add_argument(
        "--force",
        action="store_true",
        help="start even when another daemon is running (it will then stand down)",
    )
    daemon.set_defaults(func=cmd_daemon)

    install = subparsers.add_parser("install-addon", help="build and install the bridge add-on")
    install.add_argument("--profile")
    install.add_argument("--manual", action="store_true", help="only build it and print the steps")
    install.add_argument("--yes", "-y", action="store_true", help="do not ask before restarting")
    install.add_argument(
        "--no-restart", action="store_true", help="leave Thunderbird closed afterwards"
    )
    install.set_defaults(func=cmd_install_addon)

    refresh = subparsers.add_parser(
        "refresh", help="restart stale daemon code and update the add-on if needed"
    )
    refresh.add_argument("--profile")
    refresh.set_defaults(func=cmd_refresh)

    doctor = subparsers.add_parser("doctor", help="diagnose the whole chain")
    add_common(doctor)
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--no-start", action="store_true", help="do not start the daemon")
    doctor.add_argument(
        "--wait",
        type=float,
        default=25.0,
        help="seconds to wait for the add-on to connect (default 25)",
    )
    doctor.set_defaults(func=cmd_doctor)

    setup = subparsers.add_parser("setup", help="register this server with an MCP client")
    add_common(setup)
    setup.add_argument(
        "client",
        nargs="*",
        default=["claude-code", "codex"],
        help="claude-code, codex, claude-desktop, cursor, vscode, gemini, zed, all",
    )
    setup.add_argument("--scope", choices=["user", "project"], default="user")
    setup.add_argument("--dry-run", action="store_true")
    setup.add_argument(
        "--print-config", action="store_true", help="print the blocks, write nothing"
    )
    setup.set_defaults(func=cmd_setup)

    detect = subparsers.add_parser(
        "detect-clients", help="list installed MCP clients on this machine, as JSON"
    )
    detect.set_defaults(func=cmd_detect_clients)

    tools = subparsers.add_parser("tools", help="list the tools that would be registered")
    add_common(tools)
    tools.add_argument("--json", action="store_true")
    tools.set_defaults(func=cmd_tools)

    boot = subparsers.add_parser("bootstrap", help="install, repair, register, verify")
    boot.add_argument("--python")
    boot.add_argument("--venv")
    boot.add_argument("--clients", help="comma separated; default: auto-detect installed clients")
    boot.add_argument("--toolsets")
    boot.add_argument("--source")
    boot.add_argument("--skip-addon", action="store_true")
    boot.add_argument("--dry-run", action="store_true")
    boot.add_argument("--json", action="store_true")
    boot.set_defaults(func=cmd_bootstrap)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    # Bare `tbmcp` is how an MCP client launches us.
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help", "-v", "--verbose")):
        argv = ["serve", *argv]
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args = parser.parse_args(["serve", *argv])

    _configure_logging(getattr(args, "verbose", False) or os.environ.get("TBMCP_DEBUG") == "1")
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        logging.getLogger("tbmcp").error("%s", exc)
        if getattr(args, "verbose", False):
            raise
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
