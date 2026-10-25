"""Typed client for the extension's executor verbs.

The engine only talks to the browser through this class, so the unit tests can
swap in a fake. Results come back in the core Response's `extracted_data`.

Failure semantics the engine relies on:
- BrowserError(code=...): the command ran and reported a definite failure.
- outcome_unknown=True: the command may or may not have taken effect
  (timeout, or the extension disconnected mid-command). The engine must
  verify the page before deciding whether anything needs repeating.
"""

from __future__ import annotations

from typing import Any, Optional

from .server import AgentServer


class BrowserError(Exception):
    def __init__(self, code: str, message: str, details: Optional[dict] = None, outcome_unknown: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.outcome_unknown = outcome_unknown


class Disconnected(BrowserError):
    def __init__(self, message: str = "extension disconnected"):
        super().__init__("disconnected", message, outcome_unknown=True)


# Per-call ceilings (seconds) on top of the in-page wait the call itself performs.
_SLACK_S = 8.0


class ExtensionBrowser:
    def __init__(self, server: AgentServer, command_timeout_s: float = 20.0):
        self.server = server
        self.command_timeout_s = command_timeout_s

    @property
    def connected(self) -> bool:
        return self.server.session.connected

    async def call(self, action: str, params: dict[str, Any] | None = None, timeout_s: float | None = None) -> dict:
        timeout = timeout_s or self.command_timeout_s
        try:
            resp = await self.server.command(action, params or {}, timeout_s=timeout)
        except ConnectionError as e:
            raise Disconnected(str(e)) from e
        data = resp.data.extracted_data or {}
        if resp.status == "failure":
            err = resp.error or "command failed"
            if err.startswith("Command timed out"):
                raise BrowserError("timeout", f"{action}: {err}", outcome_unknown=True)
            raise BrowserError(str(data.get("code") or "command_failed"), err, data)
        if resp.data.screenshot and "screenshot" not in data:
            data["screenshot"] = resp.data.screenshot
        return data

    # ── Tab binding ──────────────────────────────────────────────────────

    async def bind_tab(self, tab_id: int, allowed_origins: list[str], run_id: str) -> dict:
        return await self.call("bind_tab", {"tab_id": tab_id, "allowed_origins": allowed_origins, "run_id": run_id})

    async def release_tab(self, outcome: str, message: str = "") -> dict:
        return await self.call("release_tab", {"outcome": outcome, "message": message}, timeout_s=10)

    async def navigate(self, url: str, timeout_ms: int = 30000) -> dict:
        return await self.call("navigate", {"url": url, "bound": True, "timeout_ms": timeout_ms},
                               timeout_s=timeout_ms / 1000 + _SLACK_S)

    # ── Observe / locate / act / verify ──────────────────────────────────

    async def observe(self, scope: Any = None, max_elements: int = 120, text_chars: int = 3000) -> dict:
        return await self.call("observe", {"scope": scope, "max_elements": max_elements, "text_chars": text_chars})

    async def locate(self, locator: dict, purpose: str, label: str, timeout_ms: int) -> dict:
        return await self.call("locate", {"locator": locator, "purpose": purpose, "label": label,
                                          "timeout_ms": timeout_ms}, timeout_s=timeout_ms / 1000 + _SLACK_S)

    async def scroll_search(self, locator: dict, label: str, max_scrolls: int = 15) -> dict:
        return await self.call("scroll_search", {"locator": locator, "label": label, "max_scrolls": max_scrolls},
                               timeout_s=max_scrolls * 1.5 + _SLACK_S)

    async def act(self, action: str, handle: str, locator: dict, label: str, **params: Any) -> dict:
        return await self.call("act", {"action": action, "handle": handle, "locator": locator,
                                       "label": label, **params})

    async def wait_for(self, expect: list[dict], timeout_ms: int, label: str) -> dict:
        return await self.call("wait_for", {"expect": expect, "timeout_ms": timeout_ms, "label": label},
                               timeout_s=timeout_ms / 1000 + _SLACK_S)

    async def check(self, expect: list[dict]) -> dict:
        return await self.call("check", {"expect": expect})

    async def settle(self, timeout_ms: int, label: str = "") -> dict:
        return await self.call("settle", {"timeout_ms": timeout_ms, "label": label},
                               timeout_s=timeout_ms / 1000 + _SLACK_S)

    async def extract_fields(self, fields: dict[str, dict]) -> dict:
        return await self.call("extract_fields", {"fields": fields})

    async def screenshot(self) -> str:
        data = await self.call("screenshot", {"bound": True}, timeout_s=15)
        return data.get("screenshot", "")

    async def overlay(self, op: str, **data: Any) -> None:
        try:
            await self.call("overlay", {"op": op, **data}, timeout_s=5)
        except BrowserError:
            pass  # feedback is best-effort; it must never fail a run
