"""Human-readable formatting for the doctor command."""

from __future__ import annotations

import textwrap
import time

from .handshake import describe_handshake


def _handshake_line(summary: dict | None) -> str:
    """The Bridge section's account of what the add-on's connections did.

    "Nothing has dialled in" and "it has dialled in three times and failed every
    time" are opposite problems that used to print identically.
    """
    attempts = int((summary or {}).get("attempts") or 0)
    if not summary or not attempts:
        return "none since the daemon started"
    parts = [f"{attempts} attempt{'' if attempts == 1 else 's'}"]
    outcome = summary.get("lastOutcome")
    if outcome:
        at = summary.get("lastAttemptAt")
        ago = f" {time.time() - at:.0f} s ago" if isinstance(at, int | float) else ""
        parts.append(f"last {outcome}{ago}")
    agent = summary.get("lastUserAgent")
    return "; ".join(parts) + (f" ({agent})" if agent else "")


def _transport_line(transport: dict) -> str:
    """The add-on's own side of the same story, from the file it writes at startup."""
    parts = [str(transport.get("state") or "unknown")]
    failures = transport.get("consecutiveFailures")
    if failures is not None:
        parts.append(f"{failures} failed attempt{'' if failures == 1 else 's'}")
    if transport.get("lastCloseCode") is not None:
        parts.append(f"last close {transport['lastCloseCode']} at {transport.get('lastCloseAt')}")
    return "; ".join(parts)


def _print_doctor(report: dict) -> None:
    from .profile import SOURCE_LABELS, describe_profile, profile_mismatch

    def line(label: str, value: object) -> None:
        print(f"  {label:<26} {value}")

    def paragraph(text: str) -> None:
        """One wrapped bullet, for a diagnosis too long to fit on a line."""
        wrapped = textwrap.wrap(text, 76) or [text]
        print(f"  * {wrapped[0]}")
        for extra in wrapped[1:]:
            print(f"    {extra}")

    print("thunderbird-mcp doctor\n")
    print("Python")
    line("version", report["python"])
    line("interpreter", report["executable"])

    print("\nThunderbird")
    addon = report.get("addon") or {}
    line("executable", addon.get("thunderbirdExe") or "NOT FOUND")
    line("running", addon.get("thunderbirdRunning"))
    line("add-on version (source)", addon.get("addonVersion"))
    versions = report.get("addonVersions") or {}
    line("add-on version (installed)", versions.get("installed") or "unknown")
    line("add-on build (source)", versions.get("sourceBuild") or "unknown")
    line("add-on build (installed)", versions.get("installedBuild") or "unknown")
    line("profile", report.get("profileSelected") or "NOT FOUND")
    if report.get("profileSource"):
        line("profile chosen by", SOURCE_LABELS[report["profileSource"]])
    if report.get("accountsOnDisk") is not None:
        line("accounts (from prefs.js)", report["accountsOnDisk"])
        line("outgoing servers", report["outgoingServersOnDisk"])
        line("global index db", report.get("glodaDatabase"))

    status = report.get("addonStatus")
    if status is None:
        line("add-on startup report", "MISSING")
    elif isinstance(status, dict) and status.get("error"):
        line("add-on startup report", f"unreadable: {status['error']}")
    else:
        capabilities = (status or {}).get("capabilities") or {}
        loaded = (capabilities.get("privilegedModules") or {}).get("loaded") or []
        line("add-on startup report", status.get("writtenAt"))
        line("privileged modules", f"{len(loaded)} loaded" if loaded else "none")
        line("bridge methods", status.get("methodCount"))

    print("\nBridge")
    daemon = report.get("daemon") or {}
    line("daemon", f"pid {daemon.get('pid')}" if daemon.get("running") else "not running")
    line("daemon log", daemon.get("logFile") or "unknown")
    bridge = report.get("bridge") or {}
    bridge_profile = bridge.get("profile") or {}
    if bridge_profile.get("path"):
        source = bridge_profile.get("source")
        label = f" ({SOURCE_LABELS[source]})" if source in SOURCE_LABELS else ""
        line("daemon profile", f"{bridge_profile['path']}{label}")
        line(
            "Thunderbird's profile",
            ", ".join(bridge_profile.get("thunderbirdProfiles") or []) or "not on its command line",
        )
    if bridge.get("error"):
        line("status", f"ERROR: {bridge['error']}")
    else:
        line("connected", bridge.get("connected"))
        tb = bridge.get("thunderbird") or {}
        if tb:
            line("add-on version (live)", tb.get("addonVersion"))
            line("privileged half", tb.get("experiment"))
            app = tb.get("app") or {}
            line("app", f"{app.get('name')} {app.get('version')}")
    line("add-on handshakes", _handshake_line(bridge.get("handshake")))
    transport = report.get("addonTransport")
    if transport:
        line("add-on transport", _transport_line(transport))

    tool_call = report.get("tbStatusCall") or {}
    if tool_call.get("skipped"):
        line("tb_status tool call", f"not checked: {tool_call['skipped']}")
    elif tool_call.get("error"):
        line("tb_status tool call", f"ERROR: {tool_call['error']}")
    else:
        line("tb_status tool call", "connected" if tool_call.get("connected") else "not connected")

    print("\nTools")
    tools = report.get("toolsets") or {}
    line("toolsets", ",".join(tools.get("selected", [])))
    line("read-only", tools.get("readOnly"))
    line("send mode", tools.get("sendMode"))

    if bridge.get("connected") and versions.get("mismatch"):
        seen = versions.get("live") or versions.get("installed")
        print(
            f"\nWarning: installed add-on is {seen} "
            f"(build {versions.get('installedBuild') or 'unknown'}), "
            f"source is {versions.get('source')} "
            f"(build {versions.get('sourceBuild') or 'unknown'}) — run"
        )
        print("         `tbmcp install-addon` and restart Thunderbird to update it.")

    if not bridge.get("connected"):
        print("\nNot connected. In order, check:")
        # A stale add-on is worth saying whatever else is wrong — including when
        # Thunderbird is closed, or the user starts it and is back here in a minute.
        if versions.get("mismatch"):
            seen = versions.get("live") or versions.get("installed")
            print(
                f"  * The installed add-on is {seen} "
                f"(build {versions.get('installedBuild') or 'unknown'}), "
                f"source is {versions.get('source')} "
                f"(build {versions.get('sourceBuild') or 'unknown'})."
            )
            print("    Run `tbmcp install-addon` and restart Thunderbird.")
        if not addon.get("thunderbirdRunning"):
            print("  * Thunderbird is not running — start it.")
        else:
            failures = describe_handshake(bridge.get("handshake"))
            if profile_mismatch(bridge_profile):
                paragraph(describe_profile(bridge_profile, running=True))
            elif failures:
                # It has dialled in, repeatedly. Anything below would be a guess that
                # the record already contradicts.
                paragraph(failures)
            elif report.get("addonStatus") is None:
                print("  * The add-on never wrote its startup report, so either it is not")
                print("    installed or its privileged half failed to load.")
                print("    Install or reinstall it:  tbmcp install-addon")
            elif not (bridge.get("thunderbird") or {}):
                print("  * The add-on started but has not dialled in yet. It polls for the")
                print("    pairing file about once a second; try again in a moment.")
            elif not (bridge.get("thunderbird") or {}).get("experiment"):
                print("  * The privileged half did not load; settings tools will fail.")
                print("    Reinstall:  tbmcp install-addon")
