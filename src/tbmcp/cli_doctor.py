"""Doctor checks and command implementation."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .cli_commands import _settings_from_args
from .cli_doctor_report import _print_doctor
from .config import ALL_TOOLSETS


def _doctor_ok(report: dict) -> bool:
    """Whether `doctor` actually established a working chain — not just ran.

    Both the direct daemon status *and* the `tb_status` tool call (routed through the
    real MCP tool-calling machinery, the same path a client uses) have to say
    `connected`. Checking only one would let the other silently regress unnoticed.

    Exception: when the `admin` toolset was deselected, `tb_status` was never
    registered at all — `report["tbStatusCall"]` then carries `skipped`, not
    `connected` or `error`. That is a supported configuration, not a broken chain,
    so it must not sink `ok` the way a genuine dispatch failure would.
    """
    bridge_state = report.get("bridge")
    tool_call = report.get("tbStatusCall")
    bridge_ok = isinstance(bridge_state, dict) and bool(bridge_state.get("connected"))
    if isinstance(tool_call, dict) and tool_call.get("skipped"):
        return bridge_ok
    tool_ok = isinstance(tool_call, dict) and bool(tool_call.get("connected"))
    return bridge_ok and tool_ok


def _addon_version_check(report: dict) -> dict:
    """The add-on Thunderbird is running, against the one this package ships.

    Three sources: what the live session announced, what the add-on wrote into the
    profile at startup, and the manifest in this source tree. A connected older
    add-on is still a working chain — `_doctor_ok` deliberately ignores this — but
    it is the first thing to fix when anything else is wrong, because every other
    remedy assumes the two halves came out of the same package.
    """
    status = report.get("addonStatus")
    installed = None
    if isinstance(status, dict) and not status.get("error"):
        installed = status.get("addonVersion")
    live = ((report.get("bridge") or {}).get("thunderbird") or {}).get("addonVersion")
    source = (report.get("addon") or {}).get("addonVersion")
    running = live or installed
    build = report.get("addonBuild") or {}
    return {
        "installed": installed,
        "live": live,
        "source": source,
        "installedBuild": build.get("installed"),
        "sourceBuild": build.get("source"),
        "mismatch": bool(source and running and running != source) or bool(build.get("stale")),
    }


def cmd_doctor(args: argparse.Namespace) -> int:
    from . import addon_build, addon_install
    from .bridge import Bridge
    from .bridge_spawn import set_shared_bridge
    from .ipc import DaemonInfo, daemon_log_path
    from .profile import find_profile, list_profiles
    from .profile_data import ProfileSnapshot
    from .server import build_server

    settings = _settings_from_args(args)
    report: dict[str, object] = {"python": sys.version.split()[0], "executable": sys.executable}

    profiles = list_profiles()
    report["profilesFound"] = [
        {"name": p.name, "path": str(p.path), "default": p.is_default} for p in profiles
    ]
    profile = find_profile(settings.profile)
    report["profileSelected"] = str(profile.path) if profile else None
    report["profileSource"] = profile.source if profile else None
    if profile:
        report["addonBuild"] = addon_build.build_check(profile.path)
        snapshot = ProfileSnapshot.load(profile)
        report["accountsOnDisk"] = len(snapshot.accounts())
        report["outgoingServersOnDisk"] = len(snapshot.outgoing_servers())
        report["glodaDatabase"] = profile.gloda_db.is_file()
        report["bridgeFile"] = profile.bridge_file.is_file()
        # Written by the add-on at startup. Its absence, when the add-on is installed
        # and active, means the privileged half did not load.
        if profile.addon_status_file.is_file():
            try:
                report["addonStatus"] = json.loads(
                    profile.addon_status_file.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as exc:
                report["addonStatus"] = {"error": str(exc)}
        else:
            report["addonStatus"] = None

    report["addon"] = addon_install.summary()

    async def probe() -> None:
        bridge = Bridge(profile_hint=settings.profile, autostart=not args.no_start)
        try:
            # The add-on dials out, so give it a moment: on a cold start the daemon
            # has only just written the pairing file it is polling for.
            report["bridge"] = await bridge.require_thunderbird(wait=args.wait)
        except Exception as exc:
            try:
                report["bridge"] = await bridge.status()
            except Exception:
                report["bridge"] = {"error": str(exc)}

        # `report["bridge"]` above talks to the daemon directly. This instead goes
        # through `MCPServer.call_tool`, the exact dispatch a real client triggers —
        # tool lookup, annotations, the `Context` object — so `doctor` (and the
        # bootstrap `verify` step that shells out to it) proves the whole chain
        # answers, not merely that `Bridge.status()` can format a reply. Reuses the
        # connection already established above; does not spawn a second daemon.
        set_shared_bridge(bridge)
        try:
            mcp = build_server(settings, bridge=bridge)
            if "admin" not in settings.toolsets:
                # tb_status lives in the admin toolset. Calling it anyway would just
                # raise `ToolError: Unknown tool: tb_status` — indistinguishable, to a
                # naive check, from the tool genuinely failing. Record the real reason
                # instead of dispatching a call we already know cannot succeed.
                report["tbStatusCall"] = {
                    "skipped": "admin toolset not selected; tb_status is not registered"
                }
            else:
                result = await mcp.call_tool("tb_status", {})
                report["tbStatusCall"] = result.structured_content
        except Exception as exc:
            report["tbStatusCall"] = {"error": f"{type(exc).__name__}: {exc}"}
        finally:
            set_shared_bridge(None)
            await bridge.close()

    asyncio.run(probe())

    # Read the advertisement only now: the probe may have started the daemon it
    # then connected to, and reading before it printed "daemon: not running"
    # directly above "connected: True" on every first run.
    info = DaemonInfo.load()
    report["daemon"] = (
        {"running": True, "pid": info.pid, "port": info.port} if info else {"running": False}
    )
    # The daemon is spawned detached onto DEVNULL, so this file is the only place
    # its side of a failure survives.
    report["daemon"]["logFile"] = str(daemon_log_path())

    # Both derived from what is already in the report, once the probe has filled in
    # the live half: which add-on is actually running, and the add-on's own account
    # of its connection attempts.
    report["addonVersions"] = _addon_version_check(report)
    status_report = report.get("addonStatus")
    if isinstance(status_report, dict) and isinstance(status_report.get("transport"), dict):
        report["addonTransport"] = status_report["transport"]

    report["toolsets"] = {
        "selected": list(settings.toolsets),
        "available": list(ALL_TOOLSETS),
        "readOnly": settings.read_only,
        "sendMode": settings.send_mode,
    }

    ok = _doctor_ok(report)
    report["ok"] = ok

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0 if ok else 1

    _print_doctor(report)
    return 0 if ok else 1
