"""Daemon advertisement, line framing, and the size a frame is allowed to be."""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import time

import pytest

from tbmcp import bridge as bridge_module
from tbmcp import ipc
from tbmcp.bridge import Bridge
from tbmcp.daemon import Daemon
from tbmcp.errors import NotConnectedError, TransportError
from tbmcp.profile import ThunderbirdProfile

pytestmark = pytest.mark.anyio


def _advertise_daemon(port: int, token: str = "t") -> ipc.DaemonInfo:
    info = ipc.DaemonInfo(
        version=ipc.PROTOCOL_VERSION,
        port=port,
        token=token,
        pid=os.getpid(),
        profile="",
    )
    info.write()
    return info


def _stub_daemon(result: object):
    async def daemon(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(Exception):
            while True:
                message = await ipc.read_message(reader)
                if message is None:
                    return
                if message.get("t") == "auth":
                    await ipc.write_message(writer, {"t": "ready", "id": message["id"]})
                    continue
                await ipc.write_message(
                    writer,
                    {"t": "res", "id": message["id"], "ok": True, "result": result},
                )

    return daemon


async def test_a_large_result_reaches_the_caller(isolated_state) -> None:
    """The live failure, end to end: 200 search hits came back, the bridge's reader
    gave up mid-frame, and the call — plus every other one in flight — died as
    DISCONNECTED."""
    result = {"messages": [{"subject": "invoice", "preview": "x" * 1000} for _ in range(200)]}

    async with await asyncio.start_server(
        _stub_daemon(result), host="127.0.0.1", port=0, limit=ipc.MAX_LINE
    ) as server:
        _advertise_daemon(server.sockets[0].getsockname()[1])
        bridge = Bridge(autostart=False)
        try:
            # `daemon.status` is answered by the daemon itself, so nothing waits for
            # Thunderbird to attach.
            assert await bridge.call("daemon.status", timeout=10.0) == result
        finally:
            await bridge.close()


async def test_a_refused_advertisement_is_withdrawn_and_reads_as_no_daemon(
    isolated_state,
) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    _advertise_daemon(port, token="stale")
    client = Bridge(autostart=False)
    try:
        with pytest.raises(TransportError) as exc:
            await client.status()

        assert exc.value.code == "NO_DAEMON"
        assert not ipc.DaemonInfo.path().exists()
    finally:
        await client.close()


async def test_a_refused_advertisement_spawns_a_fresh_daemon(isolated_state, monkeypatch) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        stale_port = listener.getsockname()[1]

    result = {"running": True}

    async with await asyncio.start_server(
        _stub_daemon(result), host="127.0.0.1", port=0, limit=ipc.MAX_LINE
    ) as server:
        _advertise_daemon(stale_port, token="stale")

        async def spawn_fresh_daemon(_bridge: Bridge) -> None:
            _advertise_daemon(server.sockets[0].getsockname()[1], token="fresh")

        monkeypatch.setattr(bridge_module.Bridge, "_spawn_daemon", spawn_fresh_daemon)
        client = Bridge(autostart=True)
        try:
            assert await client.call("daemon.status", timeout=10.0) == result
        finally:
            await client.close()


async def test_daemon_local_wait_propagates_not_connected_without_retry(monkeypatch) -> None:
    bridge = Bridge(autostart=False)
    bridge._writer = _StubWriter()  # type: ignore[assignment]
    requests = []

    async def ensure() -> None:
        return None

    async def write_message(_writer, frame) -> None:
        requests.append(frame)
        bridge._pending[frame["id"]].set_exception(NotConnectedError())

    async def unexpected_wait() -> None:
        raise AssertionError("daemon-local calls must not wait for attachment")

    monkeypatch.setattr(bridge, "_ensure", ensure)
    monkeypatch.setattr(bridge, "_wait_for_attachment", unexpected_wait)
    monkeypatch.setattr(ipc, "write_message", write_message)

    with pytest.raises(NotConnectedError):
        await bridge.call("daemon.waitForThunderbird", {"timeout": 1}, timeout=2)

    assert len(requests) == 1


async def test_a_large_request_reaches_the_daemon(isolated_state) -> None:
    """The other direction: a mail_send carrying an attachment is a big frame too."""
    profile = ThunderbirdProfile(
        path=isolated_state, name="test", is_default=True, root=isolated_state
    )
    daemon = Daemon(profile, idle_timeout=0)
    running = asyncio.create_task(daemon.run())
    try:
        info = await _advertised_daemon()
        reader, writer = await asyncio.open_connection("127.0.0.1", info.port, limit=ipc.MAX_LINE)
        try:
            await ipc.write_message(writer, {"t": "auth", "id": 1, "token": info.token})
            ready = await asyncio.wait_for(ipc.read_message(reader), timeout=5.0)
            assert ready is not None and ready["t"] == "ready"
            await ipc.write_message(
                writer,
                {
                    "t": "req",
                    "id": 2,
                    "method": "daemon.status",
                    "params": {"attachment": "p" * 200_000},
                    "timeout": 5.0,
                },
            )
            reply = await asyncio.wait_for(ipc.read_message(reader), timeout=5.0)
            assert reply is not None and reply["ok"], reply
        finally:
            writer.close()
    finally:
        daemon._stop.set()
        await asyncio.wait_for(running, timeout=10.0)


async def _advertised_daemon() -> ipc.DaemonInfo:
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        info = ipc.DaemonInfo.load()
        if info is not None:
            return info
        await asyncio.sleep(0.05)
    raise AssertionError("the daemon never advertised itself")


class _StubWriter:
    """Just enough writer for what the read loop does on its way out."""

    def __init__(self) -> None:
        self._closing = False

    def close(self) -> None:
        self._closing = True

    def is_closing(self) -> bool:
        return self._closing


def _stub_bridge() -> tuple[Bridge, _StubWriter]:
    """A bridge holding a reader that cannot frame much, and a writer we can watch."""
    bridge = Bridge(autostart=False)
    bridge._reader = asyncio.StreamReader(limit=1024)
    writer = _StubWriter()
    bridge._writer = writer  # type: ignore[assignment]
    return bridge, writer


def _in_flight(bridge: Bridge) -> asyncio.Future:
    future = asyncio.get_running_loop().create_future()
    bridge._pending[1] = future
    return future


async def test_an_oversized_frame_fails_the_call_by_name() -> None:
    """DISCONNECTED sends whoever reads it looking at the daemon; the size is the one
    thing they need to know."""
    bridge, _ = _stub_bridge()
    pending = _in_flight(bridge)
    bridge._reader.feed_data(b"x" * 4096)  # no newline, well past the buffer
    await bridge._read_loop()
    assert pending.exception().code == "TOO_LARGE"


async def test_a_peer_that_hangs_up_still_reads_as_a_disconnect() -> None:
    bridge, _ = _stub_bridge()
    pending = _in_flight(bridge)
    bridge._reader.feed_eof()
    await bridge._read_loop()
    assert pending.exception().code == "DISCONNECTED"


async def test_the_read_loop_closes_the_writer_it_can_no_longer_read_for() -> None:
    """`_ensure` judges the connection by the writer alone. Left open after the reader
    died, the next call wrote into a socket nobody was reading and then waited out its
    whole timeout — one unreadable frame wedged the bridge for good."""
    bridge, writer = _stub_bridge()
    bridge._reader.feed_eof()
    await bridge._read_loop()
    assert writer.is_closing()


def test_state_dir_honours_an_explicit_override_on_every_platform(tmp_path, monkeypatch):
    """macOS reads no environment variable at all, so a test that pointed
    LOCALAPPDATA/XDG_STATE_HOME at a temp dir still wrote into the runner's real
    ~/Library — which is how the daemon-log test failed on the macOS runner only.
    One explicit override, honoured everywhere, is what CI and tests need."""
    monkeypatch.setenv("TBMCP_STATE_DIR", str(tmp_path / "override"))
    assert ipc.state_dir() == tmp_path / "override" / "tbmcp"
    assert (tmp_path / "override" / "tbmcp").is_dir()
