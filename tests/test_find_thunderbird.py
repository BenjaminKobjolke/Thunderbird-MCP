from __future__ import annotations

import pytest

from tbmcp import addon_install


@pytest.fixture(autouse=True)
def _without_executable_override(monkeypatch):
    monkeypatch.delenv("TBMCP_THUNDERBIRD", raising=False)
    monkeypatch.setattr(addon_install, "running_command_lines", lambda: [])
    monkeypatch.setattr(addon_install.shutil, "which", lambda _name: None)


def _running_exe(monkeypatch, tmp_path, command):
    exe = tmp_path / "ThunderbirdPortable" / "App" / "thunderbird64" / "thunderbird.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()
    monkeypatch.setattr(addon_install, "running_command_lines", lambda: [command(str(exe))])
    monkeypatch.setattr(addon_install.sys, "platform", "win32")
    for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.setenv(key, str(tmp_path / key.replace("/", "_")))
    return exe


def test_finds_unquoted_portable_executable_with_spaces(monkeypatch, tmp_path):
    exe = _running_exe(monkeypatch, tmp_path, lambda path: f"{path} -profile D:\\profile")
    assert addon_install.find_thunderbird() == exe


def test_finds_quoted_executable_with_spaces(monkeypatch, tmp_path):
    exe = _running_exe(monkeypatch, tmp_path, lambda path: f'"{path}" -profile D:\\profile')
    assert addon_install.find_thunderbird() == exe


def test_skips_content_process_and_missing_executable(monkeypatch, tmp_path):
    exe = _running_exe(monkeypatch, tmp_path, lambda path: f"{path} -contentproc")
    monkeypatch.setattr(
        addon_install, "running_command_lines", lambda: [str(exe) + " -contentproc"]
    )
    assert addon_install.running_executable() is None
    monkeypatch.setattr(
        addon_install, "running_command_lines", lambda: [str(tmp_path / "thunderbird.exe")]
    )
    assert addon_install.running_executable() is None


def test_executable_override_wins_and_missing_install_returns_none(monkeypatch, tmp_path):
    override = tmp_path / "override.exe"
    override.touch()
    running = tmp_path / "thunderbird.exe"
    running.touch()
    monkeypatch.setattr(addon_install, "running_command_lines", lambda: [f'"{running}"'])
    monkeypatch.setenv("TBMCP_THUNDERBIRD", str(override))
    assert addon_install.find_thunderbird() == override
    monkeypatch.delenv("TBMCP_THUNDERBIRD")
    monkeypatch.setattr(addon_install, "running_command_lines", lambda: [])
    for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.setenv(key, str(tmp_path / key.replace("/", "_")))
    monkeypatch.setattr(addon_install.sys, "platform", "win32")
    assert addon_install.find_thunderbird() is None
