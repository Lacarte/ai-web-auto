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
        self._connections: list[WebSocketServerProtocol] = []  # live extensions, newest last; _ws is the newest
        self._server = None
        self._pending: dict[str, asyncio.Future] = {}
        self._event_handlers: dict[str, list[Callable]] = {}
        self._running = False
        # Which browser commands reach: connection_id changes whenever _ws changes (a second browser with
        # the extension connecting, or the current one going away). bind() pins an order to one of them.
        self.connection_id = 0
        self.capabilities: set[str] = set()
        self.bound_tab_id: int | None = None
        self.bound_connection_id: int | None = None
        self._capabilities: dict[int, set[str]] = {}

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

        if self._connections:
            logger.warning(f"Another AI Web-Auto extension connected from {ws.remote_address}; commands now go to it"
                           + (" - the bound order will stop" if self.bound_connection_id is not None else ""))
        self._capabilities[id(ws)] = set(auth.get("capabilities") or [])
        self._connections.append(ws)
        self._use_newest_connection()
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
            was_current = self._ws is ws
            self._connections.remove(ws)
            self._capabilities.pop(id(ws), None)
            # A replaced connection closing late must not drop the live one.
            if was_current:
                self._use_newest_connection()
                if not self._connections:
                    # Fail all pending commands
                    for fut in self._pending.values():
                        if not fut.done():
                            fut.set_exception(ConnectionError("Extension disconnected"))
                    self._pending.clear()

    def _use_newest_connection(self):
        self._ws = self._connections[-1] if self._connections else None
        self.connection_id += 1
        self.capabilities = self._capabilities.get(id(self._ws), set()) if self._ws else set()
        self.session.set_connected(self._ws is not None)

    def bind(self, tab_id: int | None) -> None:
        """Pin every following command to this tab, on the browser connected now.

        If another browser's extension connects (or this one drops) meanwhile, commands are refused
        instead of reaching a different browser; the extension refuses them if the tab was closed.
        """
        self.bound_tab_id = tab_id
        self.bound_connection_id = self.connection_id

    def unbind(self) -> None:
        self.bound_tab_id = self.bound_connection_id = None

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
        if self.bound_connection_id is not None and self.bound_connection_id != self.connection_id:
            return Response(
                correlation_id=command.id,
                type=ResponseType.ERROR,
                status=ResponseStatus.FAILURE,
                error="The browser connection changed during this order (another AI Web-Auto extension connected "
                      "or this one reconnected); not sending - run the order again",
            )
        if self.bound_tab_id is not None:
            command.params.setdefault("tab_id", self.bound_tab_id)

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

    async def select_option(self, selector: str, value: str, by_text: bool = False, method: str = "css") -> Response:
        """Select an option in a <select> dropdown, or set checkbox/radio."""
        return await self.send_command(Command(
            action="select",
            target={"method": method, "value": selector},
            params={"selector": selector, "value": value, "by_text": by_text},
        ))

    async def upload_file(self, selector: str, file_path_or_paths: str | list[str], method: str = "css") -> Response:
        """Upload file(s) directly to <input type="file"> via CDP."""
        files = [file_path_or_paths] if isinstance(file_path_or_paths, str) else file_path_or_paths
        return await self.send_command(Command(
            action="upload",
            target={"method": method, "value": selector},
            params={"selector": selector, "files": files},
        ))

    async def send_key(self, key: str, ctrl: bool = False, alt: bool = False, shift: bool = False, meta: bool = False) -> Response:
        """Send a single key or key combination via CDP."""
        return await self.send_command(Command(
            action="key",
            params={"key": key, "ctrl": ctrl, "alt": alt, "shift": shift, "meta": meta},
        ))

    # ── DOM Observation ───────────────────────────────────────────────────

    async def observe_start(self, selector: str = "body", interval: int = 1000) -> Response:
        """Start DOM observation on a selector. Takes initial snapshot."""
        return await self.send_command(Command(
            action="observe_start",
            params={"selector": selector, "interval": interval},
        ))

    async def observe_diff(self) -> Response:
        """Diff current DOM against last snapshot. Returns structured changes."""
        return await self.send_command(Command(action="observe_diff"))

    async def observe_stop(self) -> Response:
        """Stop DOM observation and clean up."""
        return await self.send_command(Command(action="observe_stop"))

    # ── Event subscription ───────────────────────────────────────────────

    def on_event(self, event_type: str, handler: Callable):
        self._event_handlers.setdefault(event_type, []).append(handler)
