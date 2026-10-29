"""Bridge behavior when Thunderbird is started on demand."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable

import pytest

from tbmcp import bridge_spawn, ipc
from tbmcp.bridge import Bridge
from tbmcp.errors import NotConnectedError

pytestmark = pytest.mark.anyio


def _advertise_daemon(port: int) -> None:
    ipc.DaemonInfo(
        version=ipc.PROTOCOL_VERSION,
        port=port,
        token="t",
        pid=os.getpid(),
        profile="",
    ).write()


def _not_connected_daemon(
    message: str = "Thunderbird is not connected.",
) -> tuple[list[str], Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]]:
    methods: list[str] = []

    async def daemon(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while (request := await ipc.read_message(reader)) is not None:
            if request.get("t") == "auth":
                await ipc.write_message(writer, {"t": "ready", "id": request["id"]})
                continue
            methods.append(request["method"])
            await ipc.write_message(
                writer,
                {
                    "t": "res",
                    "id": request["id"],
                    "ok": False,
                    "error": {
                        "kind": "transport",
                        "code": "NOT_CONNECTED",
                        "message": message,
                    },
                },
            )

    return methods, daemon


async def _call_while_thunderbird_is_running(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    *,
    retry: bool = True,
) -> tuple[NotConnectedError, list[str], list[str]]:
    methods, daemon = _not_connected_daemon(message)
    launches = []

    async with await asyncio.start_server(
        daemon, host="127.0.0.1", port=0, limit=ipc.MAX_LINE
    ) as server:
        _advertise_daemon(server.sockets[0].getsockname()[1])
        monkeypatch.setattr(
            bridge_spawn.thunderbird_process, "is_running", lambda: True, raising=False
        )
        monkeypatch.setattr(
            bridge_spawn.thunderbird_process,
            "start_thunderbird",
            lambda: launches.append("started"),
            raising=False,
        )
        bridge = Bridge(autostart=False)
        try:
            with pytest.raises(NotConnectedError) as exc:
                await bridge.call("messages.search", _retry=retry)
            return exc.value, methods, launches
        finally:
            await bridge.close()


async def test_closed_thunderbird_starts_once_and_original_calls_continue(
    isolated_state, monkeypatch
) -> None:
    methods = []
    running = False
    launches = []

    async def daemon(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while (request := await ipc.read_message(reader)) is not None:
            if request.get("t") == "auth":
                await ipc.write_message(writer, {"t": "ready", "id": request["id"]})
                continue
            methods.append(request["method"])
            result = (
                {"connected": True}
                if request["method"] == "daemon.waitForThunderbird"
                else request["method"]
            )
            await ipc.write_message(
                writer,
                {"t": "res", "id": request["id"], "ok": True, "result": result},
            )

    async with await asyncio.start_server(
        daemon, host="127.0.0.1", port=0, limit=ipc.MAX_LINE
    ) as server:
        _advertise_daemon(server.sockets[0].getsockname()[1])

        def start_thunderbird() -> None:
            nonlocal running
            launches.append("started")
            running = True

        monkeypatch.setattr(
            bridge_spawn.thunderbird_process, "is_running", lambda: running, raising=False
        )
        monkeypatch.setattr(
            bridge_spawn.thunderbird_process,
            "start_thunderbird",
            start_thunderbird,
            raising=False,
        )
        bridge = Bridge(autostart=False)
        try:
            results = await asyncio.gather(
                bridge.call("messages.search"), bridge.call("folders.list")
            )
            assert results == ["messages.search", "folders.list"]
            assert launches == ["started"]
            assert methods.count("daemon.waitForThunderbird") == 1
        finally:
            await bridge.close()


async def test_running_but_unattached_thunderbird_is_not_started_again(
    isolated_state, monkeypatch
) -> None:
    error, methods, launches = await _call_while_thunderbird_is_running(
        monkeypatch, "Thunderbird is not connected."
    )

    assert error.code == "NOT_CONNECTED"
    assert launches == []
    assert "daemon.waitForThunderbird" in methods


async def test_attachment_wait_timeout_preserves_not_connected_without_retry(
    isolated_state, monkeypatch
) -> None:
    timeout_message = "Thunderbird did not attach before the wait expired."
    error, methods, launches = await _call_while_thunderbird_is_running(
        monkeypatch, timeout_message, retry=False
    )

    assert error.code == "NOT_CONNECTED"
    assert error.message == timeout_message
    assert methods == ["daemon.waitForThunderbird", "messages.search"]
    assert launches == []
