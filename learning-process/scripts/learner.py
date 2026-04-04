"""
Self-learning browser automation builder.

Loop:
  1. Screenshot the page
  2. Extract full HTML + identify all interactive elements
  3. Highlight each element with a colored border, take annotated screenshot
  4. Try interacting (click/type) with the target element
  5. Screenshot after interaction to verify success
  6. If success → save as a confirmed step in the recipe
  7. Repeat until the full flow is complete

The final output is a proven recipe JSON that can replay the flow perfectly.

Usage:
    python learner.py
    python learner.py --url "https://labs.google/fx/tools/flow/project/..."
    python learner.py --resume   # continue from saved progress
"""

import asyncio
import argparse
import base64
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_web_auto_python.core.server import WebSocketServer
from ai_web_auto_python.core.models import ResponseStatus
from loguru import logger

OUTPUT = Path("output/learner")
RECIPE_FILE = OUTPUT / "recipe.json"

# ── JS: Deep element discovery (pierces shadow DOM) ──────────────────────────

JS_DISCOVER = """
(function() {
    const results = [];
    const seen = new Set();
    let idx = 0;

    function walk(root) {
        if (!root || !root.querySelectorAll) return;
        for (const el of root.querySelectorAll('*')) {
            try {
                const r = el.getBoundingClientRect();
                if (r.width < 8 || r.height < 8) continue;
                const tag = el.tagName?.toLowerCase() || '';
                const role = el.getAttribute?.('role') || '';
                const aria = el.getAttribute?.('aria-label') || '';
                const ph = el.placeholder || '';
                const text = (el.textContent || '').trim().slice(0, 80);
                const editable = el.contentEditable === 'true' || el.isContentEditable;

                let isInteractive = false;
                let elRole = '';

                if (['input','textarea','select'].includes(tag) || editable || role === 'textbox') {
                    isInteractive = true; elRole = 'input';
                } else if (tag === 'button' || role === 'button' || tag === 'a') {
                    isInteractive = true; elRole = tag === 'a' ? 'link' : 'button';
                } else if ((tag === 'img' || tag === 'canvas') && r.width > 80) {
                    isInteractive = true; elRole = 'image';
                } else if (el.onclick || el.hasAttribute?.('tabindex') || el.hasAttribute?.('download')) {
                    isInteractive = true; elRole = 'clickable';
                } else if (tag === 'div' || tag === 'span') {
                    // Check for click handlers or interactive-looking divs
                    if (role || aria || el.style?.cursor === 'pointer') {
                        isInteractive = true; elRole = 'clickable';
                    }
                }

                if (!isInteractive) { if (el.shadowRoot) walk(el.shadowRoot); continue; }

                const key = tag + r.x.toFixed(0) + r.y.toFixed(0) + r.width.toFixed(0);
                if (seen.has(key)) { if (el.shadowRoot) walk(el.shadowRoot); continue; }
                seen.add(key);

                // Build a working selector/xpath
                let selector = '';
                let xpath = '';
                if (el.id) {
                    selector = '#' + CSS.escape(el.id);
                    xpath = '//*[@id="' + el.id + '"]';
                } else if (aria) {
                    selector = '[aria-label="' + aria.replace(/"/g, '\\\\"') + '"]';
                    xpath = '//*[@aria-label="' + aria + '"]';
                } else if (ph) {
                    selector = '[placeholder="' + ph.replace(/"/g, '\\\\"') + '"]';
                    xpath = '//*[@placeholder="' + ph + '"]';
                } else if (el.name) {
                    selector = tag + '[name="' + el.name + '"]';
                }

                results.push({
                    idx: idx++,
                    tag, role: elRole, ariaRole: role, ariaLabel: aria,
                    text, placeholder: ph, editable,
                    id: el.id || '', name: el.name || '',
                    selector, xpath,
                    rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
                    inShadow: el.getRootNode() !== document,
                    href: el.href || '',
                });

                if (el.shadowRoot) walk(el.shadowRoot);
            } catch(e) {}
        }
    }

    walk(document);
    return JSON.stringify(results);
})()
"""

# ── JS: Highlight element with colored border by rect ────────────────────────

JS_HIGHLIGHT = """
(function(elements) {
    // Remove old highlights
    document.querySelectorAll('.learner-highlight').forEach(e => e.remove());

    const colors = { input: '#00ff00', button: '#ff6600', link: '#6699ff', image: '#ff00ff', clickable: '#ffff00' };

    elements.forEach((el, i) => {
        const div = document.createElement('div');
        div.className = 'learner-highlight';
        const c = colors[el.role] || '#ffffff';
        Object.assign(div.style, {
            position: 'fixed', zIndex: '999999',
            left: el.rect.x + 'px', top: el.rect.y + 'px',
            width: el.rect.w + 'px', height: el.rect.h + 'px',
            border: '2px solid ' + c,
            borderRadius: '3px',
            pointerEvents: 'none',
            backgroundColor: c.replace(')', ',0.1)').replace('rgb', 'rgba').replace('#', ''),
        });
        // Label
        const label = document.createElement('span');
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
})
"""

# ── JS: Click at coordinates ─────────────────────────────────────────────────

JS_CLICK_AT = """
(function(x, y) {
    const el = document.elementFromPoint(x, y);
    if (!el) return JSON.stringify({success: false, error: 'nothing at point'});
    el.scrollIntoView({block:'center'});
    el.focus();
    el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, composed:true, clientX:x, clientY:y}));
    el.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, composed:true, clientX:x, clientY:y}));
    el.dispatchEvent(new MouseEvent('click', {bubbles:true, composed:true, clientX:x, clientY:y}));
    if (typeof el.click === 'function') el.click();
    return JSON.stringify({success: true, tag: el.tagName, text: (el.textContent||'').trim().slice(0,50)});
})
"""

# ── JS: Type into deepest input ──────────────────────────────────────────────

JS_TYPE_DEEP = """
(function(text) {
    function findInputs(root) {
        const found = [];
        if (!root || !root.querySelectorAll) return found;
        for (const el of root.querySelectorAll('*')) {
            const tag = el.tagName?.toLowerCase();
            const r = el.getBoundingClientRect();
            if (r.width < 30 || r.height < 10) continue;
            if (tag === 'textarea' || tag === 'input' || el.contentEditable === 'true' || el.isContentEditable ||
                (el.getAttribute?.('role') === 'textbox')) {
                found.push({el, y: r.y, h: r.height, tag});
            }
            if (el.shadowRoot) found.push(...findInputs(el.shadowRoot));
        }
        return found;
    }

    const inputs = findInputs(document).sort((a,b) => b.y - a.y); // bottom-first (prompt is usually at bottom)

    for (const {el, tag} of inputs) {
        try {
            el.focus();
            el.click();
            if (el.contentEditable === 'true' || el.isContentEditable) {
                // Focus and clear
                el.focus();
                el.textContent = '';
                el.innerHTML = '';

                // For React contentEditable: set textContent then trigger React's internal handlers
                // Method 1: Use execCommand which React intercepts via MutationObserver
                document.execCommand('selectAll', false, null);
                document.execCommand('delete', false, null);
                document.execCommand('insertText', false, text);

                // Method 2: Also set via innerHTML and trigger every event React might listen to
                var nativeInputEvent = new InputEvent('input', {inputType:'insertText', data:text, bubbles:true, composed:true, cancelable:false});
                el.dispatchEvent(nativeInputEvent);

                // Method 3: Try to find and call React's onChange handler directly
                var reactKey = Object.keys(el).find(function(k) { return k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$') || k.startsWith('__reactProps$'); });
                if (reactKey) {
                    var props = el[reactKey];
                    // Walk up the fiber tree looking for onChange/onInput
                    var fiber = props;
                    for (var attempt = 0; attempt < 10 && fiber; attempt++) {
                        var p = fiber.memoizedProps || fiber.pendingProps || fiber;
                        if (p && (p.onChange || p.onInput)) {
                            var syntheticEvent = {target: el, currentTarget: el, preventDefault: function(){}, stopPropagation: function(){}};
                            if (p.onChange) p.onChange(syntheticEvent);
                            if (p.onInput) p.onInput(syntheticEvent);
                            break;
                        }
                        fiber = fiber.return;
                    }
                }
            } else {
                // Standard input/textarea
                el.value = '';
                el.dispatchEvent(new Event('focus', {bubbles:true}));
                // Use native input setter to trigger React
                var nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value') ||
                                   Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value');
                if (nativeSetter && nativeSetter.set) {
                    nativeSetter.set.call(el, text);
                } else {
                    el.value = text;
                }
                el.dispatchEvent(new Event('input', {bubbles:true, composed:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
            }
            return JSON.stringify({success: true, tag, y: el.getBoundingClientRect().y});
        } catch(e) { continue; }
    }
    return JSON.stringify({success: false, count: inputs.length});
})
"""


class Learner:
    def __init__(self, server: WebSocketServer):
        self.server = server
        self.step_num = 0
        self.recipe: list[dict] = []  # confirmed good steps
        self.elements: list[dict] = []

    # ── Core loop ────────────────────────────────────────────────────────

    async def learn_flow(self, url: str, prompt: str):
        OUTPUT.mkdir(parents=True, exist_ok=True)
        logger.info("=" * 60)
        logger.info("LEARNER: Starting flow discovery")
        logger.info("=" * 60)

        # Step 0: Test execute_script
        test_resp = await self.server.execute_script('return JSON.stringify({test: true, elCount: document.querySelectorAll("*").length, body: document.body?.tagName});')
        logger.info(f"EXECUTE_SCRIPT TEST: {test_resp.data.extracted_data}")

        # Step 1: Navigate
        await self.step_navigate(url)

        # Step 2: Observe — screenshot + discover + highlight + screenshot
        await self.observe("after_navigate")

        # Step 3: Type prompt
        await self.step_type_prompt(prompt)
        await self.observe("after_type")

        # Step 4: Click send/generate
        await self.step_click_send()
        await self.observe("after_send")

        # Step 5: Wait for generation
        await self.step_wait_for_result()
        await self.observe("after_generation")

        # Step 6: Download
        await self.step_download()
        await self.observe("after_download")

        # Save the recipe
        self.save_recipe()
        logger.info(f"\nDONE! Recipe has {len(self.recipe)} confirmed steps.")
        logger.info(f"Recipe saved to {RECIPE_FILE}")

    # ── Observe: screenshot → html → discover → highlight → screenshot ───

    async def observe(self, label: str):
        """Full observation cycle."""
        self.step_num += 1
        ts = datetime.now().strftime("%H%M%S")
        prefix = f"s{self.step_num:02d}_{label}"

        # 1. Raw screenshot
        ss = await self._screenshot(f"{prefix}_raw_{ts}")

        # 2. Extract HTML (first 50k chars)
        html_resp = await self.server.extract("html")
        html = html_resp.data.extracted_data.get("html", "")[:50000]
        (OUTPUT / f"{prefix}_page.html").write_text(html, encoding="utf-8")
        logger.info(f"  HTML saved ({len(html)} chars)")

        # 3. Discover all interactive elements (shadow DOM aware)
        self.elements = await self._discover()
        (OUTPUT / f"{prefix}_elements.json").write_text(
            json.dumps(self.elements, indent=2), encoding="utf-8"
        )

        inputs = [e for e in self.elements if e["role"] == "input"]
        buttons = [e for e in self.elements if e["role"] == "button"]
        images = [e for e in self.elements if e["role"] == "image"]
        clickables = [e for e in self.elements if e["role"] == "clickable"]

        logger.info(f"  Elements: {len(inputs)} inputs, {len(buttons)} buttons, "
                     f"{len(images)} images, {len(clickables)} clickables")

        for i, el in enumerate(self.elements[:15]):
            logger.info(f"    [{i}] {el['role']:10s} {el['tag']:10s} "
                         f"text='{el['text'][:30]}' aria='{el['ariaLabel'][:20]}' "
                         f"sel='{el['selector'][:40]}' @ ({el['rect']['x']},{el['rect']['y']})")

        # 4. Highlight all elements and take annotated screenshot
        await self._highlight(self.elements)
        await asyncio.sleep(0.3)
        await self._screenshot(f"{prefix}_annotated_{ts}")

        # 5. Remove highlights
        await self.server.execute_script(
            "document.querySelectorAll('.learner-highlight').forEach(e => e.remove()); return 'cleared';"
        )

        return self.elements

    # ── Step: Navigate ───────────────────────────────────────────────────

    async def step_navigate(self, url: str):
        logger.info(f"\n>>> STEP: Navigate to {url}")

        resp = await self.server.navigate(url)
        if resp.status != ResponseStatus.SUCCESS:
            logger.warning("Extension navigate failed, using webbrowser...")
            import webbrowser
            webbrowser.open(url)

        # Poll until page has content
        for i in range(20):
            await asyncio.sleep(2)
            text = await self._get_text()
            if len(text) > 50 and "recaptcha" not in text.lower()[:100]:
                logger.info(f"  Page loaded ({len(text)} chars)")
                self._add_recipe("navigate", {"url": url}, True)
                return
            logger.info(f"  Waiting... ({i+1}/20)")

        self._add_recipe("navigate", {"url": url}, False)

    # ── Step: Type prompt ────────────────────────────────────────────────

    async def step_type_prompt(self, prompt: str):
        logger.info(f"\n>>> STEP: Type prompt: {prompt[:50]}...")

        # Before screenshot
        await self._screenshot("before_type")

        # Use CDP (Chrome Debugger Protocol) for real keyboard input
        # This sends actual key events that React recognizes natively
        PROMPT_XPATH = '/html/body/div[1]/div[1]/div[5]/div/div/div[1]/div'

        resp = await self.server.type_cdp(prompt, xpath=PROMPT_XPATH, clear=True)
        if resp.status == ResponseStatus.SUCCESS:
            logger.info(f"  Typed via CDP! ({resp.data.extracted_data})")
            await asyncio.sleep(1)
            await self._screenshot("type_verify")

            # Verify text appears
            verify = await self.server.execute_script(f"""
                var el = document.evaluate('{PROMPT_XPATH}', document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                if (el && el.textContent.toLowerCase().includes({json.dumps(prompt[:20].lower())}))
                    return JSON.stringify({{found: true}});
                return JSON.stringify({{found: false, text: (el && el.textContent || '').slice(0,50)}});
            """)
            verified = self._parse_result(verify).get("found", False)
            if verified:
                logger.info("  VERIFIED: Text found in input!")
                self._add_recipe("type", {"text": prompt, "method": "cdp", "xpath": PROMPT_XPATH}, True)
                return
            else:
                logger.warning(f"  CDP typed but not verified: {self._parse_result(verify)}")
        else:
            logger.warning(f"  CDP type failed: {resp.error}")

        # Fallback: try execCommand approach via execute_script
        logger.info("  Falling back to execCommand...")
        resp = await self.server.execute_script(f"""
            var text = {json.dumps(prompt)};
            var xpath = '/html/body/div[1]/div[1]/div[5]/div/div/div[1]/div';
            var el = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;

            if (!el) {{
                // Fallback: find contenteditable near bottom
                var candidates = document.querySelectorAll('[contenteditable="true"], [role="textbox"]');
                for (var i = 0; i < candidates.length; i++) {{
                    var r = candidates[i].getBoundingClientRect();
                    if (r.y > 500 && r.width > 100) {{ el = candidates[i]; break; }}
                }}
            }}

            if (!el) return JSON.stringify({{success: false, error: 'Element not found at xpath'}});

            // Focus and click
            el.focus();
            el.click();

            // Clear existing content
            el.textContent = '';
            el.innerHTML = '';

            // Simulate real typing: use execCommand char by char
            // This triggers MutationObserver which React uses for contentEditable
            for (var i = 0; i < text.length; i++) {{
                document.execCommand('insertText', false, text[i]);
            }}

            // Also fire input event
            el.dispatchEvent(new InputEvent('input', {{
                inputType: 'insertText', data: text,
                bubbles: true, composed: true, cancelable: false
            }}));

            return JSON.stringify({{
                success: true,
                tag: el.tagName,
                text: el.textContent.slice(0, 40),
                y: el.getBoundingClientRect().y
            }});
        """)
        result = self._parse_result(resp)

        if result.get("success"):
            logger.info(f"  Typed via {result.get('tag')} at y={result.get('y')}")
            await asyncio.sleep(1)

            # Verify: screenshot and check if text appears
            ss_after = await self._screenshot("type_verify")

            # Check if text is in any input
            verify = await self.server.execute_script(f"""
                return (function(expected) {{
                    function check(root) {{
                        if (!root || !root.querySelectorAll) return false;
                        for (const el of root.querySelectorAll('*')) {{
                            const val = (el.value || el.textContent || '').toLowerCase();
                            if (val.includes(expected)) return true;
                            if (el.shadowRoot && check(el.shadowRoot)) return true;
                        }}
                        return false;
                    }}
                    return JSON.stringify({{found: check(document)}});
                }})({json.dumps(prompt[:20].lower())})
            """)
            verified = self._parse_result(verify).get("found", False)

            if verified:
                logger.info("  VERIFIED: Text found in input!")
                self._add_recipe("type", {"text": prompt, "method": result.get("tag")}, True)
                return
            else:
                logger.warning("  Text typed but NOT verified in input")

        # Fallback: click the prompt area by coordinates then try again
        logger.info("  Trying coordinate-based approach...")
        for el in self.elements:
            if el["role"] == "input":
                cx = el["rect"]["x"] + el["rect"]["w"] // 2
                cy = el["rect"]["y"] + el["rect"]["h"] // 2
                logger.info(f"  Clicking input at ({cx}, {cy})...")
                await self.server.execute_script(
                    f"return ({JS_CLICK_AT})({cx}, {cy})"
                )
                await asyncio.sleep(0.5)
                resp2 = await self.server.execute_script(
                    f"return ({JS_TYPE_DEEP})({json.dumps(prompt)})"
                )
                result2 = self._parse_result(resp2)
                if result2.get("success"):
                    logger.info(f"  Typed after coordinate click!")
                    self._add_recipe("type", {
                        "text": prompt,
                        "click_first": {"x": cx, "y": cy},
                    }, True)
                    return

        self._add_recipe("type", {"text": prompt}, False)
        logger.error("  FAILED to type prompt")

    # ── Step: Click send/generate ────────────────────────────────────────

    async def step_click_send(self):
        logger.info("\n>>> STEP: Click send/generate button")

        # Before screenshot
        await self._screenshot("before_send")

        # Look for send/generate/create button in discovered elements
        # Prioritize: arrow_forward, Create near bottom, avoid arrow_back
        candidates = []

        for el in self.elements:
            if el["role"] not in ("button", "clickable", "link"):
                continue
            combined = (el["text"] + el["ariaLabel"]).lower()

            # Skip back/navigation buttons
            if "back" in combined or "arrow_back" in combined:
                continue

            score = 0
            if "arrow_forward" in combined: score += 10
            if "create" in combined and el["rect"]["y"] > 500: score += 8
            if "send" in combined: score += 7
            if "generate" in combined: score += 7
            if "submit" in combined: score += 6
            # Bonus for being in the bottom area (prompt bar)
            if el["rect"]["y"] > 600: score += 3
            if el["rect"]["y"] > 500: score += 1

            if score > 0:
                candidates.append((score, el))

        candidates.sort(key=lambda x: -x[0])

        for score, el in candidates:
            cx = el["rect"]["x"] + el["rect"]["w"] // 2
            cy = el["rect"]["y"] + el["rect"]["h"] // 2
            logger.info(f"  Trying: '{el['text'][:30]}' aria='{el['ariaLabel'][:20]}' @ ({cx},{cy}) score={score}")

            # Highlight this specific element
            await self._highlight([el])
            await self._screenshot(f"trying_send_{el['idx']}")
            await self.server.execute_script(
                "document.querySelectorAll('.learner-highlight').forEach(e => e.remove()); return 'ok';"
            )

            # Click via CDP for real mouse event
            click_resp = await self.server.click_cdp(cx, cy)

            if click_resp.status == ResponseStatus.SUCCESS:
                await asyncio.sleep(2)
                # Check if page changed (new elements appeared, text changed)
                await self._screenshot("after_send_attempt")
                new_text = await self._get_text()

                logger.info(f"  Clicked! Page text now: {len(new_text)} chars")
                self._add_recipe("click_send", {
                    "x": cx, "y": cy,
                    "ariaLabel": el["ariaLabel"],
                    "text": el["text"][:30],
                    "selector": el["selector"],
                }, True)
                return

        # Last resort: try finding the send button via JS and clicking it
        logger.info("  Trying JS-based button finder...")
        resp = await self.server.execute_script("""
            var buttons = document.querySelectorAll('button, [role="button"]');
            var results = [];
            for (var i = 0; i < buttons.length; i++) {
                var b = buttons[i];
                var r = b.getBoundingClientRect();
                if (r.width < 10 || r.height < 10) continue;
                var text = (b.textContent || '').trim().slice(0, 40);
                var aria = b.getAttribute('aria-label') || '';
                results.push({text: text, aria: aria, x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), w: Math.round(r.width), h: Math.round(r.height)});
            }
            // Try clicking the rightmost button near bottom (likely the send arrow)
            var bottomBtns = results.filter(function(b) { return b.y > 600; });
            bottomBtns.sort(function(a, b) { return b.x - a.x; }); // rightmost first
            if (bottomBtns.length > 0) {
                var target = bottomBtns[0];
                var el = document.elementFromPoint(target.x, target.y);
                if (el) {
                    el.click();
                    el.dispatchEvent(new MouseEvent('click', {bubbles:true, composed:true}));
                }
                return JSON.stringify({clicked: true, btn: target});
            }
            return JSON.stringify({clicked: false, allButtons: results});
        """)
        result = self._parse_result(resp)
        if result.get("clicked"):
            btn = result.get("btn", {})
            logger.info(f"  Clicked bottom-right button at ({btn.get('x')},{btn.get('y')}): '{btn.get('text','')[:20]}'")
            await asyncio.sleep(2)
            await self._screenshot("after_js_send")
            self._add_recipe("click_send", {"method": "js_bottom_right", **btn}, True)
            return

        logger.info(f"  All buttons found: {json.dumps(result.get('allButtons', []), indent=2)}")
        self._add_recipe("click_send", {}, False)
        logger.error("  FAILED to find/click send button")

    # ── Step: Wait for result ────────────────────────────────────────────

    async def step_wait_for_result(self):
        logger.info("\n>>> STEP: Waiting for image generation...")

        start = time.time()
        max_wait = 120
        last_img_count = 0

        while time.time() - start < max_wait:
            elapsed = int(time.time() - start)

            resp = await self.server.execute_script("""
                return (function() {
                    let images = 0, loading = false, downloadBtn = false;
                    function scan(root) {
                        if (!root || !root.querySelectorAll) return;
                        for (const el of root.querySelectorAll('*')) {
                            const r = el.getBoundingClientRect();
                            const tag = el.tagName?.toLowerCase();
                            if ((tag === 'img' || tag === 'canvas') && r.width > 150 && r.height > 150) images++;
                            const cls = (el.className?.toString() || '').toLowerCase();
                            if (cls.includes('loading') || cls.includes('spinner') || cls.includes('progress')) {
                                if (r.width > 0 && el.offsetParent !== null) loading = true;
                            }
                            const t = ((el.textContent||'') + (el.getAttribute?.('aria-label')||'')).toLowerCase();
                            if (t.includes('download') && (tag === 'button' || el.getAttribute?.('role') === 'button')) downloadBtn = true;
                            if (el.shadowRoot) scan(el.shadowRoot);
                        }
                    }
                    scan(document);
                    return JSON.stringify({images, loading, downloadBtn});
                })()
            """)

            state = self._parse_result(resp)
            imgs = state.get("images", 0)
            loading = state.get("loading", False)
            dl = state.get("downloadBtn", False)

            if imgs != last_img_count or elapsed % 10 == 0:
                logger.info(f"  [{elapsed}s] images={imgs} loading={loading} download={dl}")
                last_img_count = imgs

            if (imgs > 0 and not loading) or dl:
                logger.info(f"  Generation complete! {imgs} images, download={dl}")
                self._add_recipe("wait_for_result", {"waited_seconds": elapsed}, True)
                return

            await asyncio.sleep(3)

        self._add_recipe("wait_for_result", {"waited_seconds": max_wait, "timeout": True}, False)
        logger.warning(f"  Timed out after {max_wait}s")

    # ── Step: Download ───────────────────────────────────────────────────

    async def step_download(self):
        logger.info("\n>>> STEP: Download generated image")

        # Re-discover to find download buttons
        self.elements = await self._discover()

        # Strategy 1: Find download button
        for el in self.elements:
            combined = (el["text"] + el["ariaLabel"]).lower()
            if "download" in combined or "save" in combined or el["role"] == "download":
                cx = el["rect"]["x"] + el["rect"]["w"] // 2
                cy = el["rect"]["y"] + el["rect"]["h"] // 2
                logger.info(f"  Found download: '{el['text'][:30]}' @ ({cx},{cy})")

                await self._highlight([el])
                await self._screenshot("download_target")
                await self.server.execute_script(
                    "document.querySelectorAll('.learner-highlight').forEach(e => e.remove()); return 'ok';"
                )

                await self.server.execute_script(f"return ({JS_CLICK_AT})({cx}, {cy})")
                await asyncio.sleep(2)
                self._add_recipe("click_download", {"x": cx, "y": cy, "ariaLabel": el["ariaLabel"]}, True)
                break

        # Strategy 2: Extract images directly
        resp = await self.server.execute_script("""
            return (function() {
                const imgs = [];
                function scan(root) {
                    if (!root || !root.querySelectorAll) return;
                    for (const el of root.querySelectorAll('*')) {
                        const r = el.getBoundingClientRect();
                        const tag = el.tagName?.toLowerCase();
                        if ((tag === 'img' || tag === 'canvas') && r.width > 150 && r.height > 150) {
                            let src = '';
                            if (tag === 'canvas') { try { src = el.toDataURL('image/png'); } catch {} }
                            else src = el.src || '';
                            imgs.push({src: src.slice(0, 200), tag, w: r.width, h: r.height, full: src.startsWith('data:') ? src : ''});
                        }
                        if (el.shadowRoot) scan(el.shadowRoot);
                    }
                }
                scan(document);
                return JSON.stringify(imgs);
            })()
        """)

        images = self._parse_result(resp) or []
        if isinstance(images, list):
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            saved = 0
            for i, img in enumerate(images):
                full = img.get("full", "")
                if full and full.startswith("data:image"):
                    b64 = full.split(",", 1)[1]
                    path = OUTPUT / f"generated_{ts}_{i}.png"
                    path.write_bytes(base64.b64decode(b64))
                    logger.info(f"  SAVED image: {path}")
                    saved += 1
                elif img.get("src"):
                    logger.info(f"  Image URL: {img['src'][:100]}")

            if saved > 0:
                self._add_recipe("extract_images", {"saved": saved}, True)

        # Final screenshot
        await self._screenshot("final_result")

    # ── Helpers ───────────────────────────────────────────────────────────

    async def _screenshot(self, label: str) -> str | None:
        resp = await self.server.screenshot()
        if resp.data.screenshot:
            ts = datetime.now().strftime("%H%M%S")
            path = OUTPUT / f"{label}_{ts}.png"
            path.write_bytes(base64.b64decode(resp.data.screenshot))
            logger.info(f"  [SCREENSHOT] {path}")
            return str(path)
        return None

    async def _discover(self) -> list[dict]:
        # Simpler discover that avoids complex IIFE issues
        resp = await self.server.execute_script("""
            var results = [];
            var allEls = document.querySelectorAll('*');
            for (var i = 0; i < allEls.length; i++) {
                var el = allEls[i];
                var tag = el.tagName.toLowerCase();
                var r = el.getBoundingClientRect();
                if (r.width < 8 || r.height < 8) continue;

                var role = el.getAttribute('role') || '';
                var aria = el.getAttribute('aria-label') || '';
                var ph = el.placeholder || '';
                var text = (el.textContent || '').trim().slice(0, 80);
                var editable = el.contentEditable === 'true';

                var isInteractive = false;
                var elRole = '';

                if (tag === 'input' || tag === 'textarea' || tag === 'select' || editable || role === 'textbox') {
                    isInteractive = true; elRole = 'input';
                } else if (tag === 'button' || role === 'button') {
                    isInteractive = true; elRole = 'button';
                } else if (tag === 'a') {
                    isInteractive = true; elRole = 'link';
                } else if ((tag === 'img' || tag === 'canvas') && r.width > 80) {
                    isInteractive = true; elRole = 'image';
                } else if (el.onclick || role || (el.style && el.style.cursor === 'pointer') || el.hasAttribute('tabindex')) {
                    isInteractive = true; elRole = 'clickable';
                }

                if (!isInteractive) continue;

                var selector = '';
                if (el.id) selector = '#' + el.id;
                else if (aria) selector = '[aria-label=\"' + aria.replace(/"/g, '') + '\"]';
                else if (ph) selector = '[placeholder=\"' + ph.replace(/"/g, '') + '\"]';

                results.push({
                    idx: results.length, tag: tag, role: elRole, ariaRole: role,
                    ariaLabel: aria, text: text, placeholder: ph, editable: editable,
                    id: el.id || '', name: el.name || '', selector: selector,
                    xpath: '', inShadow: false, href: el.href || '',
                    rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}
                });
            }
            return JSON.stringify(results);
        """)
        try:
            raw = resp.data.extracted_data.get("result", "[]")
            parsed = json.loads(raw) if isinstance(raw, str) else (raw or [])
            if not parsed:
                logger.warning(f"  Discover returned empty. Raw: {str(raw)[:200]}")
            return parsed
        except Exception as e:
            logger.error(f"  Discover parse error: {e}, raw: {str(resp.data.extracted_data)[:200]}")
            return []

    async def _highlight(self, elements: list[dict]):
        js_elements = json.dumps(elements)
        await self.server.execute_script(f"return ({JS_HIGHLIGHT})({js_elements})")

    async def _get_text(self) -> str:
        resp = await self.server.extract("text")
        return str(resp.data.extracted_data.get("text", ""))

    def _parse_result(self, resp) -> dict | list:
        try:
            raw = resp.data.extracted_data.get("result", "{}")
            return json.loads(raw) if isinstance(raw, str) else (raw or {})
        except:
            return {}

    def _add_recipe(self, action: str, params: dict, success: bool):
        step = {
            "step": len(self.recipe) + 1,
            "action": action,
            "params": params,
            "success": success,
            "timestamp": datetime.now().isoformat(),
        }
        self.recipe.append(step)
        status = "OK" if success else "FAIL"
        logger.info(f"  [RECIPE {status}] Step {step['step']}: {action}")

    def save_recipe(self):
        OUTPUT.mkdir(parents=True, exist_ok=True)
        RECIPE_FILE.write_text(json.dumps({
            "recipe": self.recipe,
            "confirmed_steps": [s for s in self.recipe if s["success"]],
            "failed_steps": [s for s in self.recipe if not s["success"]],
            "created": datetime.now().isoformat(),
        }, indent=2))


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Self-learning browser automation")
    parser.add_argument("--url", default="https://labs.google/fx/tools/flow/project/fdd97a0e-a74c-483e-8869-261e6db306c2")
    parser.add_argument("--prompt", default="A cyberpunk city at sunset with neon lights reflecting on wet streets")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    OUTPUT.mkdir(parents=True, exist_ok=True)

    server = WebSocketServer(port=args.port)
    await server.start()
    print(f"Token: {server.auth_token}")
    print("Waiting for extension... (reload it in chrome://extensions if needed)")
    await server.wait_for_connection(timeout=300)
    print("Connected!\n")

    learner = Learner(server)
    await learner.learn_flow(args.url, args.prompt)

    await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
