"""What the admin toolset says when Thunderbird is not attached.

`tb_status` and `tb_diagnostics` are the two tools a model reaches for the moment
anything else reports that it cannot reach Thunderbird, so their `hint` is the whole
diagnosis as far as the model is concerned. It used to be one fixed sentence —
"ask the user to start Thunderbird" — which was actively wrong on the morning the
add-on was dialling in twice a minute and failing the handshake every time.
"""

from __future__ import annotations

import shutil
import time

import pytest
from mcp import Client

from tbmcp.addon_build import addon_id, build_xpi
from tbmcp.config import Settings
from tbmcp.server import build_server

pytestmark = pytest.mark.anyio

FAILING_HANDSHAKE = {
    "attempts": 3,
    "lastAttemptAt": time.time() - 5,
    "lastOutcome": "no-upgrade",
    "lastCloseCode": None,
    "lastUserAgent": "Thunderbird/155.0",
    "recentFailures": 3,
    "recentOutcomes": {"no-upgrade": 3},
    "windowSeconds": 60.0,
    "recent": [],
}

QUIET_HANDSHAKE = {
    "attempts": 0,
    "lastAttemptAt": None,
    "lastOutcome": None,
    "lastCloseCode": None,
    "lastUserAgent": None,
    "recentFailures": 0,
    "recentOutcomes": {},
    "windowSeconds": 60.0,
    "recent": [],
}


def _server(bridge):
    return build_server(Settings().merged_with(toolsets=("admin",)), bridge=bridge)


def _daemon_status(handshake: dict | None) -> dict:
    return {
        "daemon": {"pid": 1234, "uptimeSeconds": 60.0, "clients": 1},
        "profile": {"path": "/profile", "name": "default"},
        "thunderbird": None,
        "connected": False,
        "handshake": handshake,
        "logFile": "/state/tbmcp/daemon.log",
    }


async def test_tb_status_explains_a_failing_handshake_rather_than_blaming_the_user(
    fake_bridge,
) -> None:
    bridge = fake_bridge({"daemon.status": _daemon_status(FAILING_HANDSHAKE)})

    async with Client(_server(bridge)) as client:
        result = await client.call_tool("tb_status", {})

    payload = result.structured_content
    assert payload["handshake"]["recentFailures"] == 3
    assert "never completed the handshake" in payload["hint"]
    assert "Ask the user to start Thunderbird" not in payload["hint"]


async def test_tb_status_keeps_the_usual_hint_when_nothing_has_dialled_in(fake_bridge) -> None:
    """No attempts is the ordinary "Thunderbird is closed" case, and the ordinary
    advice is the right advice for it."""
    bridge = fake_bridge({"daemon.status": _daemon_status(QUIET_HANDSHAKE)})

    async with Client(_server(bridge)) as client:
        result = await client.call_tool("tb_status", {})

    payload = result.structured_content
    assert "Ask the user to start Thunderbird" in payload["hint"]
    assert payload["handshake"] == QUIET_HANDSHAKE


async def test_tb_status_survives_a_daemon_too_old_to_report_handshakes(fake_bridge) -> None:
    bridge = fake_bridge({"daemon.status": _daemon_status(None)})

    async with Client(_server(bridge)) as client:
        result = await client.call_tool("tb_status", {})

    payload = result.structured_content
    assert payload["handshake"] is None
    assert "Ask the user to start Thunderbird" in payload["hint"]


@pytest.mark.parametrize("installed", ["stale", "matching", "missing"])
async def test_tb_status_reports_stale_build(fake_bridge, tmp_path, installed) -> None:
    status = _daemon_status(None)
    status["connected"] = True
    status["thunderbird"] = {"addonVersion": "1.3.0", "experiment": True}
    status["profile"]["path"] = str(tmp_path)
    if installed != "missing":
        extensions = tmp_path / "extensions"
        extensions.mkdir()
        target = extensions / f"{addon_id()}.xpi"
        shutil.copyfile(build_xpi(tmp_path / "package"), target)
        if installed == "stale":
            import zipfile

            with zipfile.ZipFile(target, "a") as zf:
                zf.writestr("changed.js", "// stale")
    bridge = fake_bridge({"daemon.status": status})
    async with Client(_server(bridge)) as client:
        payload = (await client.call_tool("tb_status", {})).structured_content
    assert payload["addonBuild"]["stale"] is (installed == "stale")
    assert ("tbmcp install-addon" in payload.get("hint", "")) is (installed == "stale")


async def test_tb_status_without_profile_omits_build(fake_bridge) -> None:
    status = _daemon_status(None)
    status["profile"] = None
    bridge = fake_bridge({"daemon.status": status})
    async with Client(_server(bridge)) as client:
        payload = (await client.call_tool("tb_status", {})).structured_content
    assert "addonBuild" not in payload


async def test_tb_diagnostics_carries_the_handshake_record_and_the_diagnosis(
    fake_bridge,
) -> None:
    bridge = fake_bridge({"daemon.status": _daemon_status(FAILING_HANDSHAKE)})

    async with Client(_server(bridge)) as client:
        result = await client.call_tool("tb_diagnostics", {})

    payload = result.structured_content
    assert payload["connected"] is False
    assert payload["handshake"]["lastOutcome"] == "no-upgrade"
    assert "never completed the handshake" in payload["hint"]
    # The privileged half was never asked: there is nothing to ask it through.
    assert bridge.methods() == ["daemon.status"]
