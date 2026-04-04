"""
Main entry point — BrowserAgent high-level API.

Usage:
    from ai_web_auto_backend.automation_controller import BrowserAgent

    agent = BrowserAgent()
    await agent.connect()
    result = await agent.execute_task(
        objective="Find the cheapest flight from NYC to London",
        start_url="https://www.google.com/travel/flights",
    )
    print(result.data)
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from loguru import logger

from .core.server import WebSocketServer
from .core.state import TaskQueue
from .core.models import TaskResult, Response
from .ai.agent import AIAgent, SafetyCheck
from .utils.screenshot import ScreenshotProcessor
from .utils.data_pipeline import DataPipeline


class BrowserAgent:
    def __init__(
        self,
        host: str = "localhost",
        port: int = 8765,
        auth_token: str | None = None,
        model: str = "claude-sonnet-4-20250514",
        api_key: str | None = None,
        safety: SafetyCheck | None = None,
    ):
        self.server = WebSocketServer(host=host, port=port, auth_token=auth_token)
        self.task_queue = TaskQueue()
        self.ai = AIAgent(server=self.server, model=model, api_key=api_key, safety=safety)
        self.screenshots = ScreenshotProcessor()
        self.data = DataPipeline()

    @property
    def auth_token(self) -> str:
        return self.server.auth_token

    @property
    def connected(self) -> bool:
        return self.server.session.connected

    async def start(self):
        """Start the WebSocket server (non-blocking)."""
        await self.server.start()
        logger.info("Waiting for Chrome extension to connect...")

    async def connect(self, timeout: float = 120.0):
        """Start server and wait for extension connection."""
        await self.start()
        await self.server.wait_for_connection(timeout=timeout)

    async def stop(self):
        await self.server.stop()

    # ── High-level task API ──────────────────────────────────────────────

    async def execute_task(
        self,
        objective: str,
        start_url: str = "",
        max_steps: int = 50,
    ) -> TaskResult:
        """Execute an AI-driven automation task."""
        task = await self.task_queue.add(objective=objective, start_url=start_url, max_steps=max_steps)
        task = await self.task_queue.get_next()
        try:
            result = await self.ai.execute_task(task)
            await self.task_queue.complete(task.id, result_data=result.data)
            return result
        except Exception as e:
            await self.task_queue.complete(task.id, error=str(e))
            return TaskResult(task_id=task.id, objective=objective, success=False, error=str(e))

    # ── Direct browser commands ──────────────────────────────────────────

    async def navigate(self, url: str) -> Response:
        return await self.server.navigate(url)

    async def click(self, selector: str, method: str = "css") -> Response:
        return await self.server.click(selector, method)

    async def type_text(self, selector: str, text: str, clear_first: bool = True) -> Response:
        return await self.server.type_text(selector, text, clear_first)

    async def screenshot(self, full_page: bool = False) -> Response:
        return await self.server.screenshot(full_page)

    async def scroll(self, direction: str = "down", amount: int = 500) -> Response:
        return await self.server.scroll(direction, amount)

    async def extract(self, extract_type: str = "text") -> Response:
        return await self.server.extract(extract_type)

    async def execute_script(self, script: str) -> Response:
        return await self.server.execute_script(script)

    async def wait_for(self, condition: str, value: str = "", timeout_ms: int = 10000) -> Response:
        return await self.server.wait_for(condition, value, timeout_ms)

    # ── DOM Observation ──────────────────────────────────────────────────

    async def observe_start(self, selector: str = "body") -> Response:
        """Start DOM observation — takes initial snapshot of target element."""
        return await self.server.observe_start(selector)

    async def observe_diff(self) -> Response:
        """Get structured DOM diff since last snapshot. Returns changes, stats, and AI summary."""
        return await self.server.observe_diff()

    async def observe_stop(self) -> Response:
        """Stop DOM observation."""
        return await self.server.observe_stop()

    # ── Confirmation callback ────────────────────────────────────────────

    def set_confirmation_callback(self, callback: Callable):
        self.ai.set_confirmation_callback(callback)

    # ── Event subscription ───────────────────────────────────────────────

    def on_event(self, event_type: str, handler: Callable):
        self.server.on_event(event_type, handler)


async def main():
    """Start server and handle commands via stdin."""
    agent = BrowserAgent()
    await agent.connect()

    print("\n=== AI Web-Auto Ready ===")
    print(f"Auth token: {agent.auth_token}")
    print("\nCommands:")
    print("  navigate <url>       — Open a URL in the browser")
    print("  screenshot           — Take a screenshot")
    print("  discover             — Discover all interactive elements")
    print("  type <text>          — Type into focused input (CDP)")
    print("  click <selector>     — Click element by CSS/XPath")
    print("  extract              — Extract page text")
    print("  observe [selector]   — Start DOM observation (default: body)")
    print("  diff                 — Show DOM changes since last snapshot")
    print("  unobserve            — Stop DOM observation")
    print("  task <objective>     — Run AI-driven automation task")
    print("  quit                 — Exit\n")

    loop = asyncio.get_event_loop()
    while True:
        raw = await loop.run_in_executor(None, input, "> ")
        cmd = raw.strip()
        if not cmd:
            continue
        if cmd.lower() in ("quit", "exit", "q"):
            break

        # ── Direct commands ──────────────────────────────────────────────
        if cmd.lower().startswith("navigate "):
            url = cmd[9:].strip()
            r = await agent.navigate(url)
            print(f"  Navigate: {r.status}")
            continue

        if cmd.lower() == "screenshot":
            import base64
            r = await agent.screenshot()
            if r.data.screenshot:
                path = "learning-process/output/screenshot.png"
                import os; os.makedirs("learning-process/output", exist_ok=True)
                with open(path, "wb") as f:
                    f.write(base64.b64decode(r.data.screenshot))
                print(f"  Saved: {path}")
            continue

        if cmd.lower() == "discover":
            r = await agent.execute_script("""
                var results = [];
                document.querySelectorAll('a, button, input, select, textarea, [role="button"], [role="tab"], [role="menuitem"], [role="option"], [onclick], [tabindex], [contenteditable="true"], [aria-haspopup]').forEach(function(el) {
                    var r = el.getBoundingClientRect();
                    if (r.width < 5 || r.height < 5) return;
                    var tag = el.tagName.toLowerCase();
                    var role = 'clickable';
                    if (tag === 'input' || tag === 'textarea' || tag === 'select' || el.contentEditable === 'true') role = 'input';
                    else if (tag === 'button' || el.getAttribute('role') === 'button') role = 'button';
                    else if (tag === 'a') role = 'link';
                    else if (tag === 'img') role = 'image';
                    results.push({
                        tag: tag,
                        text: (el.textContent || el.value || el.placeholder || '').trim().slice(0, 40),
                        id: el.id || '',
                        ariaRole: el.getAttribute('role') || '',
                        role: role,
                        rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }
                    });
                });
                return JSON.stringify(results);
            """)
            import json as _json
            raw = r.data.extracted_data.get("result", "[]")
            elements = _json.loads(raw) if isinstance(raw, str) else (raw if isinstance(raw, list) else [])
            print(f"  Found {len(elements)} interactive elements:")
            for el in elements[:30]:
                print(f"    [{el['tag']}] text=\"{el['text']}\" id={el['id']} role={el['role']}")

            # Inject border highlights on the page
            highlight_js = """
            (function(elements) {
                document.querySelectorAll('.discover-highlight').forEach(e => e.remove());
                var colors = { input: '#00ff00', button: '#ff6600', link: '#6699ff', image: '#ff00ff', clickable: '#ffff00' };
                elements.forEach(function(el, i) {
                    var div = document.createElement('div');
                    div.className = 'discover-highlight';
                    var c = colors[el.role] || '#ffffff';
                    Object.assign(div.style, {
                        position: 'fixed', zIndex: '999999',
                        left: el.rect.x + 'px', top: el.rect.y + 'px',
                        width: el.rect.w + 'px', height: el.rect.h + 'px',
                        border: '2px solid ' + c,
                        borderRadius: '3px',
                        pointerEvents: 'none',
                    });
                    var label = document.createElement('span');
                    Object.assign(label.style, {
                        position: 'absolute', top: '-16px', left: '0',
                        fontSize: '10px', color: c, fontWeight: 'bold',
                        backgroundColor: 'rgba(0,0,0,0.8)', padding: '1px 4px',
                        borderRadius: '2px', whiteSpace: 'nowrap',
                    });
                    label.textContent = i + ':' + el.role + (el.text ? '=' + el.text.slice(0,20) : '');
                    div.appendChild(label);
                    document.body.appendChild(div);
                });
                return 'highlighted ' + elements.length;
            })(""" + _json.dumps(elements) + """);
            """
            await agent.execute_script(highlight_js)
            print(f"  Highlighted {len(elements)} elements on page (borders visible)")
            continue

        if cmd.lower().startswith("type "):
            text = cmd[5:].strip()
            r = await agent.server.type_cdp(text)
            print(f"  Type: {r.status}")
            continue

        if cmd.lower().startswith("click "):
            selector = cmd[6:].strip()
            r = await agent.click(selector)
            print(f"  Click: {r.status}")
            continue

        if cmd.lower() == "extract":
            r = await agent.extract("text")
            text = r.data.extracted_data.get("text", "")
            print(f"  Page text ({len(text)} chars): {text[:200]}")
            continue

        if cmd.lower().startswith("observe"):
            selector = cmd[7:].strip() or "body"
            r = await agent.observe_start(selector)
            print(f"  Observing: {selector} [{r.status}]")
            continue

        if cmd.lower() == "diff":
            r = await agent.observe_diff()
            if r.status == "success":
                data = r.data.extracted_data
                print(f"\n{data.get('summary', 'No changes')}\n")
                stats = data.get("stats", {})
                if stats.get("total", 0) > 0:
                    changes = data.get("changes", [])
                    for c in changes[:20]:
                        if c["type"] == "add":
                            print(f"    + <{c['tag']}> {c['selector']} -- {c['reason']}")
                        elif c["type"] == "rem":
                            print(f"    - <{c['tag']}> {c['selector']} -- {c['reason']}")
                        elif c["type"] == "attr":
                            print(f"    ~ [{c['attribute']}] {c['selector']} -- {c['reason']}")
                        elif c["type"] == "text":
                            print(f"    ~ text {c['selector']}: \"{c.get('oldValue','')}\" -> \"{c.get('newValue','')}\"")
                    if len(changes) > 20:
                        print(f"    ... and {len(changes) - 20} more")
            else:
                print(f"  Diff failed: {r.error}")
            continue

        if cmd.lower() == "unobserve":
            r = await agent.observe_stop()
            print(f"  Stopped observation [{r.status}]")
            continue

        # ── AI task ──────────────────────────────────────────────────────
        if cmd.lower().startswith("task "):
            objective = cmd[5:].strip()
            start_url = await loop.run_in_executor(None, input, "Start URL (blank to skip): ")
            result = await agent.execute_task(objective=objective, start_url=start_url.strip())
            print(f"\n{'OK' if result.success else 'FAIL'} {result.objective}")
            if result.data:
                print(f"  Data: {result.data}")
        if result.error:
            print(f"  Error: {result.error}")
        print(f"  Steps: {result.steps_taken} | Duration: {result.duration_ms}ms\n")

    await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
