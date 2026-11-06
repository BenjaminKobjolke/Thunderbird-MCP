from __future__ import annotations

import asyncio

import pytest

from tbmcp import bridge_spawn, cli_commands, server
from tbmcp.cli import build_parser
from tbmcp.errors import TransportError


@pytest.fixture(autouse=True)
def reset_shared_bridge():
    yield
    bridge_spawn.set_shared_bridge(None)


@pytest.mark.parametrize("http", [False, True], ids=["stdio", "http"])
def test_serve_starts_without_waiting_for_readiness(monkeypatch, http):
    events = []

    class FakeBridge:
        async def ensure_ready(self):
            events.append("ready")

    class FakeServer:
        def run(self, **kwargs):
            events.append(kwargs)

    def fake_build_server(_settings, **kwargs):
        events.append(kwargs)
        return FakeServer()

    bridge = FakeBridge()
    monkeypatch.setattr(server, "build_server", fake_build_server)
    monkeypatch.setattr(bridge_spawn, "shared_bridge", lambda: bridge)
    argv = ["serve"]
    if http:
        argv.append("--http")

    assert cli_commands.cmd_serve(build_parser().parse_args(argv)) == 0

    build_kwargs, run_kwargs = events
    assert build_kwargs["lifespan"] is bridge_spawn.warm_up_lifespan
    assert run_kwargs["transport"] == ("streamable-http" if http else "stdio")
    assert "ready" not in events


def test_warm_up_lifespan_returns_while_readiness_is_in_flight():
    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()

        class FakeBridge:
            async def ensure_ready(self):
                started.set()
                await release.wait()

        bridge_spawn.set_shared_bridge(FakeBridge())

        async with bridge_spawn.warm_up_lifespan(None):
            await asyncio.wait_for(started.wait(), timeout=0.1)
            assert not release.is_set()

    asyncio.run(scenario())


def test_warm_up_lifespan_logs_readiness_failure(caplog):
    async def scenario():
        class FakeBridge:
            async def ensure_ready(self):
                raise TransportError("Thunderbird did not attach")

        bridge_spawn.set_shared_bridge(FakeBridge())
        async with bridge_spawn.warm_up_lifespan(None):
            await asyncio.sleep(0)

    with caplog.at_level("WARNING", logger="tbmcp.bridge"):
        asyncio.run(scenario())

    assert "Thunderbird warm-up failed" in caplog.text
    assert "Thunderbird did not attach" in caplog.text


def test_warm_up_lifespan_cancels_readiness_on_exit():
    async def scenario():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        class FakeBridge:
            async def ensure_ready(self):
                started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    raise

        bridge_spawn.set_shared_bridge(FakeBridge())
        async with bridge_spawn.warm_up_lifespan(None):
            await asyncio.wait_for(started.wait(), timeout=0.1)
        assert cancelled.is_set()

    asyncio.run(scenario())
