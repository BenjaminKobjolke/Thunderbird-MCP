"""Shared fixtures. Nothing here needs Thunderbird, or even a daemon.

The whole tool layer funnels through `Bridge.call`, so replacing the shared bridge
with a recorder is enough to exercise every tool end to end through a real MCP
client — argument validation, gating, and the shape of what comes back.
"""

from __future__ import annotations

from typing import Any

import pytest

from tbmcp import bridge_spawn as bridge_module
from tbmcp import safety, thunderbird_process
from tbmcp.config import Settings


@pytest.fixture
def anyio_backend() -> str:
    # The SDK is anyio-based; pinning asyncio keeps the suite off trio.
    return "asyncio"


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TBMCP_STATE_DIR", str(tmp_path))


class FakeBridge:
    """Stands in for the daemon connection.

    `responses` maps a bridge method to either a value or a callable taking the
    params. Anything unmapped returns `default`, which is enough for tools that only
    pass the result through.
    """

    def __init__(
        self,
        responses: dict[str, Any] | None = None,
        default: Any = None,
        connected: bool = True,
    ) -> None:
        self.responses = responses or {}
        self.default = {} if default is None else default
        self.connected = connected
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        on_progress: Any = None,
    ) -> Any:
        self.calls.append((method, dict(params or {})))
        if method in self.responses:
            handler = self.responses[method]
            return handler(params or {}) if callable(handler) else handler
        return self.default

    async def status(self) -> dict[str, Any]:
        return {"connected": self.connected, "thunderbird": {"experiment": True}}

    async def require_thunderbird(self, *, wait: float = 0.0) -> dict[str, Any]:
        return await self.status()

    async def close(self) -> None:
        return None

    # --- assertions used by the tests -----------------------------------

    def methods(self) -> list[str]:
        return [method for method, _ in self.calls]

    def params_for(self, method: str) -> dict[str, Any]:
        for name, params in self.calls:
            if name == method:
                return params
        raise AssertionError(f"{method} was never called; saw {self.methods()}")


@pytest.fixture
def fake_bridge():
    """Install a FakeBridge for the duration of a test, then restore the real one."""

    created: list[FakeBridge] = []

    def make(responses: dict[str, Any] | None = None, **kwargs: Any) -> FakeBridge:
        instance = FakeBridge(responses, **kwargs)
        bridge_module.set_shared_bridge(instance)  # type: ignore[arg-type]
        created.append(instance)
        return instance

    yield make
    bridge_module.set_shared_bridge(None)


@pytest.fixture(autouse=True)
def reset_settings():
    """Tool modules read the active policy from module state; keep tests isolated."""
    yield
    safety.set_settings(Settings())


@pytest.fixture(autouse=True)
def no_running_command_lines(monkeypatch):
    monkeypatch.setattr(thunderbird_process, "running_command_lines", lambda: [])
    monkeypatch.setattr(thunderbird_process, "saved_user_variable", lambda _name: None)


@pytest.fixture
def no_stray_interpreters(monkeypatch):
    """Keep bootstrap interpreter discovery independent of machine PATH contents."""
    monkeypatch.setattr("shutil.which", lambda name: None)


@pytest.fixture
def clean_tbmcp_env(monkeypatch):
    """`Settings.from_env()` reads these; strip them so the test's outcome depends
    only on what it configures explicitly, not on this machine's shell."""
    for var in (
        "TBMCP_TOOLSETS",
        "TBMCP_READ_ONLY",
        "TBMCP_YOLO",
        "TBMCP_UNSAFE_PREFS",
        "TBMCP_SEND",
        "TBMCP_PROFILE",
        "TBMCP_TIMEOUT",
        "TBMCP_NO_AUTOSTART",
        "TBMCP_TOOLS",
        "TBMCP_CONFIG",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("TBMCP_STATE_DIR", str(tmp_path))
    from tbmcp.ipc import DaemonInfo

    DaemonInfo.clear()
    yield tmp_path
    DaemonInfo.clear()
