from __future__ import annotations

import pytest

from tbmcp import bridge_spawn, cli_commands, server
from tbmcp.cli import build_parser


@pytest.mark.parametrize("http", [False, True], ids=["stdio", "http"])
def test_serve_waits_for_readiness_and_closes_preflight_bridge(monkeypatch, http):
    events = []

    class FakeServer:
        def run(self, **kwargs):
            events.append(("run", kwargs))

    class FakeBridge:
        async def ensure_ready(self):
            events.append(("ready",))

        async def close(self):
            events.append(("close",))

    monkeypatch.setattr(server, "build_server", lambda _settings: FakeServer())
    monkeypatch.setattr(bridge_spawn, "shared_bridge", FakeBridge, raising=False)
    argv = ["serve"]
    if http:
        argv.append("--http")
    args = build_parser().parse_args(argv)

    assert cli_commands.cmd_serve(args) == 0

    assert [event[0] for event in events] == ["ready", "close", "run"]
    run_kwargs = events[-1][1]
    assert run_kwargs["transport"] == ("streamable-http" if http else "stdio")


@pytest.mark.parametrize("http", [False, True], ids=["stdio", "http"])
def test_serve_does_not_start_transport_when_readiness_fails(monkeypatch, http):
    events = []

    class FakeServer:
        def run(self, **kwargs):
            events.append(("run", kwargs))

    class FakeBridge:
        async def ensure_ready(self):
            events.append(("ready",))
            raise RuntimeError("Thunderbird did not attach")

        async def close(self):
            events.append(("close",))

    monkeypatch.setattr(server, "build_server", lambda _settings: FakeServer())
    monkeypatch.setattr(bridge_spawn, "shared_bridge", FakeBridge, raising=False)
    argv = ["serve"]
    if http:
        argv.append("--http")
    args = build_parser().parse_args(argv)

    with pytest.raises(RuntimeError, match="Thunderbird did not attach"):
        cli_commands.cmd_serve(args)

    assert [event[0] for event in events] == ["ready", "close"]
