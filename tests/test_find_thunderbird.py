from __future__ import annotations

import pytest

from tbmcp import thunderbird_process
from tbmcp.errors import TransportError

REAL_SAVED_USER_VARIABLE = thunderbird_process.saved_user_variable


@pytest.fixture(autouse=True)
def _without_executable_override(monkeypatch):
    monkeypatch.delenv("TBMCP_THUNDERBIRD", raising=False)
    monkeypatch.setattr(thunderbird_process, "running_command_lines", lambda: [])
    monkeypatch.setattr(thunderbird_process.shutil, "which", lambda _name: None)


def _running_exe(monkeypatch, tmp_path, command):
    exe = tmp_path / "ThunderbirdPortable" / "App" / "thunderbird64" / "thunderbird.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()
    monkeypatch.setattr(thunderbird_process, "running_command_lines", lambda: [command(str(exe))])
    monkeypatch.setattr(thunderbird_process.sys, "platform", "win32")
    for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.setenv(key, str(tmp_path / key.replace("/", "_")))
    return exe


def test_saved_user_variable_is_windows_only(monkeypatch):
    monkeypatch.setattr(thunderbird_process.sys, "platform", "linux")
    assert REAL_SAVED_USER_VARIABLE("TBMCP_PROFILE") is None


@pytest.mark.skipif(thunderbird_process.sys.platform != "win32", reason="Windows registry only")
def test_saved_user_variable_reads_registry(monkeypatch):
    import winreg
    from contextlib import nullcontext

    monkeypatch.setattr(winreg, "OpenKey", lambda *_args: nullcontext(object()))
    monkeypatch.setattr(winreg, "QueryValueEx", lambda *_args: (r"D:\profile", winreg.REG_SZ))
    assert REAL_SAVED_USER_VARIABLE("TBMCP_PROFILE") == r"D:\profile"
    monkeypatch.setattr(
        winreg, "QueryValueEx", lambda *_args: (_ for _ in ()).throw(FileNotFoundError())
    )
    assert REAL_SAVED_USER_VARIABLE("TBMCP_PROFILE") is None


def test_finds_unquoted_portable_executable_with_spaces(monkeypatch, tmp_path):
    exe = _running_exe(monkeypatch, tmp_path, lambda path: f"{path} -profile D:\\profile")
    assert thunderbird_process.find_thunderbird() == exe


def test_finds_quoted_executable_with_spaces(monkeypatch, tmp_path):
    exe = _running_exe(monkeypatch, tmp_path, lambda path: f'"{path}" -profile D:\\profile')
    assert thunderbird_process.find_thunderbird() == exe


def test_skips_content_process_and_missing_executable(monkeypatch, tmp_path):
    exe = _running_exe(monkeypatch, tmp_path, lambda path: f"{path} -contentproc")
    monkeypatch.setattr(
        thunderbird_process, "running_command_lines", lambda: [str(exe) + " -contentproc"]
    )
    assert thunderbird_process.running_executable() is None
    monkeypatch.setattr(
        thunderbird_process, "running_command_lines", lambda: [str(tmp_path / "thunderbird.exe")]
    )
    assert thunderbird_process.running_executable() is None


def test_executable_override_wins_and_missing_install_returns_none(monkeypatch, tmp_path):
    override = tmp_path / "override.exe"
    override.touch()
    running = tmp_path / "thunderbird.exe"
    running.touch()
    monkeypatch.setattr(thunderbird_process, "running_command_lines", lambda: [f'"{running}"'])
    monkeypatch.setenv("TBMCP_THUNDERBIRD", str(override))
    assert thunderbird_process.find_thunderbird() == override
    monkeypatch.delenv("TBMCP_THUNDERBIRD")
    monkeypatch.setattr(thunderbird_process, "running_command_lines", lambda: [])
    for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.setenv(key, str(tmp_path / key.replace("/", "_")))
    monkeypatch.setattr(thunderbird_process.sys, "platform", "win32")
    assert thunderbird_process.find_thunderbird() is None


def test_saved_executable_is_used_only_when_running_one_is_missing(monkeypatch, tmp_path):
    saved = tmp_path / "saved.exe"
    saved.touch()
    running = tmp_path / "thunderbird.exe"
    running.touch()
    monkeypatch.setattr(thunderbird_process.sys, "platform", "win32")
    monkeypatch.setattr(thunderbird_process, "saved_user_variable", lambda _name: str(saved))
    monkeypatch.setattr(thunderbird_process, "running_executable", lambda: None)
    assert thunderbird_process.find_thunderbird() == saved
    monkeypatch.setattr(thunderbird_process, "running_executable", lambda: running)
    assert thunderbird_process.find_thunderbird() == running
    saved.unlink()
    monkeypatch.setattr(thunderbird_process, "running_executable", lambda: None)
    for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.setenv(key, str(tmp_path / key))
    assert thunderbird_process.find_thunderbird() is None


def test_start_uses_configured_executable_and_detached_launcher(monkeypatch, tmp_path):
    exe = tmp_path / "configured-thunderbird.exe"
    calls = []
    monkeypatch.setattr(thunderbird_process, "find_thunderbird", lambda: exe)
    monkeypatch.setattr(
        thunderbird_process,
        "_launch",
        lambda executable, args, profile: calls.append((executable, args, profile)),
    )

    thunderbird_process.start_thunderbird()

    assert calls == [(exe, [], None)]


def test_start_uses_selected_profile(monkeypatch, tmp_path):
    from tbmcp.profile import ThunderbirdProfile

    exe = tmp_path / "configured-thunderbird.exe"
    profile = ThunderbirdProfile(
        path=tmp_path / "portable-profile",
        name="portable",
        is_default=False,
        root=tmp_path,
    )
    calls = []
    monkeypatch.setattr(thunderbird_process, "find_thunderbird", lambda: exe)
    monkeypatch.setattr(
        thunderbird_process,
        "_launch",
        lambda executable, args, selected: calls.append((executable, args, selected)),
    )

    thunderbird_process.start_thunderbird(profile)

    assert calls == [(exe, [], profile)]


def test_start_reports_missing_executable(monkeypatch):
    monkeypatch.setattr(thunderbird_process, "find_thunderbird", lambda: None)

    with pytest.raises(TransportError, match="Thunderbird executable") as exc:
        thunderbird_process.start_thunderbird()

    assert exc.value.code == "THUNDERBIRD_NOT_FOUND"


def test_start_reports_launch_failure(monkeypatch, tmp_path):
    exe = tmp_path / "thunderbird.exe"
    monkeypatch.setattr(thunderbird_process, "find_thunderbird", lambda: exe)

    def fail_to_launch(*_args):
        raise OSError("access denied")

    monkeypatch.setattr(thunderbird_process, "_launch", fail_to_launch)

    with pytest.raises(TransportError, match="could not start Thunderbird") as exc:
        thunderbird_process.start_thunderbird()

    assert exc.value.code == "THUNDERBIRD_START_FAILED"
