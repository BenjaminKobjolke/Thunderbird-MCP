"""Broker Thunderbird's add-on connection to local MCP clients."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections import deque
from pathlib import Path
from typing import Any

from websockets.asyncio.server import ServerConnection, serve

from . import daemon_addon, daemon_control, daemon_lock, daemon_pairing, ipc
from .addon_session import AddonSession, ProgressCb
from .errors import NotConnectedError
from .handshake import HandshakeLog
from .profile import ThunderbirdProfile

log = logging.getLogger("tbmcp.daemon")

DEFAULT_IDLE_TIMEOUT = 900.0


class Daemon:
    def __init__(
        self,
        profile: ThunderbirdProfile,
        *,
        idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
        event_buffer: int = 500,
        startup_lock: daemon_lock.DaemonLock | None = None,
    ) -> None:
        self.profile = profile
        self.idle_timeout = idle_timeout
        self.startup_lock = startup_lock
        self.addon_token = ipc.new_token()
        self.control_token = ipc.new_token()
        self.handshakes = HandshakeLog()
        self.session: AddonSession | None = None
        self.events: deque[dict[str, Any]] = deque(maxlen=event_buffer)
        self.event_seq = 0
        self.started_at = time.time()
        self._clients = 0
        self._last_client_at = time.time()
        self._inflight: set[asyncio.Task[None]] = set()
        self._stop = asyncio.Event()
        self._session_ready = asyncio.Event()
        self.thunderbird_profiles: list[Path] = []
        self._followed_at = 0.0
        self._addon_port: int | None = None
        self._control_port: int | None = None

    # ------------------------------------------------------------------ add-on side

    async def _serve_addon(self, ws: ServerConnection) -> None:
        await daemon_addon.serve_addon(self, ws)

    async def _reject_hello(self, ws: ServerConnection, *, detail: str) -> None:
        await daemon_addon.reject_hello(self, ws, detail=detail)

    def _connection_class(self) -> type[ServerConnection]:
        """Report the whole TCP connection, including attempts without an upgrade."""
        handshakes = self.handshakes

        class TelemetryConnection(ServerConnection):
            def connection_made(self, transport: asyncio.BaseTransport) -> None:
                super().connection_made(transport)
                handshakes.opened(self)

            def connection_lost(self, exc: Exception | None) -> None:
                super().connection_lost(exc)
                handshakes.resolve(self, "no-upgrade")

        return TelemetryConnection

    def _on_upgrade_request(self, conn: ServerConnection, request: Any) -> None:
        daemon_addon.on_upgrade_request(self, conn, request)

    def _addon_server(self, **overrides: Any) -> serve:
        return daemon_addon.addon_server(self, **overrides)

    def _record_event(self, frame: dict[str, Any]) -> None:
        daemon_addon.record_event(self, frame)

    # ------------------------------------------------------------------ control side

    async def _serve_control(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await daemon_control.serve_control(self, reader, writer)

    async def _dispatch(self, message: dict[str, Any], writer: asyncio.StreamWriter) -> None:
        await daemon_control.dispatch(self, message, writer)

    async def _invoke(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float,
        on_progress: ProgressCb | None,
    ) -> Any:
        return await daemon_control.invoke(
            self, method, params, timeout=timeout, on_progress=on_progress
        )

    def _not_connected(self) -> NotConnectedError:
        return daemon_control.not_connected(self)

    def status(self) -> dict[str, Any]:
        return {
            "daemon": {
                "pid": os.getpid(),
                "uptimeSeconds": round(time.time() - self.started_at, 1),
                "clients": self._clients,
                "eventsBuffered": len(self.events),
                "latestEventSeq": self.event_seq,
            },
            "profile": {
                "path": str(self.profile.path),
                "name": self.profile.name,
                "source": self.profile.source,
                "thunderbirdProfiles": [str(p) for p in self.thunderbird_profiles],
            },
            "thunderbird": self.session.describe() if self.session else None,
            "connected": self.session is not None,
            "handshake": self.handshakes.summary(),
            # Nothing the daemon logs reaches a terminal — it is spawned detached
            # with its streams on DEVNULL — so every reporter needs the path.
            "logFile": str(ipc.daemon_log_path()),
        }

    # ------------------------------------------------------------------ lifecycle

    def _write_bridge_file(self, port: int) -> None:
        daemon_pairing.write_bridge_file(self, port)

    def _remove_bridge_file(self, path: Path | None = None) -> None:
        daemon_pairing.remove_bridge_file(self, path)

    def _advertise(self) -> None:
        daemon_pairing.advertise(self)

    async def _follow_thunderbird(self) -> None:
        await daemon_pairing.follow_thunderbird(self)

    async def _follow_then_pause(self) -> None:
        await daemon_pairing.follow_then_pause(self)

    def _cleanup(self) -> None:
        self._remove_bridge_file()
        ipc.DaemonInfo.clear_if_owned(os.getpid())

    async def _watch_idle(self) -> None:
        if self.idle_timeout <= 0:
            return
        while not self._stop.is_set():
            await asyncio.sleep(30.0)
            if self._clients == 0 and (time.time() - self._last_client_at) > self.idle_timeout:
                log.info("no clients for %.0fs — shutting down", self.idle_timeout)
                self._stop.set()

    async def _watch_superseded(self) -> None:
        """Stand down if another daemon has taken over the advertisement.

        Only one daemon can hold the add-on's connection, but nothing stops two from
        binding their own sockets. When that happened the add-on stayed glued to the
        older one while `serve` talked to the newer, and every tool reported
        "Thunderbird is not connected" — with both processes looking healthy. Losing
        the advertisement is the signal to exit and let the winner have the add-on.
        """
        while not self._stop.is_set():
            await asyncio.sleep(20.0)
            winner = self.superseded_by()
            if winner is not None:
                log.warning("daemon %d has taken over the advertisement — standing down", winner)
                self._stop.set()

    @staticmethod
    def superseded_by() -> int | None:
        """The pid that owns the advertisement, if it is not us."""
        advertised = ipc.DaemonInfo.load()
        if advertised is None or advertised.pid == os.getpid():
            return None
        return advertised.pid

    async def run(self) -> None:
        async with (
            await asyncio.start_server(
                self._serve_control, host="127.0.0.1", port=0, limit=ipc.MAX_LINE
            ) as control,
            self._addon_server() as addon_server,
        ):
            control_port = control.sockets[0].getsockname()[1]
            addon_port = addon_server.sockets[0].getsockname()[1]
            self._control_port = control_port
            self._write_bridge_file(addon_port)
            self._advertise()
            # Published: the startup race is over, so stop blocking other starts.
            if self.startup_lock is not None:
                self.startup_lock.release()
                self.startup_lock = None
            log.info("daemon ready (control %d, add-on %d)", control_port, addon_port)
            watchdogs = [
                asyncio.create_task(self._watch_idle()),
                asyncio.create_task(self._watch_superseded()),
            ]
            try:
                await self._stop.wait()
            finally:
                for task in watchdogs:
                    task.cancel()
                for task in watchdogs:
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                self._cleanup()
