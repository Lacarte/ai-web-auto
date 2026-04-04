"""WebSocket server for communicating with the Chrome extension."""

from __future__ import annotations

import asyncio
import json
import secrets
from typing import Any, Callable, Optional

import websockets
from websockets.server import WebSocketServerProtocol
from loguru import logger

from .models import Command, Response, ResponseType, ResponseStatus
from .state import SessionState


DEFAULT_TOKEN = "local-dev-token"


class WebSocketServer:
    def __init__(self, host: str = "localhost", port: int = 8765, auth_token: str | None = None):
        self.host = host
        self.port = port
        self.auth_token = auth_token or DEFAULT_TOKEN
        self.session = SessionState()
        self.session.auth_token = self.auth_token
        self._ws: Optional[WebSocketServerProtocol] = None
        self._server = None
        self._pending: dict[str, asyncio.Future] = {}
        self._event_handlers: dict[str, list[Callable]] = {}
        self._running = False

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def start(self):
        self._running = True
        self._server = await websockets.serve(
            self._handle_connection,
            self.host,
            self.port,
            ping_interval=30,
            ping_timeout=10,
            max_size=50 * 1024 * 1024,  # 50MB for screenshots
        )
        logger.info(f"WebSocket server started on ws://{self.host}:{self.port}")
        logger.info(f"Auth token: {self.auth_token}")

    async def stop(self):
        self._running = False
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        logger.info("WebSocket server stopped")

    async def wait_for_connection(self, timeout: float = 60.0):
        start = asyncio.get_event_loop().time()
        while not self.session.connected:
            if asyncio.get_event_loop().time() - start > timeout:
                raise TimeoutError("No extension connected within timeout")
            await asyncio.sleep(0.1)
        logger.info("Extension connected")

    # ── Connection handler ───────────────────────────────────────────────

    async def _handle_connection(self, ws: WebSocketServerProtocol):
        # Auth handshake
        try:
            auth_msg = await asyncio.wait_for(ws.recv(), timeout=10)
            auth = json.loads(auth_msg)
            logger.debug(f"Auth attempt — received token: '{auth.get('token')}', expected: '{self.auth_token}'")
            if auth.get("type") != "auth" or auth.get("token") != self.auth_token:
                logger.warning(f"Auth denied — token mismatch")
                await ws.send(json.dumps({"type": "auth_response", "status": "denied"}))
                await ws.close()
                return
            await ws.send(json.dumps({"type": "auth_response", "status": "accepted"}))
        except Exception as e:
            logger.error(f"Auth failed: {e}")
            await ws.close()
            return

        self._ws = ws
        self.session.set_connected(True)
        logger.info(f"Extension authenticated from {ws.remote_address}")

        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                    await self._route_message(msg)
                except json.JSONDecodeError:
                    logger.warning("Received non-JSON message")
        except websockets.ConnectionClosed:
            logger.warning("Extension disconnected")
        finally:
            self.session.set_connected(False)
            self._ws = None
            # Fail all pending commands
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("Extension disconnected"))
            self._pending.clear()

    async def _route_message(self, msg: dict[str, Any]):
        msg_type = msg.get("type", "")

        if msg_type == "response":
            cid = msg.get("correlation_id", "")
            fut = self._pending.pop(cid, None)
            if fut and not fut.done():
                resp = Response(**msg)
                await self.session.update_from_response(msg.get("data", {}))
                fut.set_result(resp)
            else:
                logger.warning(f"No pending command for correlation_id={cid}")

        elif msg_type == "event":
            event = msg.get("event", "")
            await self.session.update_from_event(event, msg.get("data", {}))
            for handler in self._event_handlers.get(event, []):
                try:
                    await handler(msg)
                except Exception as e:
                    logger.error(f"Event handler error: {e}")

        elif msg_type == "error":
            cid = msg.get("correlation_id", "")
            fut = self._pending.pop(cid, None)
            if fut and not fut.done():
                fut.set_result(Response(**msg))
            logger.error(f"Extension error: {msg.get('error')}")

    # ── Send commands ────────────────────────────────────────────────────

    async def send_command(self, command: Command, timeout: float | None = None) -> Response:
        if not self._ws or not self.session.connected:
            raise ConnectionError("Extension not connected")

        timeout = timeout or (command.timeout / 1000)
        fut: asyncio.Future[Response] = asyncio.get_event_loop().create_future()
        self._pending[command.id] = fut

        await self._ws.send(json.dumps(command.to_message()))
        logger.debug(f"Sent command: {command.action} id={command.id}")

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(command.id, None)
            return Response(
                correlation_id=command.id,
                type=ResponseType.ERROR,
                status=ResponseStatus.FAILURE,
                error=f"Command timed out after {timeout}s",
            )

    # ── Convenience methods ──────────────────────────────────────────────

    async def navigate(self, url: str) -> Response:
        return await self.send_command(Command(action="navigate", params={"url": url}))

    async def click(self, selector: str, method: str = "css") -> Response:
        return await self.send_command(Command(
            action="click",
            target={"method": method, "value": selector},
        ))

    async def type_text(self, selector: str, text: str, clear_first: bool = True) -> Response:
        return await self.send_command(Command(
            action="type",
            target={"method": "css", "value": selector},
            params={"text": text, "clear_first": clear_first},
        ))

    async def screenshot(self, full_page: bool = False, selector: str | None = None) -> Response:
        return await self.send_command(Command(
            action="screenshot",
            params={"full_page": full_page, "selector": selector},
        ))

    async def scroll(self, direction: str = "down", amount: int = 500) -> Response:
        return await self.send_command(Command(
            action="scroll",
            params={"direction": direction, "amount": amount},
        ))

    async def extract(self, extract_type: str = "text", selector: str | None = None) -> Response:
        return await self.send_command(Command(
            action="extract",
            params={"type": extract_type, "selector": selector},
        ))

    async def execute_script(self, script: str) -> Response:
        return await self.send_command(Command(
            action="execute_script",
            params={"script": script},
        ))

    async def type_cdp(self, text: str, xpath: str = "", clear: bool = True) -> Response:
        return await self.send_command(Command(
            action="type_cdp",
            params={"text": text, "xpath": xpath, "clear": clear},
            timeout=60000,
        ))

    async def click_cdp(self, x: int, y: int) -> Response:
        return await self.send_command(Command(
            action="click_cdp",
            params={"x": x, "y": y},
        ))

    async def wait_for(self, condition: str = "element_visible", value: str = "", timeout_ms: int = 10000) -> Response:
        return await self.send_command(Command(
            action="wait",
            params={"condition": condition, "value": value},
            timeout=timeout_ms,
        ))

    async def hover(self, selector: str, method: str = "css") -> Response:
        return await self.send_command(Command(
            action="hover",
            target={"method": method, "value": selector},
        ))

    # ── Event subscription ───────────────────────────────────────────────

    def on_event(self, event_type: str, handler: Callable):
        self._event_handlers.setdefault(event_type, []).append(handler)
