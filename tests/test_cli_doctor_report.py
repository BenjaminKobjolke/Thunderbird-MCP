from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from support_cli_doctor import (
    _ADDON_STATUS,
    _profile,
    _run_doctor,
)

from tbmcp.addon_build import source_digest
from tbmcp.cli import build_parser
from tbmcp.cli_commands import cmd_install_addon
from tbmcp.profile import ThunderbirdProfile


def test_doctor_names_profile_source_and_mismatch(monkeypatch, tmp_path, capsys):
    profile = ThunderbirdProfile(tmp_path, "portable", False, tmp_path, "running")
    _run_doctor(
        monkeypatch,
        [],
        profile=profile,
        bridge_status={
            "connected": False,
            "profile": {
                "path": "/wrong",
                "source": "explicit",
                "thunderbirdProfiles": ["/portable"],
            },
        },
        addon_summary={"thunderbirdRunning": True},
    )
    output = capsys.readouterr().out
    assert "profile chosen by" in output and "taken from the running Thunderbird" in output
    assert "/wrong" in output and "/portable" in output and "TBMCP_PROFILE" in output


def test_doctor_json_and_install_addon_profile_policy(monkeypatch, tmp_path, capsys):
    profile = ThunderbirdProfile(tmp_path, "portable", False, tmp_path, "running")
    _run_doctor(
        monkeypatch,
        ["--json"],
        profile=profile,
        bridge_status={"connected": True},
        addon_summary={"thunderbirdRunning": True},
    )
    assert json.loads(capsys.readouterr().out)["profileSource"] == "running"

    calls = []
    monkeypatch.setattr("tbmcp.profile.find_profile", lambda *a, **kw: calls.append(kw) or profile)
    monkeypatch.setattr("tbmcp.addon_install.manual_instructions", lambda: (None, "manual"))
    assert cmd_install_addon(build_parser().parse_args(["install-addon", "--manual"])) == 0
    assert calls == [{}]


@pytest.mark.usefixtures("clean_tbmcp_env")
def test_install_addon_uses_running_profile_when_environment_is_missing(monkeypatch, tmp_path):
    profile_path = tmp_path / "portable-profile"
    profile_path.mkdir()
    monkeypatch.setattr(
        "tbmcp.thunderbird_process.running_command_lines",
        lambda: [f'thunderbird.exe -profile "{profile_path}"'],
    )
    received = []

    def install(profile, **kwargs):
        received.append((profile, kwargs))
        return SimpleNamespace(ok=True, message="installed")

    monkeypatch.setattr("tbmcp.addon_install.install_automatic", install)
    assert cmd_install_addon(build_parser().parse_args(["install-addon", "--yes"])) == 0
    assert received[0][0].path == profile_path
    assert received[0][0].source == "running"


def test_the_json_report_compares_the_installed_add_on_with_the_source(
    monkeypatch, tmp_path, capsys
):
    exit_code = _run_doctor(
        monkeypatch,
        ["--json"],
        bridge_status={"connected": False},
        addon_summary={"addonVersion": "1.3.0", "thunderbirdRunning": True},
        profile=_profile(tmp_path, _ADDON_STATUS),
    )

    report = json.loads(capsys.readouterr().out)
    assert report["addonVersions"] == {
        "installed": "1.2.0",
        "live": None,
        "source": "1.3.0",
        "installedBuild": None,
        "sourceBuild": source_digest(),
        "mismatch": True,
    }
    assert exit_code == 1


@pytest.mark.usefixtures("clean_tbmcp_env")
def test_an_out_of_date_add_on_is_named_with_its_remedy(monkeypatch, tmp_path, capsys):
    exit_code = _run_doctor(
        monkeypatch,
        [],
        bridge_status={"connected": False},
        addon_summary={"addonVersion": "1.3.0", "thunderbirdRunning": True},
        profile=_profile(tmp_path, _ADDON_STATUS),
    )

    out = capsys.readouterr().out
    assert "add-on version (installed)" in out
    assert "installed add-on is 1.2.0 (build unknown), source is 1.3.0 (build" in out
    assert "tbmcp install-addon" in out
    assert exit_code == 1


@pytest.mark.usefixtures("clean_tbmcp_env")
def test_a_working_chain_on_an_older_add_on_is_a_warning_not_a_failure(
    monkeypatch, tmp_path, capsys
):
    """It works, so `ok` stays true — but the user is running code they replaced."""
    exit_code = _run_doctor(
        monkeypatch,
        [],
        bridge_status={
            "connected": True,
            "thunderbird": {"addonVersion": "1.2.0", "experiment": True, "app": {}},
        },
        addon_summary={"addonVersion": "1.3.0", "thunderbirdRunning": True},
        profile=_profile(tmp_path, _ADDON_STATUS),
    )

    out = capsys.readouterr().out
    assert "Warning: installed add-on is 1.2.0 (build unknown), source is 1.3.0 (build" in out
    assert exit_code == 0


@pytest.mark.usefixtures("clean_tbmcp_env")
def test_a_matching_add_on_says_nothing_about_versions(monkeypatch, tmp_path, capsys):
    exit_code = _run_doctor(
        monkeypatch,
        [],
        bridge_status={
            "connected": True,
            "thunderbird": {"addonVersion": "1.3.0", "experiment": True, "app": {}},
        },
        addon_summary={"addonVersion": "1.3.0", "thunderbirdRunning": True},
        profile=_profile(tmp_path, {**_ADDON_STATUS, "addonVersion": "1.3.0"}),
    )

    out = capsys.readouterr().out
    assert "Warning" not in out
    assert exit_code == 0
