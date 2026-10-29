"""Daemon add-on handshake telemetry and lifecycle coverage.

Every case here comes from something that actually went wrong during development, and
each one presented as "Thunderbird is not connected" with every process looking
healthy — which is why they are pinned down as tests rather than left to judgement:

- a second daemon binding its own sockets while the add-on stayed attached to the
  first, so `serve` and the add-on were talking to different processes;
- a live process holding the start-up lock without ever advertising, which made every
  later daemon stand down and left the bridge down until that process died.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time

import pytest
from support_daemon import _daemon
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

from tbmcp import ipc
from tbmcp.daemon import Daemon
from tbmcp.errors import NotConnectedError
from tbmcp.profile import BRIDGE_FILE

pytestmark = pytest.mark.anyio


def _hello(token: str, *, protocol: object = ipc.PROTOCOL_VERSION) -> str:
    return json.dumps(
        {
            "t": "hello",
            "token": token,
            "protocol": protocol,
            "addonVersion": "1.3.0",
            "app": {"name": "Thunderbird", "version": "155.0"},
            "capabilities": {"experiment": True, "namespaces": ["messages"]},
        }
    )


async def _settled(daemon: Daemon, predicate, *, timeout: float = 2.0) -> dict:
    """The summary once it says what the test is waiting for.

    Both ends of a close race: the client is told before the server's handler has
    finished unwinding, and a connection that never upgrades is only recorded when
    websockets gives up on it.
    """
    deadline = time.monotonic() + timeout
    while True:
        summary = daemon.handshakes.summary()
        if predicate(summary):
            return summary
        if time.monotonic() >= deadline:
            raise AssertionError(f"handshake summary never settled: {summary}")
        await asyncio.sleep(0.02)


class TestHandshakeTelemetry:
    async def test_a_client_that_never_says_hello_is_recorded(
        self, isolated_state, monkeypatch
    ) -> None:
        monkeypatch.setattr("tbmcp.daemon_addon.HELLO_TIMEOUT", 0.3)
        daemon = _daemon(isolated_state)
        async with daemon._addon_server(open_timeout=0.3) as server:
            port = server.sockets[0].getsockname()[1]
            async with ws_connect(f"ws://127.0.0.1:{port}/tbmcp") as ws:
                with pytest.raises(ConnectionClosed):
                    await asyncio.wait_for(ws.recv(), timeout=5.0)
                assert ws.close_code == 4002

        summary = daemon.status()["handshake"]
        assert summary["lastOutcome"] == "no-hello-timeout"
        assert summary["recentFailures"] == 1

    async def test_a_stale_token_is_recorded_without_being_logged(
        self, isolated_state, caplog
    ) -> None:
        daemon = _daemon(isolated_state)
        with caplog.at_level(logging.DEBUG):
            async with daemon._addon_server(open_timeout=0.3) as server:
                port = server.sockets[0].getsockname()[1]
                async with ws_connect(f"ws://127.0.0.1:{port}/tbmcp") as ws:
                    await ws.send(_hello("not-the-token"))
                    with pytest.raises(ConnectionClosed):
                        await asyncio.wait_for(ws.recv(), timeout=5.0)
                    assert ws.close_code == 4001

        summary = daemon.handshakes.summary()
        assert summary["lastOutcome"] == "bad-token"
        assert daemon.addon_token not in caplog.text, "a token must never reach a log"

    async def test_a_non_numeric_protocol_is_a_mismatch_not_a_crash(
        self, isolated_state, caplog
    ) -> None:
        """`int(hello["protocol"])` on a string raised straight out of the handler:
        websockets logged a traceback and the add-on learnt nothing."""
        daemon = _daemon(isolated_state)
        with caplog.at_level(logging.DEBUG):
            async with daemon._addon_server(open_timeout=0.3) as server:
                port = server.sockets[0].getsockname()[1]
                async with ws_connect(f"ws://127.0.0.1:{port}/tbmcp") as ws:
                    await ws.send(_hello(daemon.addon_token, protocol="x"))
                    with pytest.raises(ConnectionClosed):
                        await asyncio.wait_for(ws.recv(), timeout=5.0)
                    assert ws.close_code == 4002

        assert daemon.handshakes.summary()["lastOutcome"] == "protocol-mismatch"
        assert "Traceback" not in caplog.text

    async def test_a_good_hello_is_welcomed_and_then_disconnects(self, isolated_state) -> None:
        daemon = _daemon(isolated_state)
        async with daemon._addon_server(open_timeout=0.3) as server:
            port = server.sockets[0].getsockname()[1]
            async with ws_connect(f"ws://127.0.0.1:{port}/tbmcp") as ws:
                await ws.send(_hello(daemon.addon_token))
                welcome = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                assert welcome["t"] == "welcome"
                assert daemon.status()["connected"] is True

            summary = await _settled(daemon, lambda s: s["lastOutcome"] == "disconnected")

        assert summary["lastCloseCode"] == 1000
        assert summary["recentFailures"] == 0
        assert summary["recent"][-1]["addonVersion"] == "1.3.0"

    async def test_the_session_that_is_replaced_reads_as_superseded(self, isolated_state) -> None:
        daemon = _daemon(isolated_state)
        async with daemon._addon_server(open_timeout=0.3) as server:
            port = server.sockets[0].getsockname()[1]
            async with ws_connect(f"ws://127.0.0.1:{port}/tbmcp") as first:
                await first.send(_hello(daemon.addon_token))
                await asyncio.wait_for(first.recv(), timeout=5.0)

                async with ws_connect(f"ws://127.0.0.1:{port}/tbmcp") as second:
                    await second.send(_hello(daemon.addon_token))
                    await asyncio.wait_for(second.recv(), timeout=5.0)
                    with pytest.raises(ConnectionClosed):
                        await asyncio.wait_for(first.recv(), timeout=5.0)
                    assert first.close_code == 1012

                    summary = await _settled(
                        daemon, lambda s: s["recent"][0]["outcome"] == "superseded"
                    )

        assert [row["outcome"] for row in summary["recent"]] == ["superseded", "welcomed"]

    async def test_a_connection_that_never_upgrades_is_recorded(self, isolated_state) -> None:
        """The one the handler cannot see: the socket opens, nothing is sent, and
        websockets drops it when `open_timeout` expires without ever calling us."""
        daemon = _daemon(isolated_state)
        async with daemon._addon_server(open_timeout=0.3) as server:
            port = server.sockets[0].getsockname()[1]
            _reader, writer = await asyncio.open_connection("127.0.0.1", port)
            try:
                summary = await _settled(daemon, lambda s: s["lastOutcome"] == "no-upgrade")
            finally:
                writer.close()
                with contextlib.suppress(OSError):
                    await writer.wait_closed()

        assert summary["recentFailures"] == 1

    async def test_a_failing_handshake_reaches_the_caller_of_a_tool(
        self, isolated_state, monkeypatch
    ) -> None:
        """The payoff: every "not connected" now carries why, instead of leaving the
        model to guess between "start Thunderbird" and a broken add-on."""
        monkeypatch.setattr("tbmcp.daemon_addon.HELLO_TIMEOUT", 0.3)
        daemon = _daemon(isolated_state)
        async with daemon._addon_server(open_timeout=0.3) as server:
            port = server.sockets[0].getsockname()[1]
            async with ws_connect(f"ws://127.0.0.1:{port}/tbmcp") as ws:
                with pytest.raises(ConnectionClosed):
                    await asyncio.wait_for(ws.recv(), timeout=5.0)

            with pytest.raises(NotConnectedError) as caught:
                await daemon._invoke("messages.query", {}, timeout=1, on_progress=None)

        assert "never completed the handshake" in caught.value.message

    def test_standing_down_leaves_the_winners_files_alone(self, isolated_state) -> None:
        """`_cleanup` unlinked both files unconditionally, so a daemon that stood
        down because it had been superseded deleted the *winner's* pairing file and
        advertisement — and the add-on was left holding a token nobody listened for."""
        daemon = _daemon(isolated_state)
        bridge_file = isolated_state / BRIDGE_FILE
        bridge_file.write_text(json.dumps({"port": 1, "token": "theirs", "pid": 999_001}))
        ipc.DaemonInfo(
            version=ipc.PROTOCOL_VERSION, port=1, token="theirs", pid=999_001, profile=""
        ).write()

        daemon._cleanup()

        assert bridge_file.is_file(), "deleted the winner's pairing file"
        assert ipc.DaemonInfo.path().is_file(), "deleted the winner's advertisement"

    def test_a_daemon_still_clears_up_after_itself(self, isolated_state) -> None:
        daemon = _daemon(isolated_state)
        daemon._write_bridge_file(51234)
        ipc.DaemonInfo(
            version=ipc.PROTOCOL_VERSION,
            port=1,
            token="ours",
            pid=os.getpid(),
            profile=str(isolated_state),
        ).write()

        daemon._cleanup()

        assert not (isolated_state / BRIDGE_FILE).exists()
        assert not ipc.DaemonInfo.path().exists()
