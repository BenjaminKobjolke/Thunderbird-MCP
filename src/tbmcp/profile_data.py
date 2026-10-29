"""Read Thunderbird preferences and database snapshots without starting it."""

from __future__ import annotations

import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .profile import ThunderbirdProfile

_PREF_LINE = re.compile(
    r"""^\s*(?:user_)?pref\(\s*(["'])(?P<name>.*?)\1\s*,\s*(?P<value>.*?)\s*\)\s*;\s*$"""
)


def _decode_pref_value(raw: str) -> Any:
    raw = raw.strip()
    if raw == "true":
        return True
    if raw == "false":
        return False
    if raw and (raw[0] in "\"'"):
        quote = raw[0]
        body = raw[1:-1] if raw.endswith(quote) and len(raw) >= 2 else raw[1:]
        # Gecko writes JS string escapes; only these actually occur in prefs.js.
        return (
            body.replace("\\\\", "\x00")
            .replace('\\"', '"')
            .replace("\\'", "'")
            .replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace("\x00", "\\")
        )
    try:
        return int(raw, 10)
    except ValueError:
        return raw


def read_prefs(prefs_js: Path) -> dict[str, Any]:
    """Parse a `prefs.js` / `user.js` into a plain dict.

    Read-only by design. Writing prefs.js behind a running Thunderbird loses data,
    because Thunderbird rewrites the whole file from memory on shutdown — preference
    *writes* always go through the add-on.
    """
    if not prefs_js.is_file():
        return {}
    out: dict[str, Any] = {}
    for line in prefs_js.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _PREF_LINE.match(line)
        if match:
            out[match.group("name")] = _decode_pref_value(match.group("value"))
    return out


def open_readonly(db_path: Path, *, copy_if_locked: bool = True) -> sqlite3.Connection:
    """Open a Thunderbird sqlite file without disturbing a running Thunderbird.

    `mode=ro` plus a short busy timeout is enough for the WAL databases most of the
    time. When Windows refuses the handle outright we fall back to a snapshot copy,
    which is what makes `tbmcp doctor` and the offline read path reliable.
    """
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    uri = f"file:{db_path.as_posix()}?mode=ro&immutable=0"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        conn.execute("PRAGMA query_only = ON")
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        if not copy_if_locked:
            raise
    tmp_dir = Path(tempfile.mkdtemp(prefix="tbmcp-"))
    snapshot = tmp_dir / db_path.name
    shutil.copy2(db_path, snapshot)
    for sidecar in ("-wal", "-shm"):
        side = db_path.with_name(db_path.name + sidecar)
        if side.is_file():
            shutil.copy2(side, snapshot.with_name(snapshot.name + sidecar))
    conn = sqlite3.connect(snapshot, timeout=2.0)
    conn.row_factory = sqlite3.Row
    return conn


@dataclass
class ProfileSnapshot:
    """What we can tell about a profile without Thunderbird's help."""

    profile: ThunderbirdProfile
    prefs: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, profile: ThunderbirdProfile) -> ProfileSnapshot:
        return cls(profile=profile, prefs=read_prefs(profile.prefs_js))

    def accounts(self) -> list[dict[str, Any]]:
        """Accounts reconstructed from `mail.account.*` / `mail.server.*` prefs."""
        order = str(self.prefs.get("mail.accountmanager.accounts", "")).split(",")
        default = self.prefs.get("mail.accountmanager.defaultaccount")
        out: list[dict[str, Any]] = []
        for key in (k.strip() for k in order):
            if not key:
                continue
            server_key = self.prefs.get(f"mail.account.{key}.server")
            identity_keys = [
                i.strip()
                for i in str(self.prefs.get(f"mail.account.{key}.identities", "")).split(",")
                if i.strip()
            ]
            entry: dict[str, Any] = {
                "accountKey": key,
                "isDefault": key == default,
                "serverKey": server_key,
                "identityKeys": identity_keys,
            }
            if server_key:
                prefix = f"mail.server.{server_key}."
                entry["server"] = {
                    name[len(prefix) :]: value
                    for name, value in self.prefs.items()
                    if name.startswith(prefix)
                }
            entry["identities"] = [
                {
                    name[len(f"mail.identity.{ident}.") :]: value
                    for name, value in self.prefs.items()
                    if name.startswith(f"mail.identity.{ident}.")
                }
                for ident in identity_keys
            ]
            out.append(entry)
        return out

    def outgoing_servers(self) -> list[dict[str, Any]]:
        keys = [
            k.strip() for k in str(self.prefs.get("mail.smtpservers", "")).split(",") if k.strip()
        ]
        return [
            {
                "serverKey": key,
                **{
                    name[len(f"mail.smtpserver.{key}.") :]: value
                    for name, value in self.prefs.items()
                    if name.startswith(f"mail.smtpserver.{key}.")
                },
            }
            for key in keys
        ]
