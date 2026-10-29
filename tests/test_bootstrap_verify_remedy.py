from __future__ import annotations

import json
import pathlib

import pytest

from tbmcp.bootstrap import (
    Options,
    _step_verify,
    bootstrap,
)

pytestmark = pytest.mark.usefixtures("no_stray_interpreters")


def test_verify_step_shells_out_to_doctor_json(tmp_path):
    calls: list[list[str]] = []

    def run(argv):
        calls.append(list(argv))
        return 0, json.dumps({"ok": True})

    venv_python = str(tmp_path / "venv" / "Scripts" / "python.exe")
    status, _detail = _step_verify(Options(dry_run=False), {"venv_python": venv_python}, run)

    assert status == "ok"
    assert calls == [[venv_python, "-m", "tbmcp", "doctor", "--json"]]


def test_verify_step_fails_when_doctor_exits_zero_but_reports_not_ok(tmp_path):
    """C1: `doctor --json` exiting 0 must not be enough on its own. This is exactly
    the bug that let `verify` certify a server that never worked — `doctor --json`
    used to always exit 0 regardless of what it found, and `_step_verify` trusted
    only the exit code.
    """

    def run(argv):
        # A real subprocess exiting 0 while its own report says the chain is broken —
        # the state the pre-fix `cmd_doctor --json` always produced.
        return 0, json.dumps({"ok": False, "bridge": {"connected": False}})

    venv_python = str(tmp_path / "venv" / "Scripts" / "python.exe")
    status, _detail = _step_verify(Options(dry_run=False), {"venv_python": venv_python}, run)

    assert status == "failed"
    assert status != "ok"


def test_verify_step_fails_when_doctor_output_is_not_json(tmp_path):
    def run(argv):
        return 0, "not json at all"

    venv_python = str(tmp_path / "venv" / "Scripts" / "python.exe")
    status, _detail = _step_verify(Options(dry_run=False), {"venv_python": venv_python}, run)
    assert status == "failed"


# --------------------------------------------------------------------- next_command


def test_remedy_for_binaries_names_a_real_distribution_and_venv_pip():
    """C3: the remedy must name the resolved distribution (never the raw module),
    a real version bound (never a `<older>` placeholder), and the venv's own pip
    (never bare `pip`, which is not on PATH here).
    """
    from tbmcp.bootstrap import ImportFailure, _remedy

    venv_python = r"C:\venv\Scripts\python.exe"
    state = {
        "venv": pathlib.Path(r"C:\venv"),
        "venv_python": venv_python,
        "binaries_failure": ImportFailure(
            module="_cffi_backend", path=None, message="blocked", dist="cffi", floor="2.1.0"
        ),
    }
    command = _remedy("binaries", "detail", Options(), state)

    assert "cffi" in command
    assert "_cffi_backend" not in command
    assert "<older>" not in command
    assert venv_python in command
    assert not command.startswith("pip ")


def test_remedy_for_binaries_without_a_resolved_distribution_does_not_fabricate_one():
    """When no distribution could be resolved at all (an empty-module failure),
    the remedy must not invent a fake `target==<older>` command — there is nothing
    real to name."""
    from tbmcp.bootstrap import ImportFailure, _remedy

    venv_python = r"C:\venv\Scripts\python.exe"
    state = {
        "venv": pathlib.Path(r"C:\venv"),
        "venv_python": venv_python,
        "binaries_failure": ImportFailure(module="", path=None, message="probe exited 1: "),
    }
    command = _remedy("binaries", "detail", Options(), state)

    assert "<older>" not in command
    assert "target" not in command
    assert venv_python in command


def test_remedy_for_addon_clients_verify_uses_the_venv_python_not_bare_tbmcp():
    """C4: the fall-through default used to be the bare string `tbmcp doctor` —
    unrunnable, since the bootstrap venv is never added to PATH. It must name the
    venv's own python explicitly, same as the fix already applied to `venv`/`install`.
    """
    from tbmcp.bootstrap import _remedy

    venv_python = r"C:\venv\Scripts\python.exe"
    state = {"venv": pathlib.Path(r"C:\venv"), "venv_python": venv_python}
    for step_name in ("addon", "clients", "verify"):
        command = _remedy(step_name, "some failure detail", Options(), state)
        assert command.startswith('"' + venv_python), command
        assert not command.startswith("tbmcp ")


@pytest.mark.parametrize(
    "step_name", ["interpreter", "venv", "install", "binaries", "addon", "clients", "verify"]
)
def test_no_remedy_ever_names_a_bare_tbmcp_or_pip(step_name):
    """The property the review asked for directly: whatever failed, the single next
    command must never start with a bare `tbmcp` or bare `pip` — neither is on PATH
    in the state that emits a `next_command` at all."""
    from tbmcp.bootstrap import ImportFailure, _remedy

    venv_python = r"C:\venv\Scripts\python.exe"
    state = {
        "venv": pathlib.Path(r"C:\venv"),
        "interpreter": r"C:\Python314\python.exe",
        "venv_python": venv_python,
        "binaries_failure": ImportFailure(
            module="_cffi_backend", path=None, message="x", dist="cffi", floor="1.0.0"
        ),
    }
    command = _remedy(step_name, "some failure detail", Options(), state)
    assert not command.startswith("tbmcp ")
    assert not command.startswith("pip ")
    assert command != "tbmcp doctor"


# ------------------------------------------------------------------- I5: no unhandled crash


def test_a_steps_own_bug_still_produces_valid_json_not_a_traceback():
    """I5: a step raising something other than `BootstrapError` (an `OSError`,
    `KeyError`, whatever) used to escape `bootstrap()` entirely, so `--json` gave no
    JSON at all — the one output contract a calling agent depends on.
    """

    def broken_run(argv):
        raise KeyError("boom")

    report = bootstrap(Options(venv=pathlib.Path(r"C:\venv"), dry_run=False), run=broken_run)
    assert report.ok is False
    assert report.steps[0].status == "failed"
    assert "KeyError" in report.steps[0].detail
    # Must still serialise: this is the actual contract check.
    payload = json.loads(report.to_json())
    assert set(payload) == {"ok", "version", "launcher", "steps", "next_command"}
