"""Shared helpers for bootstrap orchestration tests."""

from __future__ import annotations

import json
import sys

STEPS = [
    "interpreter",
    "venv",
    "install",
    "imports",
    "binaries",
    "launcher",
    "addon",
    "clients",
    "verify",
]


class Recorder:
    """Every subprocess succeeds; imports work first try.

    A call this fake was not built to answer raises rather than falling through to a
    success-shaped `(0, "")`: a step invoking the wrong interpreter, or shelling out
    with the wrong argv, must show up as a test failure, not disappear into a silent
    no-op that happens to look fine. That includes `argv[0]`, not just the `-c`
    source — matching only on source content would let a step probe a wrong or
    nonexistent interpreter path and still see a success-shaped payload back, which is
    exactly the failure mode this fake exists to catch. The `interpreter` step tries
    candidate interpreters (here, just `sys.executable`, since the module's own
    interpreter-discovery is neutralised by `no_stray_interpreters`); `imports` and
    `binaries` must always be called with the venv's python, never the system one.
    """

    def __init__(self, venv_python, system_python=None):
        self.calls: list[list[str]] = []
        self.venv_python = str(venv_python)
        self.system_python = str(system_python or sys.executable)

    def run(self, argv):
        argv = list(argv)
        self.calls.append(argv)
        interpreter = argv[0] if argv else None
        if argv[1:] == ["-m", "tbmcp", "detect-clients"]:
            if interpreter != self.venv_python:
                raise AssertionError(
                    f"detect-clients ran under {interpreter!r}, expected the venv's "
                    f"python {self.venv_python!r}"
                )
            return 0, json.dumps([])
        if "-c" in argv:
            source = argv[argv.index("-c") + 1]
            if "sqlite3" in source:
                if interpreter not in (self.system_python, self.venv_python):
                    raise AssertionError(
                        f"interpreter_ok probed {interpreter!r}, expected the chosen "
                        f"interpreter {self.system_python!r} or the venv's python "
                        f"{self.venv_python!r}"
                    )
                return 0, json.dumps({"ok": True, "version": "3.14"})
            if "__import__" in source:
                if interpreter != self.venv_python:
                    raise AssertionError(
                        f"import probe used {interpreter!r}, expected the venv's "
                        f"python {self.venv_python!r}"
                    )
                return 0, json.dumps({"ok": True})
            if "packages_distributions" in source:
                if interpreter != self.venv_python:
                    raise AssertionError(
                        f"distribution lookup used {interpreter!r}, expected the "
                        f"venv's python {self.venv_python!r}"
                    )
                return 0, json.dumps({"dist": None})
        raise AssertionError(f"Recorder was not built to answer this call: {argv}")
