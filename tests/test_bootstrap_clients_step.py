from __future__ import annotations

import json

import pytest

from tbmcp.bootstrap import (
    Options,
    _detected_clients,
    _step_clients,
)

pytestmark = pytest.mark.usefixtures("no_stray_interpreters")


def test_clients_step_shells_out_with_requested_clients(tmp_path):
    calls: list[list[str]] = []

    def run(argv):
        calls.append(list(argv))
        return 0, "configured"

    venv_python = str(tmp_path / "venv" / "Scripts" / "python.exe")
    options = Options(dry_run=False, clients=("claude-code", "codex"))
    status, _detail = _step_clients(options, {"venv_python": venv_python}, run)

    assert status == "ok"
    assert calls == [[venv_python, "-m", "tbmcp", "setup", "claude-code", "codex"]]


# ------------------------------------------------------------ auto-detected clients


def test_detected_clients_returns_none_on_nonzero_exit():
    """`None` — could not run — not `()` — ran and found nothing. See I2: conflating
    the two let `_step_clients` claim a fact about the machine it never checked."""

    def run(argv):
        return 1, "detect-clients crashed"

    assert _detected_clients("python", run) is None


def test_detected_clients_returns_none_on_empty_output():
    def run(argv):
        return 0, ""

    assert _detected_clients("python", run) is None


def test_detected_clients_returns_none_on_malformed_json():
    def run(argv):
        return 0, "[this is not json"

    assert _detected_clients("python", run) is None


def test_detected_clients_returns_empty_tuple_on_a_genuine_empty_list():
    """The one case that really is "ran cleanly, found nothing": a well-formed `[]`."""

    def run(argv):
        return 0, json.dumps([])

    assert _detected_clients("python", run) == ()


def test_detected_clients_skips_junk_lines_before_the_json():
    def run(argv):
        return 0, 'warning: something noisy\nDeprecationWarning: whatever\n["zed"]'

    assert _detected_clients("python", run) == ("zed",)


def test_clients_step_detects_and_registers_when_none_requested(tmp_path):
    """The headline promise: no `--clients` still registers whatever is installed."""
    calls: list[list[str]] = []

    def run(argv):
        argv = list(argv)
        calls.append(argv)
        if argv[-1] == "detect-clients":
            return 0, json.dumps(["claude-code", "codex"])
        return 0, ""

    venv_python = str(tmp_path / "venv" / "Scripts" / "python.exe")
    status, detail = _step_clients(Options(dry_run=False), {"venv_python": venv_python}, run)

    assert status == "ok"
    assert "claude-code" in detail and "codex" in detail
    assert calls == [
        [venv_python, "-m", "tbmcp", "detect-clients"],
        [venv_python, "-m", "tbmcp", "setup", "claude-code", "codex"],
    ]


def test_clients_step_skips_not_ok_when_nothing_detected(tmp_path):
    """No `--clients` and nothing found must read as `skipped`, never `ok`."""

    def run(argv):
        argv = list(argv)
        if argv[-1] == "detect-clients":
            return 0, json.dumps([])
        raise AssertionError(f"nothing was detected; must not register anyway: {argv}")

    venv_python = str(tmp_path / "venv" / "Scripts" / "python.exe")
    status, detail = _step_clients(Options(dry_run=False), {"venv_python": venv_python}, run)

    assert status == "skipped"
    assert status != "ok"
    assert "no" in detail.lower() and "detect" in detail.lower()


def test_clients_step_dry_run_may_detect_but_never_registers(tmp_path):
    """I1: a dry run that detected something real still established nothing —
    `skipped`, not `ok`, the same status the project already uses everywhere else
    for a deferred outcome.
    """
    calls: list[list[str]] = []

    def run(argv):
        argv = list(argv)
        calls.append(argv)
        if argv[-1] == "detect-clients":
            return 0, json.dumps(["claude-code"])
        raise AssertionError(f"--dry-run must never register anything: {argv}")

    venv_python = str(tmp_path / "venv" / "Scripts" / "python.exe")
    status, detail = _step_clients(Options(dry_run=True), {"venv_python": venv_python}, run)

    assert status == "skipped"
    assert status != "ok"
    assert "claude-code" in detail
    assert calls == [[venv_python, "-m", "tbmcp", "detect-clients"]]


def test_clients_step_says_it_could_not_check_when_detection_cannot_run(tmp_path):
    """I2: when detection cannot run at all (a nonexistent venv python under
    `--dry-run`, before any venv exists, shells out to `FileNotFoundError` and a
    non-zero exit), the step must say that detection did not run — never assert a
    fact about the machine ("no MCP clients detected") that it never checked.
    """

    def run(argv):
        return 127, "FileNotFoundError: no such file"

    venv_python = str(tmp_path / "venv" / "Scripts" / "python.exe")
    status, detail = _step_clients(Options(dry_run=True), {"venv_python": venv_python}, run)

    assert status == "skipped"
    assert "no mcp clients detected" not in detail.lower()
    assert "could not" in detail.lower()


def test_clients_step_in_a_real_run_reports_a_crash_not_a_missing_venv(tmp_path):
    """New breakage #5: by the time `clients` runs in a real (non-dry-run) bootstrap,
    `venv` and `install` have already reported `ok` — a working venv necessarily
    exists. If `detect-clients` still comes back unusable here, that is because it
    crashed or produced junk, not because there was "no working venv to run it in
    yet" (that phrasing is true only under `--dry-run`, before any venv is built).
    """

    def run(argv):
        return 1, "Traceback (most recent call last): ... ImportError"

    venv_python = str(tmp_path / "venv" / "Scripts" / "python.exe")
    status, detail = _step_clients(Options(dry_run=False), {"venv_python": venv_python}, run)

    assert status == "skipped"
    assert "no working venv" not in detail.lower()
    assert "could not" in detail.lower()


def test_clients_step_with_explicit_clients_skips_detection_entirely(tmp_path):
    """An explicit `--clients` must register exactly that, with no detection call at all."""

    def run(argv):
        argv = list(argv)
        if argv[-1] == "detect-clients":
            raise AssertionError("explicit --clients must not trigger detection")
        return 0, "configured"

    venv_python = str(tmp_path / "venv" / "Scripts" / "python.exe")
    options = Options(dry_run=False, clients=("zed",))
    status, _detail = _step_clients(options, {"venv_python": venv_python}, run)

    assert status == "ok"
