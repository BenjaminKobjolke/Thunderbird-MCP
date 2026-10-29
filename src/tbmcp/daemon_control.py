"""Control socket authentication, request dispatch, and local daemon methods."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

from . import ipc
from .addon_session import ProgressCb, error_payload
from .errors import NotConnectedError, TransportError
from .handshake import describe_handshake

if TYPE_CHECKING:
    from .daemon import Daemon

log = logging.getLogger("tbmcp.daemon")


async def serve_control(
    daemon: Daemon, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    peer = writer.get_extra_info("peername")
    if not peer or peer[0] not in ("127.0.0.1", "::1"):
        writer.close()
        return
    authenticated = False
    daemon._clients += 1
    try:
        while True:
            message = await ipc.read_message(reader)
            if message is None:
                return
            if not authenticated:
                if message.get("t") != "auth" or message.get("token") != daemon.control_token:
                    await ipc.write_message(
                        writer,
                        {
                            "t": "res",
                            "id": message.get("id"),
                            "ok": False,
                            "error": {
                                "kind": "transport",
                                "code": "UNAUTHORIZED",
                                "message": "bad control token",
                            },
                        },
                    )
                    return
                authenticated = True
                await ipc.write_message(writer, {"t": "ready", "id": message.get("id")})
                continue
            task = asyncio.create_task(dispatch(daemon, message, writer))
            daemon._inflight.add(task)
            task.add_done_callback(daemon._inflight.discard)
    except TransportError as exc:
        log.warning("control connection error: %s", exc)
    finally:
        daemon._clients -= 1
        daemon._last_client_at = time.time()
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()


async def dispatch(daemon: Daemon, message: dict[str, Any], writer: asyncio.StreamWriter) -> None:
    request_id = message.get("id")
    method = str(message.get("method", ""))
    params = message.get("params") or {}
    timeout = float(message.get("timeout") or 30.0)
    wants_progress = bool(message.get("wantsProgress"))

    async def forward_progress(frame: dict[str, Any]) -> None:
        await ipc.write_message(
            writer,
            {
                "t": "progress",
                "id": request_id,
                "done": frame.get("done"),
                "total": frame.get("total"),
                "message": frame.get("message"),
            },
        )

    try:
        result = await invoke(
            daemon,
            method,
            params,
            timeout=timeout,
            on_progress=forward_progress if wants_progress else None,
        )
    except Exception as exc:
        with contextlib.suppress(Exception):
            await ipc.write_message(
                writer, {"t": "res", "id": request_id, "ok": False, "error": error_payload(exc)}
            )
        return
    with contextlib.suppress(Exception):
        await ipc.write_message(
            writer, {"t": "res", "id": request_id, "ok": True, "result": result}
        )


async def invoke(
    daemon: Daemon,
    method: str,
    params: dict[str, Any],
    *,
    timeout: float,
    on_progress: ProgressCb | None,
) -> Any:
    """Daemon-local methods first, then anything the add-on handles."""
    if method == "daemon.status":
        await daemon._follow_thunderbird()
        return daemon.status()
    if method == "daemon.events":
        since = int(params.get("since") or 0)
        limit = int(params.get("limit") or 100)
        selected = [event for event in daemon.events if event["seq"] > since][:limit]
        return {"events": selected, "latestSeq": daemon.event_seq}
    if method == "daemon.waitForThunderbird":
        wait = float(params.get("timeout") or 30.0)
        deadline = time.monotonic() + wait
        while not daemon._session_ready.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise not_connected(daemon)
            racers = [
                asyncio.ensure_future(daemon._session_ready.wait()),
                asyncio.ensure_future(daemon._follow_then_pause()),
            ]
            try:
                done, _ = await asyncio.wait(
                    racers, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    task.result()
            finally:
                for task in racers:
                    task.cancel()
        return daemon.status()
    if method == "daemon.shutdown":
        daemon._stop.set()
        return {"stopping": True}

    session = daemon.session
    if session is None:
        raise not_connected(daemon)
    return await session.call(method, params, timeout=timeout, on_progress=on_progress)


def not_connected(daemon: Daemon) -> NotConnectedError:
    """Include handshake diagnosis with failures from the disconnected daemon."""
    return NotConnectedError(describe_handshake(daemon.handshakes.summary()))
