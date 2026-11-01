"""
Self-correcting execution runner for browser automation.

Reads execution-list.txt, runs each step, takes screenshots to verify,
and rewrites the execution list with corrected selectors when steps fail.

Usage:
    python runner-execution.py
    python runner-execution.py --list my-steps.txt
    python runner-execution.py --prompt "A dragon reading a book"
"""

from __future__ import annotations

import asyncio
import argparse
import base64
import json
import os
import re
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_web_auto_backend.core.server import WebSocketServer
from ai_web_auto_backend.core.models import Command, ResponseStatus
from loguru import logger

# ── Config ────────────────────────────────────────────────────────────────────

OUTPUT = Path("output/execution")
LEARNED_FILE = Path("output/execution/learned_selectors.json")
EXECUTION_LIST = Path("execution-list.txt")
MAX_RETRIES = 3


# ── Learned knowledge store ──────────────────────────────────────────────────

class Knowledge:
    """Persistent store of learned selectors, page patterns, and flow steps."""

    def __init__(self):
        self.selectors: dict[str, str] = {}       # role -> working CSS selector
        self.page_patterns: dict[str, Any] = {}    # url pattern -> element info
        self.flow_steps: list[dict] = []           # successful step history
        self._load()

    def _load(self):
        if LEARNED_FILE.exists():
            data = json.loads(LEARNED_FILE.read_text())
            self.selectors = data.get("selectors", {})
            self.page_patterns = data.get("page_patterns", {})
            self.flow_steps = data.get("flow_steps", [])
            logger.info(f"Loaded knowledge: {len(self.selectors)} selectors, {len(self.flow_steps)} steps")

    def save(self):
        LEARNED_FILE.parent.mkdir(parents=True, exist_ok=True)
        LEARNED_FILE.write_text(json.dumps({
            "selectors": self.selectors,
            "page_patterns": self.page_patterns,
            "flow_steps": self.flow_steps,
            "updated": datetime.now().isoformat(),
        }, indent=2))

    def learn_selector(self, role: str, selector: str):
        self.selectors[role] = selector
        logger.info(f"Learned selector: {role} = {selector}")
        self.save()

    def learn_step(self, step: dict):
        self.flow_steps.append({**step, "timestamp": datetime.now().isoformat()})
        self.save()


# ── Step executor ─────────────────────────────────────────────────────────────

class ExecutionRunner:
    def __init__(self, server: WebSocketServer, knowledge: Knowledge):
        self.server = server
        self.kb = knowledge
        self.step_num = 0
        self.screenshots: list[str] = []
        self.last_elements: list[dict] = []
        self.last_page_text = ""
        self.corrections: list[dict] = []

    async def run_list(self, steps: list[str]):
        """Execute all steps from the list, self-correcting on failure."""
        total = len(steps)
        for i, raw_step in enumerate(steps):
            self.step_num = i + 1
            logger.info(f"\n{'='*60}")
            logger.info(f"STEP {self.step_num}/{total}: {raw_step}")
            logger.info(f"{'='*60}")

            parts = [p.strip() for p in raw_step.split("|")]
            cmd = parts[0]
            args = parts[1:] if len(parts) > 1 else []

            # Start DOM observation before step for verification
            try:
                await self.server.send_command(Command(action="observe_start", params={"selector": "body"}))
            except Exception:
                pass

            success = False
            for attempt in range(MAX_RETRIES):
                try:
                    result = await self.execute_step(cmd, args)

                    # Get DOM diff to enrich step verification
                    try:
                        diff_resp = await self.server.send_command(Command(action="observe_diff"))
                        if diff_resp.status == ResponseStatus.SUCCESS:
                            diff_data = diff_resp.data.extracted_data
                            dom_summary = diff_data.get("summary", "")
                            dom_stats = diff_data.get("stats", {})
                            if dom_stats.get("total", 0) > 0:
                                logger.info(f"  DOM changes: {dom_summary.split(chr(10))[0]}")
                                result["dom_changes"] = diff_data
                    except Exception:
                        pass

                    if result.get("success", True):
                        success = True
                        self.kb.learn_step({"step": self.step_num, "cmd": cmd, "args": args, "status": "success"})
                        break
                    else:
                        logger.warning(f"Step failed (attempt {attempt+1}): {result.get('error', 'unknown')}")
                        if attempt < MAX_RETRIES - 1:
                            corrected = await self.self_correct(cmd, args, result)
                            if corrected:
                                args = corrected
                                logger.info(f"Self-corrected args: {args}")
                except Exception as e:
                    logger.error(f"Step exception (attempt {attempt+1}): {e}")
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(2)

            if not success:
                logger.error(f"Step {self.step_num} FAILED after {MAX_RETRIES} attempts: {raw_step}")
                self.kb.learn_step({"step": self.step_num, "cmd": cmd, "args": args, "status": "failed"})
                # Take failure screenshot
                await self.take_screenshot(f"FAILED_step{self.step_num}")
                # Try to continue with remaining steps
                continue

        logger.info(f"\n{'='*60}")
        logger.info(f"EXECUTION COMPLETE: {total} steps processed")
        logger.info(f"Corrections made: {len(self.corrections)}")
        logger.info(f"{'='*60}")

        # Update execution list with corrections
        if self.corrections:
            self.update_execution_list()

    async def execute_step(self, cmd: str, args: list[str]) -> dict:
        """Execute a single step command."""

        if cmd == "navigate":
            return await self.cmd_navigate(args[0] if args else "")

        elif cmd == "wait":
            secs = int(args[0]) if args else 3
            logger.info(f"  Waiting {secs}s...")
            await asyncio.sleep(secs)
            return {"success": True}

        elif cmd == "screenshot":
            label = args[0] if args else f"step{self.step_num}"
            return await self.take_screenshot(label)

        elif cmd == "check_page":
            return await self.cmd_check_page(args)

        elif cmd == "discover_all":
            label = args[0] if args else "elements"
            return await self.cmd_discover_all(label)

        elif cmd == "click_at":
            x, y = int(args[0]), int(args[1])
            return await self._click_at(x, y)

        elif cmd == "find_and_click":
            return await self.cmd_find_and_click(args)

        elif cmd == "type_text":
            text = args[0] if args else ""
            return await self.cmd_type_text(text)

        elif cmd == "check_typed":
            return await self.cmd_check_typed(args)

        elif cmd == "poll_until_image":
            timeout = int(args[0]) if args else 120
            return await self.cmd_poll_until_image(timeout)

        elif cmd == "extract_images":
            action = args[0] if args else "log"
            return await self.cmd_extract_images(action)

        elif cmd == "save_flow":
            filename = args[0] if args else "learned_flow.json"
            return self.cmd_save_flow(filename)

        else:
            logger.warning(f"  Unknown command: {cmd}")
            return {"success": True}

    # ── Commands ──────────────────────────────────────────────────────────

    async def cmd_navigate(self, url: str) -> dict:
        logger.info(f"  Navigating to {url}")
        resp = await self.server.navigate(url)

        if resp.status != ResponseStatus.SUCCESS:
            logger.warning("  Extension navigate failed, opening via webbrowser...")
            webbrowser.open(url)

        # Poll until the page loads with real content
        for i in range(15):
            await asyncio.sleep(2)
            text = await self._get_page_text()
            if len(text) > 100 and "recaptcha" not in text.lower()[:200]:
                logger.info(f"  Page loaded ({len(text)} chars): {text[:80]}...")
                return {"success": True}
            logger.info(f"  Waiting for page... ({i+1}/15, {len(text)} chars)")

        return {"success": False, "error": "Page did not load with expected content"}

    async def cmd_check_page(self, args: list[str]) -> dict:
        expected = args[0] if args else ""
        text = await self._get_page_text()
        if expected.lower() in text.lower():
            logger.info(f"  Page check passed: found '{expected}'")
            return {"success": True}
        logger.warning(f"  Page check failed: '{expected}' not in page text ({len(text)} chars)")
        return {"success": False, "error": f"'{expected}' not found on page"}

    async def cmd_discover_all(self, label: str) -> dict:
        """Discover all interactive elements on the page."""
        logger.info("  Discovering all interactive elements (with shadow DOM piercing)...")

        resp = await self.server.execute_script("""
            const results = [];
            const seen = new Set();

            function addEl(el, role) {
                try {
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 5 || rect.height < 5) return;
                    const key = el.tagName + (el.id||'') + rect.x + rect.y;
                    if (seen.has(key)) return;
                    seen.add(key);

                    let selector = '';
                    if (el.id) selector = '#' + CSS.escape(el.id);
                    else if (el.getAttribute && el.getAttribute('aria-label')) selector = '[aria-label="' + el.getAttribute('aria-label') + '"]';
                    else if (el.name) selector = el.tagName.toLowerCase() + '[name="' + el.name + '"]';
                    else if (el.placeholder) selector = '[placeholder="' + el.placeholder + '"]';

                    results.push({
                        tag: el.tagName?.toLowerCase() || '?',
                        type: el.type || (el.getAttribute && el.getAttribute('role')) || '',
                        text: (el.textContent || '').trim().slice(0, 80),
                        placeholder: el.placeholder || '',
                        ariaLabel: (el.getAttribute && el.getAttribute('aria-label')) || '',
                        id: el.id || '',
                        name: el.name || '',
                        selector: selector,
                        contentEditable: el.contentEditable === 'true' || el.isContentEditable,
                        rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
                        role: role,
                        inShadow: el.getRootNode() !== document,
                    });
                } catch(e) {}
            }

            // Deep walk: traverse ALL nodes including shadow roots
            function walkDeep(root) {
                if (!root) return;
                const els = root.querySelectorAll ? root.querySelectorAll('*') : [];
                for (const el of els) {
                    const tag = el.tagName?.toLowerCase() || '';

                    // Inputs
                    if (['input','textarea','select'].includes(tag) || el.contentEditable === 'true' || el.isContentEditable ||
                        (el.getAttribute && (el.getAttribute('role') === 'textbox' || el.getAttribute('contenteditable') === 'true'))) {
                        addEl(el, 'input');
                    }

                    // Buttons
                    if (tag === 'button' || (el.getAttribute && el.getAttribute('role') === 'button') || tag === 'a') {
                        addEl(el, tag === 'a' ? 'link' : 'button');
                    }

                    // Images
                    if (tag === 'img' || tag === 'canvas') {
                        const r = el.getBoundingClientRect();
                        if (r.width > 80 && r.height > 80) addEl(el, 'image');
                    }

                    // Download
                    if (el.hasAttribute && (el.hasAttribute('download') ||
                        (el.getAttribute('aria-label')||'').toLowerCase().includes('download') ||
                        (el.getAttribute('title')||'').toLowerCase().includes('download'))) {
                        addEl(el, 'download');
                    }

                    // Recurse into shadow roots
                    if (el.shadowRoot) walkDeep(el.shadowRoot);
                }
            }

            // Start from document
            walkDeep(document);

            // Also try document.body children with open shadow roots
            document.querySelectorAll('*').forEach(el => {
                if (el.shadowRoot) walkDeep(el.shadowRoot);
            });

            return JSON.stringify(results);
        """)

        try:
            raw = resp.data.extracted_data.get("result", "[]")
            self.last_elements = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except:
            self.last_elements = []

        # Save discovered elements
        OUTPUT.mkdir(parents=True, exist_ok=True)
        path = OUTPUT / f"{label}.json"
        path.write_text(json.dumps(self.last_elements, indent=2), encoding="utf-8")

        # Log what we found
        inputs = [e for e in self.last_elements if e.get("role") == "input"]
        buttons = [e for e in self.last_elements if e.get("role") == "button"]
        images = [e for e in self.last_elements if e.get("role") == "image"]
        downloads = [e for e in self.last_elements if e.get("role") == "download"]

        logger.info(f"  Found: {len(inputs)} inputs, {len(buttons)} buttons, {len(images)} images, {len(downloads)} download elements")

        for inp in inputs[:5]:
            logger.info(f"    INPUT: [{inp.get('tag')}] placeholder='{inp.get('placeholder')}' aria='{inp.get('ariaLabel')}' sel='{inp.get('selector')}'")
        for btn in buttons[:10]:
            logger.info(f"    BUTTON: text='{btn.get('text')[:40]}' aria='{btn.get('ariaLabel')}' sel='{btn.get('selector')}'")

        # Store in knowledge
        self.kb.page_patterns["elements_count"] = len(self.last_elements)
        self.kb.save()

        return {"success": len(self.last_elements) > 0, "count": len(self.last_elements)}

    async def cmd_find_and_click(self, args: list[str]) -> dict:
        """Find an element by multiple selector strategies and click it."""
        selector_hints = args[0] if args else ""
        role_name = args[1] if len(args) > 1 else "element"

        logger.info(f"  Finding and clicking: {role_name} (hints: {selector_hints})")

        # Check if we already have a learned selector for this role
        if role_name in self.kb.selectors:
            learned = self.kb.selectors[role_name]
            logger.info(f"  Trying learned selector: {learned}")
            resp = await self.server.click(learned)
            if resp.status == ResponseStatus.SUCCESS:
                logger.info(f"  Clicked via learned selector!")
                return {"success": True, "selector": learned}

        # Parse selector hints — format: "selector1,selector2" or "keyword:text"
        candidates = []
        for hint in selector_hints.split(","):
            hint = hint.strip()
            if not hint:
                continue
            if ":" in hint and not hint.startswith("[") and not hint.startswith(".") and not hint.startswith("#"):
                # keyword match like "button:generate"
                tag, keyword = hint.split(":", 1)
                # Find matching elements from last discovery
                for el in self.last_elements:
                    if el.get("tag", "") == tag or not tag:
                        combined = (el.get("text", "") + el.get("ariaLabel", "") + el.get("placeholder", "")).lower()
                        if keyword.lower() in combined and el.get("selector"):
                            candidates.append(el["selector"])
            else:
                candidates.append(hint)

        # Also search by text content in last discovered elements
        for el in self.last_elements:
            sel = el.get("selector")
            if not sel:
                continue
            text = (el.get("text", "") + el.get("ariaLabel", "")).lower()
            for hint in selector_hints.lower().split(","):
                hint = hint.strip()
                if ":" in hint:
                    _, kw = hint.split(":", 1)
                    if kw in text and sel not in candidates:
                        candidates.append(sel)

        logger.info(f"  Candidates: {candidates[:8]}")

        # Try each candidate
        for sel in candidates:
            try:
                resp = await self.server.click(sel)
                if resp.status == ResponseStatus.SUCCESS:
                    self.kb.learn_selector(role_name, sel)
                    logger.info(f"  Clicked: {sel}")
                    return {"success": True, "selector": sel}
                logger.info(f"  Failed: {sel}")
            except Exception as e:
                logger.info(f"  Error with {sel}: {e}")

        # Fallback: do a fresh discovery and try broader matching
        logger.warning("  No candidate worked, rediscovering...")
        await self.cmd_discover_all("retry_discover")

        # Try matching by aria-label or text content broadly
        for el in self.last_elements:
            combined = (el.get("text", "") + el.get("ariaLabel", "") + el.get("placeholder", "")).lower()
            for hint in selector_hints.lower().replace("button:", "").replace("input:", "").split(","):
                hint = hint.strip().split(":")[-1] if ":" in hint.strip() else hint.strip()
                if hint and hint in combined:
                    # Try selector first
                    sel = el.get("selector")
                    if sel:
                        resp = await self.server.click(sel)
                        if resp.status == ResponseStatus.SUCCESS:
                            self.kb.learn_selector(role_name, sel)
                            logger.info(f"  Clicked via rediscovery: {sel}")
                            return {"success": True, "selector": sel}
                    # Try coordinate click
                    rect = el.get("rect", {})
                    if rect.get("w") and rect.get("h"):
                        cx = rect["x"] + rect["w"] // 2
                        cy = rect["y"] + rect["h"] // 2
                        result = await self._click_at(cx, cy)
                        if result.get("success"):
                            logger.info(f"  Clicked via coordinates ({cx},{cy}): {el.get('text','')[:30]}")
                            return {"success": True, "method": "coordinates", "x": cx, "y": cy}

        # Last resort: try coordinate click on all discovered elements matching role
        target_role = "input" if "input" in role_name.lower() or "prompt" in role_name.lower() or "text" in role_name.lower() else "button"
        for el in self.last_elements:
            if el.get("role") == target_role:
                rect = el.get("rect", {})
                if rect.get("w") and rect.get("h"):
                    cx = rect["x"] + rect["w"] // 2
                    cy = rect["y"] + rect["h"] // 2
                    result = await self._click_at(cx, cy)
                    if result.get("success"):
                        logger.info(f"  Clicked element by role+coordinates ({cx},{cy})")
                        return {"success": True, "method": "role_coordinates"}

        return {"success": False, "error": f"Could not find clickable element for: {role_name}"}

    async def _click_at(self, x: int, y: int) -> dict:
        """Click at specific page coordinates using JS."""
        resp = await self.server.execute_script(f"""
            const el = document.elementFromPoint({x}, {y});
            if (!el) return JSON.stringify({{success: false, error: 'No element at point'}});
            el.click();
            el.dispatchEvent(new MouseEvent('click', {{bubbles: true, composed: true, clientX: {x}, clientY: {y}}}));
            return JSON.stringify({{success: true, tag: el.tagName, text: (el.textContent||'').trim().slice(0,40)}});
        """)
        try:
            raw = resp.data.extracted_data.get("result", "{}")
            return json.loads(raw) if isinstance(raw, str) else raw
        except:
            return {"success": False}

    async def cmd_type_text(self, text: str) -> dict:
        """Type text into the prompt input — handles shadow DOM and contenteditable."""
        logger.info(f"  Typing: {text[:50]}...")

        # Use a comprehensive script that walks shadow DOM to find and type into input
        resp = await self.server.execute_script(f"""
            const text = {json.dumps(text)};

            // Deep search for input elements, piercing shadow DOM
            function findInputs(root) {{
                const found = [];
                if (!root) return found;
                const all = root.querySelectorAll ? root.querySelectorAll('*') : [];
                for (const el of all) {{
                    const tag = el.tagName?.toLowerCase();
                    const r = el.getBoundingClientRect();
                    if (r.width < 30 || r.height < 15) continue;
                    if (tag === 'textarea' || tag === 'input' || el.contentEditable === 'true' || el.isContentEditable ||
                        (el.getAttribute && (el.getAttribute('role') === 'textbox' || el.getAttribute('contenteditable') === 'true'))) {{
                        found.push(el);
                    }}
                    if (el.shadowRoot) found.push(...findInputs(el.shadowRoot));
                }}
                return found;
            }}

            // Also check active element and its shadow path
            let inputs = findInputs(document);

            // Sort by position (prefer bottom of page — that's where the prompt usually is)
            inputs.sort((a, b) => b.getBoundingClientRect().y - a.getBoundingClientRect().y);

            for (const el of inputs) {{
                try {{
                    el.focus();
                    el.click();

                    const tag = el.tagName?.toLowerCase();
                    if (el.contentEditable === 'true' || el.isContentEditable) {{
                        // Clear and type via execCommand
                        el.textContent = '';
                        document.execCommand('selectAll', false, null);
                        document.execCommand('delete', false, null);
                        document.execCommand('insertText', false, text);
                        el.dispatchEvent(new Event('input', {{bubbles: true, composed: true}}));
                        return JSON.stringify({{success: true, method: 'contenteditable', tag: tag, y: el.getBoundingClientRect().y}});
                    }} else {{
                        el.value = '';
                        el.dispatchEvent(new Event('input', {{bubbles: true, composed: true}}));
                        for (const ch of text) {{
                            el.value += ch;
                            el.dispatchEvent(new Event('input', {{bubbles: true, composed: true}}));
                            el.dispatchEvent(new KeyboardEvent('keydown', {{key: ch, bubbles: true, composed: true}}));
                            el.dispatchEvent(new KeyboardEvent('keyup', {{key: ch, bubbles: true, composed: true}}));
                        }}
                        el.dispatchEvent(new Event('change', {{bubbles: true, composed: true}}));
                        return JSON.stringify({{success: true, method: 'value', tag: tag, y: el.getBoundingClientRect().y}});
                    }}
                }} catch(e) {{ continue; }}
            }}

            return JSON.stringify({{success: false, error: 'No input found (checked ' + inputs.length + ' candidates)'}});
        """)

        try:
            raw = resp.data.extracted_data.get("result", "{}")
            result = json.loads(raw) if isinstance(raw, str) else raw
            if result.get("success"):
                logger.info(f"  Typed via {result.get('method')} ({result.get('tag')})")
                return {"success": True}
        except:
            pass

        return {"success": False, "error": "Could not type text into any input"}

    async def cmd_check_typed(self, args: list[str]) -> dict:
        expected = args[0] if args else ""
        text = await self._get_page_text()
        if expected.lower() in text.lower():
            logger.info(f"  Text verified: '{expected}' found on page")
            return {"success": True}

        # Also check input values
        resp = await self.server.execute_script(f"""
            const expected = {json.dumps(expected.lower())};
            const inputs = [...document.querySelectorAll('textarea, input, [contenteditable="true"]')];
            for (const el of inputs) {{
                const val = (el.value || el.textContent || '').toLowerCase();
                if (val.includes(expected)) return JSON.stringify({{found: true, in: el.tagName}});
            }}
            return JSON.stringify({{found: false}});
        """)

        try:
            raw = resp.data.extracted_data.get("result", "{}")
            result = json.loads(raw) if isinstance(raw, str) else raw
            if result.get("found"):
                logger.info(f"  Text verified in {result.get('in')}")
                return {"success": True}
        except:
            pass

        return {"success": False, "error": f"Typed text '{expected}' not found"}

    async def cmd_poll_until_image(self, timeout: int) -> dict:
        """Poll page until generated images appear or loading stops."""
        logger.info(f"  Polling for image generation (max {timeout}s)...")
        start = time.time()
        last_state = ""

        while time.time() - start < timeout:
            resp = await self.server.execute_script("""
                const state = { loading: false, images: 0, newImages: 0 };

                // Check loading indicators
                const loaders = document.querySelectorAll(
                    '[class*="loading"], [class*="spinner"], [class*="progress"], ' +
                    '[role="progressbar"], [class*="generating"], [class*="pending"]'
                );
                for (const l of loaders) {
                    if (l.getBoundingClientRect().width > 0 && l.offsetParent !== null) {
                        state.loading = true;
                        break;
                    }
                }

                // Count large images
                document.querySelectorAll('img, canvas').forEach(el => {
                    const r = el.getBoundingClientRect();
                    if (r.width > 150 && r.height > 150) state.images++;
                });

                // Check for download buttons
                const dl = document.querySelector('[download], [aria-label*="ownload"], [title*="ownload"], button');
                const allBtns = document.querySelectorAll('button, [role="button"]');
                for (const b of allBtns) {
                    const t = (b.textContent + (b.getAttribute('aria-label') || '')).toLowerCase();
                    if (t.includes('download') || t.includes('save')) {
                        state.downloadAvailable = true;
                        break;
                    }
                }

                return JSON.stringify(state);
            """)

            try:
                raw = resp.data.extracted_data.get("result", "{}")
                state = json.loads(raw) if isinstance(raw, str) else raw
            except:
                state = {}

            elapsed = int(time.time() - start)
            state_str = json.dumps(state)
            if state_str != last_state:
                logger.info(f"  [{elapsed}s] {state_str}")
                last_state = state_str

            # Done conditions: has images AND not loading, or download button appeared
            if state.get("images", 0) > 0 and not state.get("loading"):
                logger.info(f"  Images ready! ({state['images']} found)")
                return {"success": True, "images": state["images"]}

            if state.get("downloadAvailable") and not state.get("loading"):
                logger.info(f"  Download available!")
                return {"success": True}

            await asyncio.sleep(3)

        return {"success": False, "error": f"No images after {timeout}s"}

    async def cmd_extract_images(self, action: str) -> dict:
        """Extract and optionally save all generated images."""
        resp = await self.server.execute_script("""
            const images = [];
            document.querySelectorAll('img, canvas').forEach(el => {
                const r = el.getBoundingClientRect();
                if (r.width > 150 && r.height > 150) {
                    let src = '';
                    if (el.tagName === 'CANVAS') {
                        try { src = el.toDataURL('image/png'); } catch {}
                    } else {
                        src = el.src || '';
                    }
                    images.push({ src, tag: el.tagName, w: r.width, h: r.height });
                }
            });
            return JSON.stringify(images);
        """)

        try:
            raw = resp.data.extracted_data.get("result", "[]")
            images = json.loads(raw) if isinstance(raw, str) else raw
        except:
            images = []

        logger.info(f"  Found {len(images)} images")

        if action == "save" and images:
            OUTPUT.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            saved = 0
            for i, img in enumerate(images):
                src = img.get("src", "")
                if src.startswith("data:image"):
                    b64 = src.split(",", 1)[1] if "," in src else ""
                    path = OUTPUT / f"generated_{ts}_{i}.png"
                    path.write_bytes(base64.b64decode(b64))
                    logger.info(f"  Saved: {path}")
                    saved += 1
                elif src.startswith("http"):
                    logger.info(f"  Image URL: {src}")

            # Also take a screenshot as backup
            await self.take_screenshot(f"extracted_{ts}")
            return {"success": saved > 0 or len(images) > 0, "saved": saved, "total": len(images)}

        return {"success": len(images) > 0, "total": len(images)}

    def cmd_save_flow(self, filename: str) -> dict:
        """Save the learned flow to a file."""
        OUTPUT.mkdir(parents=True, exist_ok=True)
        path = OUTPUT / filename
        path.write_text(json.dumps({
            "selectors": self.kb.selectors,
            "flow_steps": self.kb.flow_steps,
            "corrections": self.corrections,
            "screenshots": self.screenshots,
            "saved": datetime.now().isoformat(),
        }, indent=2))
        logger.info(f"  Flow saved to {path}")
        return {"success": True}

    # ── Self-correction ───────────────────────────────────────────────────

    async def self_correct(self, cmd: str, args: list[str], failure: dict) -> list[str] | None:
        """Try to fix a failed step by re-discovering the page and adjusting selectors."""
        logger.info("  SELF-CORRECTING...")

        # Take a screenshot to see current state
        await self.take_screenshot(f"correction_step{self.step_num}")

        # Re-discover elements
        await self.cmd_discover_all(f"correction_{self.step_num}")

        if cmd == "find_and_click":
            role_name = args[1] if len(args) > 1 else "element"
            # Try broader search based on role name keywords
            keywords = role_name.lower().split()
            for el in self.last_elements:
                combined = (el.get("text", "") + el.get("ariaLabel", "") + el.get("placeholder", "")).lower()
                if any(kw in combined for kw in keywords) and el.get("selector"):
                    new_args = [el["selector"], role_name]
                    self.corrections.append({
                        "step": self.step_num,
                        "original_args": args,
                        "corrected_args": new_args,
                        "reason": f"Found match by keyword in: {combined[:60]}",
                    })
                    return new_args

        elif cmd == "type_text":
            # Try clicking any visible input first, then retry type
            for el in self.last_elements:
                if el.get("role") == "input" and el.get("selector"):
                    await self.server.click(el["selector"])
                    await asyncio.sleep(0.5)
                    return args  # retry with same args after clicking input

        elif cmd == "navigate":
            # Already handled by webbrowser fallback
            return args

        return None

    def update_execution_list(self):
        """Rewrite execution-list.txt with corrected selectors."""
        if not self.corrections:
            return

        lines = EXECUTION_LIST.read_text().splitlines()
        step_idx = 0
        new_lines = []

        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                new_lines.append(line)
                continue

            step_idx += 1
            corrected = None
            for c in self.corrections:
                if c["step"] == step_idx:
                    corrected = c
                    break

            if corrected:
                parts = stripped.split("|")
                cmd = parts[0].strip()
                new_args = corrected["corrected_args"]
                new_line = cmd + " | " + " | ".join(new_args)
                new_lines.append(new_line + f"  # auto-corrected from: {' | '.join(corrected['original_args'])}")
                logger.info(f"  Updated step {step_idx}: {new_line}")
            else:
                new_lines.append(line)

        EXECUTION_LIST.write_text("\n".join(new_lines) + "\n")
        logger.info(f"Execution list updated with {len(self.corrections)} corrections")

    # ── Helpers ───────────────────────────────────────────────────────────

    async def take_screenshot(self, label: str) -> dict:
        resp = await self.server.screenshot()
        if resp.data.screenshot:
            OUTPUT.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%H%M%S")
            path = OUTPUT / f"{label}_{ts}.png"
            path.write_bytes(base64.b64decode(resp.data.screenshot))
            self.screenshots.append(str(path))
            logger.info(f"  Screenshot: {path}")
            return {"success": True, "path": str(path)}
        return {"success": False, "error": "Screenshot failed"}

    async def _get_page_text(self) -> str:
        resp = await self.server.extract("text")
        text = str(resp.data.extracted_data.get("text", ""))
        self.last_page_text = text
        return text


# ── Parse execution list ──────────────────────────────────────────────────────

def parse_execution_list(path: Path, prompt_override: str | None = None) -> list[str]:
    lines = path.read_text().splitlines()
    steps = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Strip inline comments
        if "  #" in stripped:
            stripped = stripped[:stripped.index("  #")].strip()
        steps.append(stripped)

    # Override prompt if specified
    if prompt_override:
        steps = [
            s.replace("A cyberpunk city at sunset with neon lights reflecting on wet streets", prompt_override)
            if s.startswith("type_text") else s
            for s in steps
        ]

    return steps


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Self-correcting execution runner")
    parser.add_argument("--list", default="execution-list.txt", help="Execution list file")
    parser.add_argument("--prompt", default=None, help="Override the image prompt")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    list_path = Path(args.list)
    if not list_path.exists():
        print(f"ERROR: {list_path} not found")
        sys.exit(1)

    steps = parse_execution_list(list_path, args.prompt)
    print(f"Loaded {len(steps)} steps from {list_path}")

    OUTPUT.mkdir(parents=True, exist_ok=True)

    server = WebSocketServer(port=args.port)
    await server.start()
    print(f"Auth token: {server.auth_token}")
    print("Waiting for Chrome extension to connect...")
    await server.wait_for_connection(timeout=300)
    print("Extension connected!\n")

    kb = Knowledge()
    runner = ExecutionRunner(server, kb)
    await runner.run_list(steps)

    await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
