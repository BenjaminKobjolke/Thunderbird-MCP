"""Read, merge, and safely write client configuration files."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .client_model import SERVER_NAME, Ctx, Outcome, SetupError


def _read(path: Path) -> str | None:
    """Existing text, or `None` when the file is not there.

    `utf-8-sig` on the way in and plain `utf-8` on the way out: an editor may have
    left a BOM, and both `json.loads` and `tomlkit.parse` choke on one, so it must be
    dropped on read and never written back.
    """
    try:
        return path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SetupError(f"cannot read {path}: {exc}") from exc


def _has_comments(text: str) -> bool:
    """Whether this is really JSONC. String-aware, so `https://` does not count."""
    in_string = False
    escaped = False
    previous = ""
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif previous == "/" and char in "/*":
            return True
        previous = char
    return False


def _load_json(path: Path) -> tuple[dict[str, Any], bool]:
    """`(object, has_comments)`. Refuses to guess at a file it cannot parse."""
    text = _read(path)
    if text is None or not text.strip():
        return {}, False
    commented = _has_comments(text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        if commented:
            return {}, True
        raise SetupError(
            f"{path} is not valid JSON ({exc}). Fix it, then run setup again."
        ) from exc
    if not isinstance(data, dict):
        raise SetupError(f"{path} does not hold a JSON object.")
    return data, commented


def _peek_json(path: Path) -> dict[str, Any]:
    """Read-only, forgiving load — for files we inspect but must never write."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _backup(path: Path) -> Path | None:
    """One timestamped copy before the first write of a run."""
    if not path.exists():
        return None
    target = path.with_name(f"{path.name}.tbmcp-{time.strftime('%Y%m%d%H%M%S')}.bak")
    try:
        shutil.copy2(path, target)
    except OSError as exc:
        raise SetupError(f"could not back up {path}: {exc}") from exc
    return target


def _write(path: Path, text: str) -> Path | None:
    """Back up, write a sibling temp file, then `os.replace` it into place.

    Same directory for the temp file so the replace is atomic on the same volume —
    a crash mid-write can then never leave a truncated config behind.
    """
    backup = _backup(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tbmcp-tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except OSError as exc:
        raise SetupError(f"could not write {path}: {exc}") from exc
    return backup


def _dump_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _run(argv: list[str]) -> tuple[int, str]:
    """Run a client CLI. Never raises: a missing CLI is a normal outcome here."""
    # argv is ours and argv[0] came from which(), so there is no shell in the path.
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        return 127, str(exc)
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def _apply_json(
    client: str,
    path: Path,
    root_key: str,
    entry: dict[str, Any],
    ctx: Ctx,
    *,
    note: str = "",
) -> Outcome:
    """Merge one server entry into a plain JSON config, preserving everything else."""
    data, commented = _load_json(path)
    section = data.get(root_key)
    if not isinstance(section, dict):
        section = {}
    current = section.get(SERVER_NAME)
    block = _dump_json({root_key: {SERVER_NAME: entry}})

    if current == entry:
        return Outcome(client, "unchanged", str(path), note)
    status = "updated" if isinstance(current, dict) else "added"
    if ctx.preview:
        return Outcome(client, status, str(path), note, block, planned=True)
    if commented:
        return Outcome(
            client,
            "skipped",
            str(path),
            "has // comments; reserialising would delete them. Paste this instead:",
            block,
        )

    data[root_key] = {**section, SERVER_NAME: entry}
    backup = _write(path, _dump_json(data))
    return Outcome(client, status, str(path), _with_backup(note, backup))


def _with_backup(note: str, backup: Path | None) -> str:
    if backup is None:
        return note
    text = f"backup: {backup.name}"
    return f"{note} ({text})" if note else text
