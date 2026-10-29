"""The live Thunderbird add-on session and its in-flight requests."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from websockets.asyncio.server import ServerConnection
from websockets.exceptions import ConnectionClosed

from . import ipc
from .errors import NotConnectedError, TimeoutError_, TransportError, from_wire

log = logging.getLogger("tbmcp.daemon")

ProgressCb = Callable[[dict[str, Any]], Awaitable[None]]
PING_INTERVAL = 20.0


def error_payload(exc: BaseException) -> dict[str, Any]:
    """Serialise a failure into the protocol's `error` object.

    Deliberately reads `.message` rather than `str(exc)`. `TbmcpError.__str__` renders
    the code and the `needs` list into the text for humans, so relaying `str(exc)`
    appended them again on every hop: a "not connected" that crossed the add-on, the
    daemon and the bridge reached the caller as "... [NOT_CONNECTED] [NOT_CONNECTED]".
    Reading the field back keeps a relayed error identical to the one that was sent.
    """
    payload: dict[str, Any] = {
        "kind": getattr(exc, "kind", "internal"),
        "code": getattr(exc, "code", None),
        "message": getattr(exc, "message", None) or str(exc),
    }
    needs = getattr(exc, "needs", None)
    if needs:
        payload["needs"] = needs
    return payload


@dataclass
class _Pending:
    future: asyncio.Future[Any]
    method: str
    on_progress: ProgressCb | None = None
    chunks: list[bytes] = field(default_factory=list)


class AddonSession:
    """One live add-on connection."""

    def __init__(self, ws: ServerConnection, hello: dict[str, Any]) -> None:
        self.ws = ws
        self.hello = hello
        self.app: dict[str, Any] = hello.get("app") or {}
        self.capabilities: dict[str, Any] = hello.get("capabilities") or {}
        self.addon_version: str = str(hello.get("addonVersion", "?"))
        self.connected_at = time.time()
        self._next_id = 0
        self._pending: dict[int, _Pending] = {}
        self._closed = asyncio.Event()

    @property
    def has_experiment(self) -> bool:
        return bool(self.capabilities.get("experiment"))

    def describe(self) -> dict[str, Any]:
        return {
            "addonVersion": self.addon_version,
            "app": self.app,
            "experiment": self.has_experiment,
            "namespaces": self.capabilities.get("namespaces", []),
            "connectedForSeconds": round(time.time() - self.connected_at, 1),
            "inFlight": len(self._pending),
        }

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
        on_progress: ProgressCb | None = None,
    ) -> Any:
        if self._closed.is_set():
            raise NotConnectedError()
        if method.startswith("x.") and not self.has_experiment:
            raise TransportError(
                f"{method} needs the privileged half of the add-on, which did not load. "
                "Reinstall with `tbmcp install-addon` and check Thunderbird's error console.",
                code="NO_EXPERIMENT",
            )
        self._next_id += 1
        request_id = self._next_id
        loop = asyncio.get_running_loop()
        pending = _Pending(future=loop.create_future(), method=method, on_progress=on_progress)
        self._pending[request_id] = pending
        frame = {
            "t": "req",
            "id": request_id,
            "method": method,
            "params": params or {},
            "deadlineMs": int(timeout * 1000),
        }
        try:
            await self.ws.send(ipc.encode_message(frame).decode("utf-8"))
        except ConnectionClosed as exc:
            self._pending.pop(request_id, None)
            raise NotConnectedError() from exc
        try:
            return await asyncio.wait_for(pending.future, timeout=timeout)
        except TimeoutError:
            with contextlib.suppress(ConnectionClosed):
                await self.ws.send(json.dumps({"t": "cancel", "id": request_id}))
            raise TimeoutError_(method, timeout) from None
        finally:
            self._pending.pop(request_id, None)

    async def pump(self, on_event: Callable[[dict[str, Any]], None]) -> None:
        """Read frames until the add-on goes away."""
        try:
            async for raw in self.ws:
                try:
                    frame = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    log.warning("dropping malformed frame from add-on")
                    continue
                if not isinstance(frame, dict):
                    continue
                await self._handle(frame, on_event)
        except ConnectionClosed:
            pass
        finally:
            self._closed.set()
            for pending in self._pending.values():
                if not pending.future.done():
                    pending.future.set_exception(NotConnectedError())
            self._pending.clear()

    async def _handle(
        self, frame: dict[str, Any], on_event: Callable[[dict[str, Any]], None]
    ) -> None:
        kind = frame.get("t")
        if kind == "ping":
            with contextlib.suppress(ConnectionClosed):
                await self.ws.send(json.dumps({"t": "pong", "ts": frame.get("ts")}))
            return
        if kind == "event":
            on_event(frame)
            return
        if kind == "progress":
            pending = self._pending.get(int(frame.get("id", -1)))
            if pending and pending.on_progress:
                with contextlib.suppress(Exception):
                    await pending.on_progress(frame)
            return
        if kind != "res":
            return

        pending = self._pending.get(int(frame.get("id", -1)))
        if pending is None or pending.future.done():
            return

        chunk = frame.get("chunked")
        if isinstance(chunk, dict):
            payload = frame.get("result")
            if isinstance(payload, str):
                pending.chunks.append(base64.b64decode(payload))
            if not chunk.get("last"):
                return
            joined = b"".join(pending.chunks)
            try:
                pending.future.set_result(json.loads(joined.decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                pending.future.set_exception(
                    TransportError(f"chunked result was not valid JSON: {exc}", code="BAD_CHUNK")
                )
            return

        if frame.get("ok"):
            pending.future.set_result(frame.get("result"))
        else:
            error = frame.get("error")
            pending.future.set_exception(
                from_wire(pending.method, error if isinstance(error, dict) else {})
            )

    async def keepalive(self) -> None:
        while not self._closed.is_set():
            await asyncio.sleep(PING_INTERVAL)
            try:
                await asyncio.wait_for(self.ws.ping(), timeout=PING_INTERVAL)
            except (TimeoutError, ConnectionClosed):
                with contextlib.suppress(ConnectionClosed):
                    await self.ws.close(code=1011, reason="keepalive timeout")
                return
