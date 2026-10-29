"""CLI command handlers and logging setup."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

from .config import Settings, parse_toolsets

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        stream=sys.stderr,
        format=LOG_FORMAT,
    )
    # websockets logs every frame at DEBUG; that is never what we want.
    logging.getLogger("websockets").setLevel(logging.WARNING)


def _attach_log_file(path) -> None:
    """Also write the log to `path`, so the daemon's side of a failure survives.

    The daemon is spawned detached with all three streams on DEVNULL, which meant
    everything it logged — a handshake that never completed, a stand-down, the
    reason it exited — was discarded as it was written, and the only account of a
    broken bridge was whatever the add-on managed to leave in the profile.

    Called after `_configure_logging`, which sets the root level: a handler added
    first would also stop `basicConfig` configuring stderr at all.
    """
    from logging.handlers import RotatingFileHandler

    from . import ipc

    try:
        handler = RotatingFileHandler(path, maxBytes=512_000, backupCount=2, encoding="utf-8")
    except OSError as exc:
        # A log we cannot open is not a reason to refuse to run.
        logging.getLogger("tbmcp").warning("cannot write the daemon log %s: %s", path, exc)
        return
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logging.getLogger().addHandler(handler)
    ipc._restrict_permissions(path)


def _settings_from_args(args: argparse.Namespace) -> Settings:
    config = getattr(args, "config", None)
    settings = Settings.from_env(Path(config) if config is not None else None)
    toolsets = parse_toolsets(args.toolsets) if getattr(args, "toolsets", None) else None
    return settings.merged_with(
        toolsets=toolsets,
        read_only=True if getattr(args, "read_only", False) else None,
        yolo=True if getattr(args, "yolo", False) else None,
        unsafe_prefs=True if getattr(args, "unsafe_prefs", False) else None,
        send_mode="send" if getattr(args, "send", False) else None,
        profile=getattr(args, "profile", None),
        default_timeout=getattr(args, "timeout", None),
    )


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import build_server

    settings = _settings_from_args(args)
    mcp = build_server(settings)
    if args.http:
        mcp.run(
            transport="streamable-http",
            host=args.host,
            port=args.port,
            streamable_http_path=args.path,
        )
    else:
        mcp.run(transport="stdio")
    return 0


def cmd_daemon(args: argparse.Namespace) -> int:
    from .daemon_run import run_daemon
    from .ipc import daemon_log_path

    _attach_log_file(daemon_log_path())
    return asyncio.run(run_daemon(args.profile, idle_timeout=args.idle_timeout, force=args.force))


def cmd_install_addon(args: argparse.Namespace) -> int:
    from . import addon_install
    from .profile import find_profile

    profile = find_profile(args.profile)
    if args.manual:
        _package, text = addon_install.manual_instructions()
        print(text)
        return 0

    if not args.yes:
        print(
            "This will close Thunderbird, install the bridge add-on through "
            "Thunderbird's own automation channel, and start it again.\n"
            "Nothing is sent anywhere and no mail is touched.\n"
            "Use --manual to get the package and install it by hand instead.",
            file=sys.stderr,
        )
        answer = input("Proceed? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Cancelled. `tbmcp install-addon --manual` builds the XPI only.")
            return 1

    outcome = addon_install.install_automatic(profile, restart_after=not args.no_restart)
    print(outcome.message)
    if not outcome.ok:
        _print_manual_install_fallback(addon_install, outcome)
        return 1
    return 0


def _print_manual_install_fallback(addon_install, outcome) -> None:
    _package, text = addon_install.manual_instructions(outcome.xpi)
    print("\nAutomatic install did not work. Do it by hand:\n", file=sys.stderr)
    print(text, file=sys.stderr)


def cmd_refresh(args: argparse.Namespace) -> int:
    """Stop stale Python code and update the add-on only when its build changed."""
    from . import addon_build, addon_install, ipc
    from .bridge import Bridge
    from .errors import TransportError
    from .profile import find_profile

    bridge = Bridge(profile_hint=args.profile, autostart=False)

    async def refresh_daemon() -> None:
        try:
            status = await bridge.status()
        except TransportError as exc:
            # Anything else (a rejected token, an unreadable frame) is a daemon that
            # may well be running stale code, not an absent one.
            if exc.code != "NO_DAEMON":
                raise
            print("no daemon running")
            return
        uptime = (status.get("daemon") or {}).get("uptimeSeconds", 0)
        if ipc.newest_source_mtime() > time.time() - uptime:
            try:
                await bridge.call("daemon.shutdown")
            except TransportError as exc:
                # The daemon hanging up before it answers is the shutdown working.
                # `Bridge.call` reconnects once after such a drop, and with autostart
                # off that retry reports `NO_DAEMON` — the same outcome. Any other
                # code does not establish that it stopped.
                if exc.code not in ("DISCONNECTED", "NO_DAEMON"):
                    raise
            print("daemon code is stale; the next call starts a fresh one")
        else:
            print("daemon is current")

    try:
        asyncio.run(refresh_daemon())
    finally:
        asyncio.run(bridge.close())

    profile = find_profile(args.profile)
    if profile is not None:
        build = addon_build.build_check(profile.path)
        if build["installed"] == build["source"]:
            print(f"add-on build `{build['source']}` is current, Thunderbird left alone")
            return 0

    from .thunderbird_process import is_running

    outcome = addon_install.install_automatic(profile, restart_after=is_running())
    print(outcome.message)
    if not outcome.ok:
        _print_manual_install_fallback(addon_install, outcome)
        return 1
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    from .clients import run_setup

    return run_setup(
        clients=args.client,
        scope=args.scope,
        dry_run=args.dry_run,
        print_only=args.print_config,
        settings=_settings_from_args(args),
    )


def cmd_detect_clients(args: argparse.Namespace) -> int:
    """A machine-readable list of installed clients — for `bootstrap` to shell out to.

    `bootstrap.py` may not import from this package at all, so it cannot call
    `clients.installed_clients()` directly; it spawns the venv's interpreter and
    reads this command's stdout instead. Plain `print(json.dumps(...))` keeps that
    parse trivial.
    """
    from .clients import installed_clients

    print(json.dumps(installed_clients()))
    return 0


def cmd_tools(args: argparse.Namespace) -> int:
    """List the tools that would be registered — useful when writing allowlists."""
    from .server import build_server

    settings = _settings_from_args(args)
    mcp = build_server(settings)

    async def dump() -> list[dict]:
        listed = await mcp.list_tools()
        return [
            {
                "name": tool.name,
                "title": getattr(tool, "title", None),
                "readOnly": bool(
                    tool.annotations and getattr(tool.annotations, "read_only_hint", False)
                ),
                "destructive": bool(
                    tool.annotations and getattr(tool.annotations, "destructive_hint", False)
                ),
            }
            for tool in listed
        ]

    rows = asyncio.run(dump())
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    for row in rows:
        marker = "r" if row["readOnly"] else ("!" if row["destructive"] else "w")
        print(f"  [{marker}] {row['name']:<34} {row['title'] or ''}")
    print(f"\n{len(rows)} tools from toolsets: {','.join(settings.toolsets)}")
    return 0


def cmd_bootstrap(args: argparse.Namespace) -> int:
    from .bootstrap import main as bootstrap_main

    forwarded: list[str] = []
    for flag in ("python", "venv", "clients", "toolsets", "source"):
        value = getattr(args, flag, None)
        if value:
            forwarded += [f"--{flag}", str(value)]
    for flag in ("skip_addon", "dry_run", "json"):
        if getattr(args, flag, False):
            forwarded.append("--" + flag.replace("_", "-"))
    return bootstrap_main(forwarded)
