"""AgentServer — the ai-web-auto WebSocketServer, hardened for an auto-started backend.

Reused from the core unchanged: command/response correlation with futures and
timeouts (`send_command`), event routing to `on_event` handlers, SessionState
tracking, and the convenience commands (`navigate`, `screenshot`,
`execute_script`, ...), which the extension implements so existing ai-web-auto
scripts can drive it too.

Overridden:
- `start()`: binds 127.0.0.1 only, pins the WebSocket Origin to the extension
  (any web page can open ws://localhost, so this matters), serves GET /health
  and GET /shutdown on the same port, and never logs the token.
- `_handle_connection()`: same auth handshake, plus protocol-version
  negotiation; newest connection wins; a stale socket closing no longer
  clobbers the live one (the core clears `_ws` unconditionally); per-message
  errors no longer kill the connection; app-level ping -> pong.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any, Awaitable, Callable, Optional

import websockets
from loguru import logger

from ai_web_auto_backend.core.models import Command
from ai_web_auto_backend.core.server import WebSocketServer

from . import __version__
from .protocol import APP_NAME, PROTOCOL_VERSION, UiRequest, is_compatible


class AgentCommand(Command):
    """Core Command with `action` widened from the ActionType enum to str.

    The goal agent adds verbs the platform enum doesn't list (bind_tab, locate,
    act, wait_for, settle, check, observe, overlay, ...). Everything else about
    the message shape is the core's.
    """

    action: str


UiHandler = Callable[[UiRequest], Awaitable[Any]]


class AgentServer(WebSocketServer):
    def __init__(
        self,
        host: str,
        port: int,
        auth_token: str,
        allowed_origins: list[str],
        started_by: str = "manual",
        capabilities: Callable[[], dict[str, Any]] | None = None,
    ):
        super().__init__(host=host, port=port, auth_token=auth_token)
        self.allowed_origins = allowed_origins
        self.started_by = started_by
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._capabilities = capabilities or (lambda: {})
        self._ui_handler: Optional[UiHandler] = None
        self._client_listeners: list[Callable[[bool], None]] = []
        self._shutdown = asyncio.Event()
        self.shutdown_reason = ""
        self.last_client_change = time.monotonic()
        self.client_info: dict[str, Any] = {}

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def start(self):
        self._running = True
        self._server = await websockets.serve(
            self._handle_connection,
            self.host,
            self.port,
            # None admits clients that send no Origin (Python scripts, the
            # native host); browsers always send one, so web pages are refused.
            origins=[*self.allowed_origins, None],
            process_request=self._process_request,
            ping_interval=20,
            ping_timeout=20,
            max_size=50 * 1024 * 1024,
        )
        logger.info(f"{APP_NAME} backend v{__version__} listening on ws://{self.host}:{self.port} "
                    f"(started_by={self.started_by}, pid={os.getpid()})")

    def request_shutdown(self, reason: str):
        if not self._shutdown.is_set():
            self.shutdown_reason = reason
            self._shutdown.set()

    async def wait_for_shutdown(self):
        await self._shutdown.wait()

    def health(self) -> dict[str, Any]:
        return {
            "app": APP_NAME,
            "version": __version__,
            "protocol": PROTOCOL_VERSION,
            "pid": os.getpid(),
            "port": self.port,
            "started_by": self.started_by,
            "started_at": self.started_at,
            "client_connected": self.session.connected,
        }

    def _process_request(self, connection, request):
        path = request.path.split("?", 1)[0]
        if path == "/health":
            return self._json_response(connection, HTTPStatus.OK, self.health())
        if path == "/shutdown":
            supplied = request.headers.get("X-WGA-Token", "")
            if not secrets.compare_digest(supplied, self.auth_token):
                return self._json_response(connection, HTTPStatus.FORBIDDEN, {"ok": False, "error": "bad token"})
            asyncio.get_running_loop().call_soon(self.request_shutdown, "requested")
            return self._json_response(connection, HTTPStatus.OK, {"ok": True, "pid": os.getpid()})
        return None  # continue with the WebSocket handshake

    @staticmethod
    def _json_response(connection, status: HTTPStatus, body: dict):
        response = connection.respond(status, json.dumps(body))
        del response.headers["Content-Type"]
        response.headers["Content-Type"] = "application/json"
        response.headers["Cache-Control"] = "no-store"
        return response

    # ── Connection handler ───────────────────────────────────────────────

    async def _handle_connection(self, ws):
        try:
            auth = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        except Exception as e:
            logger.warning(f"Auth handshake failed: {e}")
            await ws.close()
            return

        reason = ""
        if auth.get("type") != "auth":
            reason = "expected_auth"
        elif not secrets.compare_digest(str(auth.get("token", "")), self.auth_token):
            reason = "bad_token"
        elif not is_compatible(auth.get("protocol")):
            reason = "protocol_mismatch"
        if reason:
            logger.warning(f"Auth denied ({reason}) from {ws.remote_address}")
            await ws.send(json.dumps({
                "type": "auth_response", "status": "denied", "reason": reason,
                "protocol": PROTOCOL_VERSION, "backend_version": __version__,
            }))
            await ws.close()
            return

        previous = self._ws
        self._ws = ws
        self.client_info = {"client": str(auth.get("client", ""))[:64], "version": str(auth.get("version", ""))[:32]}
        self.session.set_connected(True)
        self.last_client_change = time.monotonic()
        if previous is not None:
            logger.info("Newer client connected; closing the previous socket")
            asyncio.create_task(previous.close(code=4001, reason="replaced by a newer connection"))

        await ws.send(json.dumps({
            "type": "auth_response", "status": "accepted",
            "protocol": PROTOCOL_VERSION, "backend_version": __version__,
            "session_id": str(uuid.uuid4()), "capabilities": self._capabilities(),
            "started_by": self.started_by, "pid": os.getpid(),
        }))
        logger.info(f"Extension authenticated: {self.client_info}")
        self._notify_client(True)

        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("Ignoring non-JSON message")
                    continue
                try:
                    await self._dispatch(ws, msg)
                except Exception as e:  # one bad message must not drop the link
                    logger.exception(f"Error handling message type={msg.get('type')}: {e}")
        except websockets.ConnectionClosed:
            pass
        finally:
            if self._ws is ws:
                self._ws = None
                self.session.set_connected(False)
                self.last_client_change = time.monotonic()
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(ConnectionError("Extension disconnected"))
                self._pending.clear()
                logger.warning("Extension disconnected")
                self._notify_client(False)

    async def _dispatch(self, ws, msg: dict[str, Any]):
        msg_type = msg.get("type")
        if msg_type == "ping":
            await ws.send(json.dumps({"type": "pong", "ts": msg.get("ts")}))
            return
        if msg_type == "event" and msg.get("event") == "ui_request":
            # Handlers may run for a while (start_run awaits validation); never
            # block the receive loop, or command responses would stall behind it.
            asyncio.create_task(self._handle_ui_request(msg.get("data") or {}))
            return
        await self._route_message(msg)

    async def _handle_ui_request(self, data: dict[str, Any]):
        request_id = str(data.get("request_id", ""))[:64]
        try:
            req = UiRequest.model_validate(data)
            if not self._ui_handler:
                raise RuntimeError("backend not ready")
            result = await self._ui_handler(req)
            await self.push("ui_reply", {"request_id": request_id, "ok": True, "result": result})
        except Exception as e:
            logger.warning(f"ui_request {data.get('op')!r} failed: {e}")
            await self.push("ui_reply", {"request_id": request_id, "ok": False, "error": _short_error(e)})

    # ── Push / hooks ─────────────────────────────────────────────────────

    async def push(self, event: str, data: dict[str, Any]) -> bool:
        """Send an event to the extension. Returns False if no client is connected."""
        ws = self._ws
        if ws is None:
            return False
        try:
            await ws.send(json.dumps({
                "id": str(uuid.uuid4()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": "event",
                "event": event,
                "data": data,
            }, default=str))
            return True
        except websockets.ConnectionClosed:
            return False

    def set_ui_handler(self, handler: UiHandler):
        self._ui_handler = handler

    def on_client_change(self, listener: Callable[[bool], None]):
        self._client_listeners.append(listener)

    def _notify_client(self, connected: bool):
        for listener in self._client_listeners:
            try:
                listener(connected)
            except Exception as e:
                logger.error(f"client listener failed: {e}")

    async def command(self, action: str, params: dict[str, Any] | None = None, timeout_s: float = 20.0):
        """Send a goal-agent verb and return the core Response."""
        return await self.send_command(
            AgentCommand(action=action, params=params or {}, timeout=int(timeout_s * 1000)),
            timeout=timeout_s,
        )


def _short_error(e: Exception) -> str:
    text = str(e) or e.__class__.__name__
    return text if len(text) <= 500 else text[:500] + "..."
