"""Locating Thunderbird on disk, and reading what it stores there.

Two jobs:

1. Find the profile directory, so the daemon can drop its pairing file where the
   add-on will look for it, and so `doctor` can explain what it found.
2. Read a few things straight off disk. This is the fallback path for when
   Thunderbird is closed — it is strictly read-only, and never touches a file
   Thunderbird might be writing.

Profile discovery prefers the running Thunderbird's `-profile` directory, then
the stored Windows user variable, then follows `profiles.ini`, where a `Default=` inside an
`[Install<HASH>]` section is authoritative (that is the dedicated profile of the
installed build) and `Default=1` inside a `[Profile<N>]` section is the legacy
fallback. On the machine this was developed against those two disagree, so the
order matters.
"""

from __future__ import annotations

import configparser
import os
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path

BRIDGE_FILE = "tbmcp-bridge.json"
SOURCE_LABELS = {
    "explicit": "set with --profile / TBMCP_PROFILE",
    "running": "taken from the running Thunderbird",
    "user-variable": "TBMCP_PROFILE from the Windows user variables",
    "default": "the profiles.ini default",
}


@dataclass(frozen=True)
class ThunderbirdProfile:
    path: Path
    name: str
    is_default: bool
    root: Path
    """The directory holding profiles.ini — the packaging-specific install root."""
    source: str = "explicit"

    @property
    def bridge_file(self) -> Path:
        return self.path / BRIDGE_FILE

    @property
    def addon_status_file(self) -> Path:
        """What the add-on wrote at startup; the only channel left when the bridge
        itself is broken. See `addon/background/main.js`."""
        return self.path / "tbmcp-addon-status.json"

    @property
    def prefs_js(self) -> Path:
        return self.path / "prefs.js"

    @property
    def gloda_db(self) -> Path:
        return self.path / "global-messages-db.sqlite"

    @property
    def address_book_db(self) -> Path:
        return self.path / "abook.sqlite"

    @property
    def calendar_db(self) -> Path:
        return self.path / "calendar-data" / "local.sqlite"


def candidate_roots() -> list[Path]:
    """Every place a Thunderbird `profiles.ini` may live on this platform."""
    home = Path.home()
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        roots = [Path(appdata) / "Thunderbird"] if appdata else []
        return [*roots, home / "AppData" / "Roaming" / "Thunderbird"]
    if sys.platform == "darwin":
        return [
            home / "Library" / "Thunderbird",
            home / "Library" / "Application Support" / "Thunderbird",
        ]
    return [
        home / ".thunderbird",
        # Snap and Flatpak relocate the whole tree.
        home / "snap" / "thunderbird" / "common" / ".thunderbird",
        home / ".var" / "app" / "org.mozilla.Thunderbird" / ".thunderbird",
    ]


def _parse_profiles_ini(root: Path) -> list[ThunderbirdProfile]:
    ini_path = root / "profiles.ini"
    if not ini_path.is_file():
        return []
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    # profiles.ini is written by Gecko and can contain duplicate keys after
    # profile juggling; strict=False tolerates that.
    parser.read(ini_path, encoding="utf-8")

    def resolve(raw: str, is_relative: bool) -> Path:
        cleaned = raw.replace("/", os.sep)
        return (root / cleaned) if is_relative else Path(cleaned)

    # An [InstallXXXX] Default= wins: it is the profile the installed build uses.
    install_default: str | None = None
    for section in parser.sections():
        if section.lower().startswith("install") and parser.has_option(section, "Default"):
            install_default = parser.get(section, "Default")
            break

    profiles: list[ThunderbirdProfile] = []
    legacy_default: Path | None = None
    for section in parser.sections():
        if not re.fullmatch(r"Profile\d+", section):
            continue
        raw_path = parser.get(section, "Path", fallback=None)
        if not raw_path:
            continue
        is_relative = parser.get(section, "IsRelative", fallback="1").strip() == "1"
        path = resolve(raw_path, is_relative)
        name = parser.get(section, "Name", fallback=path.name)
        if parser.get(section, "Default", fallback="").strip() == "1":
            legacy_default = path
        profiles.append(ThunderbirdProfile(path=path, name=name, is_default=False, root=root))

    chosen: Path | None = None
    if install_default:
        chosen = resolve(install_default, True)
    elif legacy_default:
        chosen = legacy_default
    elif profiles:
        chosen = profiles[0].path

    return [
        ThunderbirdProfile(
            path=p.path,
            name=p.name,
            is_default=(chosen is not None and p.path == chosen),
            root=p.root,
        )
        for p in profiles
    ]


def list_profiles() -> list[ThunderbirdProfile]:
    """All profiles found across every candidate root, default first."""
    found: list[ThunderbirdProfile] = []
    for root in candidate_roots():
        for profile in _parse_profiles_ini(root):
            if profile.path.is_dir() and all(p.path != profile.path for p in found):
                found.append(profile)
    found.sort(key=lambda p: (not p.is_default, p.name))
    return found


def profile_from_command_line(line: str) -> Path | None:
    if re.search(r"(?:^|\s)-contentproc(?:\s|$)", line, re.I):
        return None
    # ponytail: unquoted paths with spaces in ps output fall back to profiles.ini;
    # read /proc/<pid>/cmdline if that case becomes common.
    match = re.search(r'(?:^|\s)--?profile(?:\s+|=)(?:"([^"]+)"|\'([^\']+)\'|(\S+))', line, re.I)
    return Path(next((part for part in match.groups() if part), "")) if match else None


def same_path(a: str | os.PathLike[str], b: str | os.PathLike[str]) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def running_profile_dirs() -> list[Path]:
    from .thunderbird_process import running_command_lines

    found: list[Path] = []
    for line in running_command_lines():
        path = profile_from_command_line(line)
        if path is not None and path.is_dir() and not any(same_path(path, p) for p in found):
            found.append(path)
    return found


def profile_mismatch(profile_status: dict | None) -> bool:
    status = profile_status or {}
    path = status.get("path")
    others = status.get("thunderbirdProfiles") or []
    return bool(path and others and not any(same_path(path, p) for p in others))


def describe_profile(profile_status: dict | None, *, running: bool) -> str | None:
    if not profile_status or not profile_status.get("path"):
        return None
    path = profile_status["path"]
    source = profile_status.get("source")
    label = f" ({SOURCE_LABELS[source]})" if source in SOURCE_LABELS else ""
    others = profile_status.get("thunderbirdProfiles") or []
    if profile_mismatch(profile_status):
        other = others[0]
        return (
            f"The daemon watches the profile {path}{label}, but the running Thunderbird "
            f"uses {other}, so the add-on never sees the pairing file. Set "
            f"TBMCP_PROFILE={other} or pass --profile {other} in the MCP server "
            "configuration, then restart the daemon and MCP client."
        )
    if running:
        return (
            f"The daemon watches the profile {path}{label}. If Thunderbird uses a "
            "different one, set TBMCP_PROFILE or --profile to its directory."
        )
    return None


def find_profile(
    explicit: str | os.PathLike[str] | None = None, *, follow_running: bool = True
) -> ThunderbirdProfile | None:
    """Resolve the profile to operate on.

    `explicit` (from `--profile` or `TBMCP_PROFILE`) may be a directory path or a
    profile name from profiles.ini.
    """
    explicit = explicit or os.environ.get("TBMCP_PROFILE") or None
    profiles = list_profiles()
    if explicit:
        as_path = Path(explicit).expanduser()
        if as_path.is_dir():
            root = as_path.parent.parent
            return ThunderbirdProfile(path=as_path, name=as_path.name, is_default=False, root=root)
        for profile in profiles:
            if profile.name == str(explicit):
                return profile
        return None
    if follow_running:
        running = running_profile_dirs()
        if running:
            path = running[0]
            return ThunderbirdProfile(path, path.name, False, path.parent.parent, "running")
    from .thunderbird_process import saved_user_variable

    saved = saved_user_variable("TBMCP_PROFILE")
    if saved:
        chosen = find_profile(saved, follow_running=False)
        if chosen is not None:
            return replace(chosen, source="user-variable")
    return replace(profiles[0], source="default") if profiles else None
