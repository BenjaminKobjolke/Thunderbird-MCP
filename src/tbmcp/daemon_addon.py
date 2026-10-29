"""Thunderbird add-on WebSocket listener and connection lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from . import ipc
from .addon_session import AddonSession

if TYPE_CHECKING:
    from .daemon import Daemon

log = logging.getLogger("tbmcp.daemon")
CHUNK_LIMIT = 4 * 1024 * 1024
HELLO_TIMEOUT = 10.0


async def serve_addon(daemon: Daemon, ws: ServerConnection) -> None:
    """One add-on connection, from the upgrade to whatever ends it.

    Every exit records its own outcome in `daemon.handshakes`. Splitting what used
    to be one `except` is the point: "the socket closed before the hello" and
    "the hello was not JSON" are different faults with different remedies, and
    collapsing them left the user with nothing but a 4002 close code.
    """
    peer = ws.remote_address[0] if ws.remote_address else "?"
    if peer not in ("127.0.0.1", "::1", "localhost"):
        daemon.handshakes.resolve(ws, "non-loopback", close_code=4003, detail=str(peer))
        await ws.close(code=4003, reason="non-loopback peer")
        return
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=HELLO_TIMEOUT)
    except TimeoutError:
        daemon.handshakes.resolve(
            ws, "no-hello-timeout", close_code=4002, detail=f"{HELLO_TIMEOUT:g}s"
        )
        await ws.close(code=4002, reason="expected a hello frame")
        return
    except ConnectionClosed:
        # Already gone: there is nothing left to close, only to record.
        daemon.handshakes.resolve(ws, "closed-before-hello", close_code=ws.close_code)
        return
    try:
        hello = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        await daemon._reject_hello(ws, detail=str(exc))
        return
    if not isinstance(hello, dict) or hello.get("t") != "hello":
        await daemon._reject_hello(ws, detail=f"first frame was {type(hello).__name__}")
        return
    if hello.get("token") != daemon.addon_token:
        daemon.handshakes.resolve(ws, "bad-token", close_code=4001)
        await ws.close(code=4001, reason="bad token")
        return
    try:
        protocol = int(hello.get("protocol", 0))
    except (TypeError, ValueError):
        # A non-numeric version used to raise straight out of the handler, so
        # websockets logged a traceback and the add-on was told nothing at all.
        protocol = -1
    if protocol != ipc.PROTOCOL_VERSION:
        daemon.handshakes.resolve(
            ws, "protocol-mismatch", close_code=4002, detail=f"protocol {protocol}"
        )
        await ws.close(code=4002, reason="protocol mismatch")
        return

    if daemon.session is not None:
        # A restarted Thunderbird, or a second window; the newest wins.
        daemon.handshakes.resolve(daemon.session.ws, "superseded", close_code=1012)
        with contextlib.suppress(ConnectionClosed):
            await daemon.session.ws.close(code=1012, reason="superseded")

    session = AddonSession(ws, hello)
    daemon.session = session
    daemon._session_ready.set()
    await ws.send(json.dumps({"t": "welcome", "protocol": ipc.PROTOCOL_VERSION, "server": "tbmcp"}))
    daemon.handshakes.resolve(ws, "welcomed", addon_version=session.addon_version)
    log.info(
        "add-on connected: %s %s (experiment=%s)",
        session.app.get("name", "Thunderbird"),
        session.app.get("version", "?"),
        session.has_experiment,
    )
    keepalive = asyncio.create_task(session.keepalive())
    try:
        await session.pump(daemon._record_event)
    finally:
        keepalive.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await keepalive
        if daemon.session is session:
            daemon.session = None
            daemon._session_ready.clear()
        # A no-op for a session we superseded: that outcome is already final.
        daemon.handshakes.resolve(ws, "disconnected", close_code=ws.close_code)


async def reject_hello(daemon: Daemon, ws: ServerConnection, *, detail: str) -> None:
    daemon.handshakes.resolve(ws, "bad-hello", close_code=4002, detail=detail)
    await ws.close(code=4002, reason="expected a hello frame")


def on_upgrade_request(daemon: Daemon, conn: ServerConnection, request: Any) -> None:
    """`process_request`: note who is dialling in, then accept by returning None."""
    # Nothing here may raise: websockets turns an exception from process_request
    # into a 500 and rejects the connection, which would make the telemetry the
    # outage it is meant to explain.
    with contextlib.suppress(Exception):
        daemon.handshakes.note_request(
            conn,
            path=getattr(request, "path", None),
            origin=request.headers.get("Origin"),
            user_agent=request.headers.get("User-Agent"),
        )
    return None


def addon_server(daemon: Daemon, **overrides: Any) -> serve:
    """The add-on listener, factored out so tests exercise the server `run` runs."""
    kwargs: dict[str, Any] = {
        "host": "127.0.0.1",
        "port": 0,
        "max_size": CHUNK_LIMIT * 2,
        "create_connection": daemon._connection_class(),
        "process_request": daemon._on_upgrade_request,
    }
    kwargs.update(overrides)
    return serve(daemon._serve_addon, **kwargs)


def record_event(daemon: Daemon, frame: dict[str, Any]) -> None:
    daemon.event_seq += 1
    daemon.events.append(
        {
            "seq": daemon.event_seq,
            "at": time.time(),
            "name": frame.get("name"),
            "data": frame.get("data"),
        }
    )
