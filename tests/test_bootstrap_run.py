from __future__ import annotations

import json
import sys

import pytest
from support_bootstrap import STEPS, Recorder

from tbmcp.bootstrap import (
    VERSION,
    Options,
    _step_addon,
    _step_imports,
    _step_venv,
    bootstrap,
)
from tbmcp.bootstrap import venv_python as _venv_python_path

pytestmark = pytest.mark.usefixtures("no_stray_interpreters")


def test_reports_every_step_in_order(tmp_path):
    recorder = Recorder(_venv_python_path(tmp_path / "venv"))
    report = bootstrap(Options(venv=tmp_path / "venv", dry_run=True), run=recorder.run)
    assert [step.name for step in report.steps] == STEPS
    # A dry run establishes nothing — no step actually ran `verify` — so it must
    # never read the same as a real success. See test_dry_run_is_never_reported_ok.
    assert report.ok is False
    assert report.next_command is not None


def test_dry_run_creates_nothing(tmp_path):
    venv = tmp_path / "venv"
    bootstrap(Options(venv=venv, dry_run=True), run=Recorder(_venv_python_path(venv)).run)
    assert not venv.exists()


def test_failure_stops_and_names_the_next_command(tmp_path):
    def run(argv):
        return 1, "blocked"

    report = bootstrap(Options(venv=tmp_path / "venv", dry_run=True), run=run)
    assert report.ok is False
    assert report.steps[0].status == "failed"
    assert report.next_command
    assert len(report.steps) == 1


def test_venv_step_fails_when_the_created_interpreter_cannot_load_extensions(tmp_path):
    """I8: `python -m venv` exiting 0 says the tool ran, not that the interpreter it
    produced can load `sqlite3`/`ssl`/`ctypes` — the same gap `interpreter_ok` exists
    to close for candidate selection. A reused venv was already load-tested; a freshly
    created one must be too, not trusted on a bare exit code.
    """
    venv_dir = tmp_path / "venv"
    python_path = _venv_python_path(venv_dir)

    def run(argv):
        argv = list(argv)
        if "-m" in argv and "venv" in argv:
            python_path.parent.mkdir(parents=True, exist_ok=True)
            python_path.write_text("not a real interpreter", encoding="utf-8")
            return 0, ""
        if "-c" in argv:
            # The load test on the interpreter this "created" — deliberately unhealthy.
            return 1, "ImportError: DLL load failed while importing _sqlite3"
        raise AssertionError(f"unexpected call: {argv}")

    state = {"venv": venv_dir, "interpreter": "python", "launcher": None}
    status, detail = _step_venv(Options(dry_run=False), state, run)

    assert status == "failed"
    assert "sqlite3" in detail or "ssl" in detail or "ctypes" in detail


def test_skip_addon_marks_it_skipped(tmp_path):
    recorder = Recorder(_venv_python_path(tmp_path / "venv"))
    report = bootstrap(
        Options(venv=tmp_path / "venv", dry_run=True, skip_addon=True), run=recorder.run
    )
    statuses = {step.name: step.status for step in report.steps}
    assert statuses["addon"] == "skipped"


def test_json_is_machine_readable(tmp_path):
    recorder = Recorder(_venv_python_path(tmp_path / "venv"))
    report = bootstrap(Options(venv=tmp_path / "venv", dry_run=True), run=recorder.run)
    payload = json.loads(report.to_json())
    assert payload["ok"] is False  # dry run: see test_dry_run_is_never_reported_ok
    assert payload["version"] == VERSION
    assert [s["name"] for s in payload["steps"]] == STEPS
    assert set(payload) == {"ok", "version", "launcher", "steps", "next_command"}


def test_dry_run_is_never_reported_ok(tmp_path):
    """I1: a dry run must not be byte-shaped identically to a real success in the
    five-key JSON contract an agent parses. `ok` means "verified working"; a dry run
    never runs `verify` (it is always `skipped`), so `ok` must never be `true`,
    no matter how cleanly every individual step reads.
    """
    recorder = Recorder(_venv_python_path(tmp_path / "venv"))
    report = bootstrap(Options(venv=tmp_path / "venv", dry_run=True), run=recorder.run)
    assert report.ok is False
    assert all(step.status in ("ok", "skipped") for step in report.steps)
    statuses = {step.name: step.status for step in report.steps}
    assert statuses["verify"] == "skipped"
    # The next command must be something that actually does the install — not a
    # remedy for a failure that never happened.
    assert report.next_command.startswith("python bootstrap.py")
    assert "--dry-run" not in report.next_command


def test_clean_dry_run_text_does_not_read_as_stopped(tmp_path):
    """New breakage #2: `to_text()` used to print "Stopped. Next: ..." for *every*
    `ok: false` report, so a dry run that did exactly what was asked looked
    byte-for-byte like a genuine failure in the human-readable output too.
    """
    recorder = Recorder(_venv_python_path(tmp_path / "venv"))
    report = bootstrap(Options(venv=tmp_path / "venv", dry_run=True), run=recorder.run)
    assert report.ok is False
    assert not any(step.status == "failed" for step in report.steps)
    text = report.to_text()
    assert "Stopped." not in text
    assert report.next_command in text


def test_failed_dry_run_text_still_says_stopped(tmp_path):
    """The other half: a dry run that genuinely hit a `failed` step must still read
    as stopped, not as a clean dry-run completion."""

    def run(argv):
        return 1, "no usable interpreter"

    report = bootstrap(Options(venv=tmp_path / "venv", dry_run=True), run=run)
    assert report.ok is False
    assert any(step.status == "failed" for step in report.steps)
    text = report.to_text()
    assert "Stopped." in text


def test_real_run_reaching_the_end_is_reported_ok(tmp_path):
    """The flip side of the above: a run that is *not* a dry run, and that hits no
    `failed` step, must still be able to report `ok: true` — the dry-run fix must
    not make every run unconditionally `ok: false`.
    """

    def run(argv):
        argv = list(argv)
        if argv[1:] == ["-m", "tbmcp", "detect-clients"]:
            return 0, json.dumps([])
        if "-c" in argv:
            source = argv[argv.index("-c") + 1]
            if "sqlite3" in source:
                return 0, json.dumps({"ok": True, "version": "3.14"})
            if "__import__" in source:
                return 0, json.dumps({"ok": True})
        return 0, json.dumps({"ok": True})

    report = bootstrap(Options(venv=tmp_path / "venv", dry_run=False, skip_addon=True), run=run)
    assert report.ok is True
    assert report.next_command is None


# --------------------------------------------------------------- deferred-import status


def test_blocked_import_is_reported_as_skipped_not_ok_or_failed(tmp_path):
    """A blocked import must never read as `ok` — that is the exact lie this exists to catch.

    Exercises `_step_imports` directly with `dry_run=False`, so it actually probes
    (see `test_imports_step_does_not_probe_under_dry_run` below for the dry-run
    case, where nothing is probed at all).
    """
    venv_python = str(tmp_path / "venv" / "Scripts" / "python.exe")

    def run(argv):
        argv = list(argv)
        if "-c" in argv:
            source = argv[argv.index("-c") + 1]
            if "__import__" in source:
                return 0, json.dumps(
                    {"ok": False, "name": "somepkg", "path": None, "message": "boom"}
                )
        return 0, ""

    status, detail = _step_imports(Options(dry_run=False), {"venv_python": venv_python}, run)
    assert status == "skipped"
    assert status != "ok"
    assert status != "failed"
    assert "somepkg" in detail


def test_imports_step_does_not_probe_under_dry_run(tmp_path):
    """I3: nothing was installed under `--dry-run`, so nothing should be probed —
    probing a venv that was never created can only report a failure that has
    nothing to do with the actual package.
    """
    venv_python = str(tmp_path / "venv" / "Scripts" / "python.exe")

    def run(argv):
        raise AssertionError(f"--dry-run must not probe anything: {argv}")

    status, detail = _step_imports(Options(dry_run=True), {"venv_python": venv_python}, run)
    assert status == "skipped"
    assert "venv" in detail.lower()


# ------------------------------------------------------- interpreter identity, not just argv shape


def test_imports_step_probes_the_venvs_python_not_the_system_one(tmp_path):
    """A step that probed the wrong interpreter must make the fake raise, not succeed quietly.

    `state` carries both the venv's python (what `imports` is supposed to use) and the
    system interpreter (what `interpreter` chose) under different keys, so a step that
    reached for the wrong one would produce a different `argv[0]` — not a `KeyError` —
    and this fake is built to catch exactly that.
    """
    venv_py = str(_venv_python_path(tmp_path / "venv"))
    system_py = sys.executable

    def run(argv):
        argv = list(argv)
        if argv[0] != venv_py:
            raise AssertionError(f"probed {argv[0]!r}, expected the venv's python {venv_py!r}")
        return 0, json.dumps({"ok": True})

    state = {"venv_python": venv_py, "interpreter": system_py}
    status, _detail = _step_imports(Options(dry_run=False), state, run)
    assert status == "ok"


# --------------------------------------------------------------- exact argv, real subprocess


def test_addon_step_shells_out_with_yes_and_never_imports_tbmcp(tmp_path):
    """`--yes` is load-bearing: without it `install-addon` calls `input()` and hangs an agent."""
    calls: list[list[str]] = []

    def run(argv):
        calls.append(list(argv))
        return 0, "installed"

    venv_python = str(tmp_path / "venv" / "Scripts" / "python.exe")
    status, _detail = _step_addon(Options(dry_run=False), {"venv_python": venv_python}, run)

    assert status == "ok"
    assert calls == [[venv_python, "-m", "tbmcp", "install-addon", "--yes"]]
