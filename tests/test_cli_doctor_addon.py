from __future__ import annotations

import json
import shutil

import pytest
from support_cli_doctor import (
    _ADDON_STATUS,
    _FAILING_HANDSHAKE,
    _TRANSPORT,
    _profile,
    _run_doctor,
    _stub_common,
    _stub_server,
)

from tbmcp.addon_build import addon_id, build_xpi, source_digest
from tbmcp.cli import build_parser
from tbmcp.cli_doctor import cmd_doctor


@pytest.mark.usefixtures("clean_tbmcp_env")
def test_repeated_handshake_failures_replace_the_try_again_in_a_moment_advice(
    monkeypatch, tmp_path, capsys
):
    """The advice that was wrong all morning: the add-on had dialled in dozens of
    times, so telling the user to wait a moment sent them nowhere."""
    exit_code = _run_doctor(
        monkeypatch,
        [],
        bridge_status={"connected": False, "handshake": _FAILING_HANDSHAKE},
        addon_summary={"addonVersion": "1.2.0", "thunderbirdRunning": True},
        profile=_profile(tmp_path, _ADDON_STATUS),
    )

    out = capsys.readouterr().out
    # The diagnosis is a wrapped paragraph, so read it as one flowing sentence.
    flat = " ".join(out.split())
    assert "add-on handshakes" in out
    assert "3 attempts" in out
    assert "never completed the handshake" in flat
    assert "try again in a moment" not in flat
    assert exit_code == 1


@pytest.mark.usefixtures("clean_tbmcp_env")
def test_the_add_ons_own_account_of_the_connection_is_printed(monkeypatch, tmp_path, capsys):
    """The add-on's side of the same story, from the file it writes itself: without
    it a reader cannot tell a daemon nobody dialled from an add-on that keeps
    failing to."""
    _run_doctor(
        monkeypatch,
        [],
        bridge_status={"connected": False},
        addon_summary={"addonVersion": "1.2.0", "thunderbirdRunning": True},
        profile=_profile(tmp_path, {**_ADDON_STATUS, "transport": _TRANSPORT}),
    )

    out = capsys.readouterr().out
    assert "add-on transport" in out
    assert "connecting" in out
    assert "7 failed attempts" in out
    assert "1006" in out


@pytest.mark.usefixtures("clean_tbmcp_env")
def test_the_daemon_log_is_named_because_nothing_else_shows_it(monkeypatch, tmp_path, capsys):
    _run_doctor(
        monkeypatch,
        [],
        bridge_status={"connected": False},
        addon_summary={"addonVersion": "1.2.0", "thunderbirdRunning": False},
        profile=_profile(tmp_path),
    )

    out = capsys.readouterr().out
    assert "daemon log" in out
    assert "daemon.log" in out
    assert "add-on handshakes" in out
    assert "none since the daemon started" in out


@pytest.mark.usefixtures("clean_tbmcp_env")
def test_a_stale_addon_is_named_even_while_thunderbird_is_closed(monkeypatch, tmp_path, capsys):
    """The remedy used to hide behind "Thunderbird is not running": a user with a stale
    add-on and a closed client was told only to start it, and came straight back."""
    _run_doctor(
        monkeypatch,
        [],
        bridge_status={"connected": False},
        addon_summary={"addonVersion": "1.3.0", "thunderbirdRunning": False},
        profile=_profile(tmp_path, {**_ADDON_STATUS, "addonVersion": "1.2.0"}),
    )

    out = capsys.readouterr().out
    assert "Thunderbird is not running" in out
    assert "installed add-on is 1.2.0 (build unknown), source is 1.3.0 (build" in out
    assert "tbmcp install-addon" in out


@pytest.mark.usefixtures("clean_tbmcp_env")
@pytest.mark.parametrize("installed", ["stale", "matching", "missing"])
def test_doctor_detects_same_version_stale_build(monkeypatch, tmp_path, capsys, installed):
    (tmp_path / "profile").mkdir()
    profile = _profile(tmp_path / "profile", {**_ADDON_STATUS, "addonVersion": "1.3.0"})
    if installed != "missing":
        extensions = profile.path / "extensions"
        extensions.mkdir()
        xpi = build_xpi(tmp_path / "package")
        target = extensions / f"{addon_id()}.xpi"
        shutil.copyfile(xpi, target)
        if installed == "stale":
            import zipfile

            with zipfile.ZipFile(target, "a") as zf:
                zf.writestr("changed.js", "// stale")
    _run_doctor(
        monkeypatch,
        ["--json"],
        bridge_status={"connected": True, "thunderbird": {"addonVersion": "1.3.0"}},
        addon_summary={"addonVersion": "1.3.0", "thunderbirdRunning": True},
        profile=profile,
    )
    report = json.loads(capsys.readouterr().out)
    assert report["addonVersions"]["mismatch"] is (installed == "stale")
    assert report["addonVersions"]["sourceBuild"] == source_digest()
    assert bool(report["addonVersions"]["installedBuild"]) is (installed != "missing")
    _run_doctor(
        monkeypatch,
        [],
        bridge_status={"connected": True, "thunderbird": {"addonVersion": "1.3.0"}},
        addon_summary={"addonVersion": "1.3.0", "thunderbirdRunning": True},
        profile=profile,
    )
    text = capsys.readouterr().out
    assert ("Warning: installed add-on is" in text) is (installed == "stale")
    assert "add-on build (installed)" in text


@pytest.mark.usefixtures("clean_tbmcp_env")
def test_the_daemon_line_reflects_the_daemon_the_probe_started(monkeypatch, tmp_path, capsys):
    """`doctor` read the advertisement before its probe autostarted a daemon, so the
    most common first run printed `daemon: not running` directly above
    `connected: True` — and shipped `daemon.running: false` in --json."""
    from types import SimpleNamespace

    from tbmcp.ipc import DaemonInfo

    _stub_common(
        monkeypatch,
        bridge_status={"connected": True, "thunderbird": {"addonVersion": "1.3.0"}},
        addon_summary={"addonVersion": "1.3.0", "thunderbirdRunning": True},
        profile=_profile(tmp_path, {**_ADDON_STATUS, "addonVersion": "1.3.0"}),
    )
    _stub_server(monkeypatch, connected=True)
    # Nothing is advertised until the probe has run; the advertisement must be read
    # after it, never before. `probed` flips when the (fake) bridge is asked to
    # connect, so a read that happens too early sees None.
    probed = {"done": False}

    def load(cls):
        return SimpleNamespace(pid=4242, port=5151) if probed["done"] else None

    monkeypatch.setattr(DaemonInfo, "load", classmethod(load))
    import tbmcp.bridge as bridge_module

    fake_bridge_cls = bridge_module.Bridge
    original_require = fake_bridge_cls.require_thunderbird

    async def require_and_mark(self, *, wait=0.0):
        probed["done"] = True
        return await original_require(self, wait=wait)

    monkeypatch.setattr(fake_bridge_cls, "require_thunderbird", require_and_mark)

    exit_code = cmd_doctor(build_parser().parse_args(["doctor", "--json", "--wait", "0"]))

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert report["daemon"]["running"] is True
    assert report["daemon"]["pid"] == 4242
    assert report["daemon"]["logFile"].endswith("daemon.log")
