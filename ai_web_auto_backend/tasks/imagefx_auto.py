"""
ImageFX (Google AI Image Generator) automation.

Flow: Navigate → Type prompt → Click generate → Wait for image → Download

Usage:
    python -m python.tasks.imagefx_auto
    python -m python.tasks.imagefx_auto --prompt "A cat astronaut on Mars"
    python -m python.tasks.imagefx_auto --discover   # just gather DOM info
"""

from __future__ import annotations

import asyncio
import argparse
import base64
import json
import os
import time
from datetime import datetime
from pathlib import Path

from loguru import logger

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from ai_web_auto_backend.core.server import WebSocketServer
from ai_web_auto_backend.core.models import Command, ResponseStatus


IMAGEFX_URL = "https://labs.google/fx/tools/flow/"
OUTPUT_DIR = Path("learning-process/output/imagefx")
DISCOVER_DIR = Path("learning-process/output/discover")

# ── Known selectors (updated by discover mode) ──────────────────────────────
# These will be refined iteratively. Start with best guesses.

SELECTORS = {
    # Prompt input area
    "prompt_input": None,
    # Generate / Send button
    "generate_btn": None,
    # Image result container
    "image_result": None,
    # Download button
    "download_btn": None,
    # Loading indicator
    "loading_indicator": None,
}

SELECTORS_FILE = Path("learning-process/output/discover/imagefx_selectors.json")


def load_selectors():
    """Load previously discovered selectors."""
    if SELECTORS_FILE.exists():
        saved = json.loads(SELECTORS_FILE.read_text())
        SELECTORS.update(saved)
        logger.info(f"Loaded selectors from {SELECTORS_FILE}")
    return SELECTORS


def save_selectors(selectors: dict):
    SELECTORS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SELECTORS_FILE.write_text(json.dumps(selectors, indent=2))
    logger.info(f"Selectors saved to {SELECTORS_FILE}")


class ImageFXAutomation:
    def __init__(self, server: WebSocketServer):
        self.server = server
        self.selectors = load_selectors()
        self.iteration = 0

    # ── Phase 1: Discover page structure ─────────────────────────────────

    async def discover(self) -> dict:
        """Navigate to ImageFX and extract full DOM info for selector discovery."""
        logger.info("=== DISCOVER MODE: Gathering page structure ===")

        DISCOVER_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Navigate — try extension first, fall back to opening browser directly
        resp = await self.server.navigate(IMAGEFX_URL)
        logger.info(f"Navigate via extension: {resp.status}")

        if resp.status != ResponseStatus.SUCCESS:
            logger.warning("Extension navigate failed — opening browser directly...")
            import webbrowser
            webbrowser.open(IMAGEFX_URL)

        # Wait for SPA to fully hydrate, then poll until real content appears
        logger.info("Waiting for ImageFX to load...")
        loaded = False
        for attempt in range(10):
            await asyncio.sleep(3)
            text_resp = await self.server.extract("text")
            page_text = str(text_resp.data.extracted_data.get("text", ""))
            if len(page_text) > 100 and "recaptcha" not in page_text.lower():
                logger.info(f"Page loaded (attempt {attempt+1}): {page_text[:80]}...")
                loaded = True
                break
            logger.info(f"  Attempt {attempt+1}: page not ready yet ({len(page_text)} chars)")

        if not loaded:
            logger.warning("Page may not have loaded fully — continuing with what we have")

        # Take screenshot
        ss = await self.server.screenshot()
        if ss.data.screenshot:
            ss_path = DISCOVER_DIR / f"imagefx_page_{ts}.png"
            ss_path.write_bytes(base64.b64decode(ss.data.screenshot))
            logger.info(f"Screenshot saved: {ss_path}")

        # Extract full DOM text
        text_resp = await self.server.extract("text")
        if text_resp.data.extracted_data:
            text_path = DISCOVER_DIR / f"imagefx_text_{ts}.txt"
            text_path.write_text(
                json.dumps(text_resp.data.extracted_data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info(f"Page text saved: {text_path}")

        # Extract HTML
        html_resp = await self.server.extract("html")
        if html_resp.data.extracted_data:
            html_path = DISCOVER_DIR / f"imagefx_html_{ts}.html"
            html_path.write_text(
                html_resp.data.extracted_data.get("html", ""),
                encoding="utf-8",
            )
            logger.info(f"Page HTML saved: {html_path}")

        # Get interactive elements via custom script
        elements_resp = await self.server.execute_script("""
            const results = { inputs: [], buttons: [], textareas: [], images: [], misc: [] };

            // Find all input-like elements
            document.querySelectorAll('input, textarea, [contenteditable="true"], [role="textbox"]').forEach(el => {
                const rect = el.getBoundingClientRect();
                results.inputs.push({
                    tag: el.tagName, type: el.type || '', placeholder: el.placeholder || '',
                    ariaLabel: el.getAttribute('aria-label') || '',
                    id: el.id, className: el.className?.toString().slice(0, 100),
                    role: el.getAttribute('role') || '',
                    contentEditable: el.contentEditable,
                    selector: el.id ? '#' + el.id : null,
                    rect: { x: rect.x, y: rect.y, w: rect.width, h: rect.height },
                    visible: rect.width > 0 && rect.height > 0,
                });
            });

            // Find all buttons
            document.querySelectorAll('button, [role="button"], input[type="submit"]').forEach(el => {
                const rect = el.getBoundingClientRect();
                results.buttons.push({
                    tag: el.tagName, text: el.textContent?.trim().slice(0, 60),
                    ariaLabel: el.getAttribute('aria-label') || '',
                    id: el.id, className: el.className?.toString().slice(0, 100),
                    title: el.title || '',
                    disabled: el.disabled,
                    selector: el.id ? '#' + el.id : null,
                    rect: { x: rect.x, y: rect.y, w: rect.width, h: rect.height },
                    visible: rect.width > 0 && rect.height > 0,
                });
            });

            // Find images (result images)
            document.querySelectorAll('img, canvas').forEach(el => {
                const rect = el.getBoundingClientRect();
                if (rect.width > 100 && rect.height > 100) {
                    results.images.push({
                        tag: el.tagName, src: el.src?.slice(0, 200) || '',
                        alt: el.alt || '', id: el.id,
                        className: el.className?.toString().slice(0, 100),
                        rect: { x: rect.x, y: rect.y, w: rect.width, h: rect.height },
                    });
                }
            });

            // Find anything with "download" or "save" in attributes
            document.querySelectorAll('[download], [aria-label*="ownload"], [title*="ownload"]').forEach(el => {
                results.misc.push({
                    tag: el.tagName, text: el.textContent?.trim().slice(0, 60),
                    ariaLabel: el.getAttribute('aria-label') || '',
                    href: el.href || '', download: el.getAttribute('download') || '',
                });
            });

            // Find loading/spinner elements
            document.querySelectorAll('[class*="loading"], [class*="spinner"], [class*="progress"], [role="progressbar"]').forEach(el => {
                results.misc.push({
                    tag: el.tagName, type: 'loading-indicator',
                    className: el.className?.toString().slice(0, 100),
                    visible: el.getBoundingClientRect().width > 0,
                });
            });

            return JSON.stringify(results);
        """)

        elements = {}
        try:
            raw = elements_resp.data.extracted_data.get("result", "{}")
            if isinstance(raw, str):
                elements = json.loads(raw)
            else:
                elements = raw
        except Exception as e:
            logger.error(f"Failed to parse elements: {e}")

        elements_path = DISCOVER_DIR / f"imagefx_elements_{ts}.json"
        elements_path.write_text(json.dumps(elements, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"Interactive elements saved: {elements_path}")

        # Auto-detect selectors from discovered elements
        detected = self._auto_detect_selectors(elements)
        logger.info(f"Auto-detected selectors: {json.dumps(detected, indent=2)}")

        return {"elements": elements, "selectors": detected}

    def _auto_detect_selectors(self, elements: dict) -> dict:
        """Try to identify the correct selectors from discovered elements."""
        detected = {}

        # Find prompt input: look for textarea or contenteditable
        for inp in elements.get("inputs", []):
            if not inp.get("visible"):
                continue
            if inp.get("tag") == "TEXTAREA" or inp.get("contentEditable") == "true" or inp.get("role") == "textbox":
                sel = inp.get("selector")
                if not sel:
                    if inp.get("tag") == "TEXTAREA":
                        sel = "textarea"
                    elif inp.get("ariaLabel"):
                        sel = f'[aria-label="{inp["ariaLabel"]}"]'
                    elif inp.get("role"):
                        sel = f'[role="{inp["role"]}"]'
                detected["prompt_input"] = sel
                logger.info(f"  Prompt input: {sel} (tag={inp['tag']}, placeholder={inp.get('placeholder', '')})")
                break

        # Find generate button: look for button with "generate" or send icon
        for btn in elements.get("buttons", []):
            if not btn.get("visible"):
                continue
            text = (btn.get("text", "") + btn.get("ariaLabel", "") + btn.get("title", "")).lower()
            if any(kw in text for kw in ["generate", "create", "send", "go", "submit"]):
                sel = btn.get("selector")
                if not sel:
                    if btn.get("ariaLabel"):
                        sel = f'[aria-label="{btn["ariaLabel"]}"]'
                    else:
                        sel = f'button'
                detected["generate_btn"] = sel
                logger.info(f"  Generate button: {sel} (text={btn.get('text', '')[:30]})")
                break

        # Find download elements
        for item in elements.get("misc", []):
            if item.get("download") or "download" in (item.get("ariaLabel", "") + item.get("text", "")).lower():
                detected["download_btn"] = f'[aria-label="{item["ariaLabel"]}"]' if item.get("ariaLabel") else "[download]"
                logger.info(f"  Download button: {detected['download_btn']}")
                break

        if detected:
            self.selectors.update(detected)
            save_selectors(self.selectors)

        return detected

    # ── Phase 2: Execute the full flow ───────────────────────────────────

    async def generate_image(self, prompt: str, max_wait: int = 120) -> dict:
        """Full flow: type prompt → generate → wait → download."""
        self.iteration += 1
        logger.info(f"=== ITERATION {self.iteration}: Generating image ===")
        logger.info(f"Prompt: {prompt}")

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        # Step 1: Navigate if not already there
        current_url = self.server.session.browser.url
        if "image-fx" not in current_url and "imagefx" not in current_url:
            logger.info("Step 1: Navigating to ImageFX...")
            await self.server.navigate(IMAGEFX_URL)
            await asyncio.sleep(5)
        else:
            logger.info("Step 1: Already on ImageFX page")

        # Discover selectors if we don't have them
        if not self.selectors.get("prompt_input"):
            logger.info("No selectors found, running discover...")
            result = await self.discover()
            if not self.selectors.get("prompt_input"):
                return {"success": False, "error": "Could not find prompt input selector. Check output/discover/ for page info."}

        # Step 2: Type the prompt
        logger.info(f"Step 2: Typing prompt into {self.selectors['prompt_input']}...")
        type_resp = await self.server.type_text(
            self.selectors["prompt_input"], prompt, clear_first=True
        )
        if type_resp.status != ResponseStatus.SUCCESS:
            # Try clicking first to focus
            logger.warning("Direct type failed, trying click-then-type...")
            await self.server.click(self.selectors["prompt_input"])
            await asyncio.sleep(0.5)
            type_resp = await self.server.type_text(
                self.selectors["prompt_input"], prompt, clear_first=True
            )

        if type_resp.status != ResponseStatus.SUCCESS:
            return {"success": False, "error": f"Failed to type prompt: {type_resp.error}", "step": "type"}

        await asyncio.sleep(1)
        logger.info("Prompt typed successfully")

        # Take screenshot after typing
        await self._save_screenshot(f"after_type_{self.iteration}")

        # Step 3: Click generate
        if self.selectors.get("generate_btn"):
            logger.info(f"Step 3: Clicking generate button: {self.selectors['generate_btn']}...")
            gen_resp = await self.server.click(self.selectors["generate_btn"])
        else:
            # Try Enter key as fallback
            logger.info("Step 3: No generate button found, pressing Enter...")
            gen_resp = await self.server.execute_script(f"""
                const el = document.querySelector('{self.selectors["prompt_input"]}');
                if (el) {{
                    el.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Enter', code: 'Enter', bubbles: true}}));
                    el.dispatchEvent(new KeyboardEvent('keyup', {{key: 'Enter', code: 'Enter', bubbles: true}}));
                }}
                return 'enter_sent';
            """)

        await asyncio.sleep(2)
        logger.info("Generate triggered")
        await self._save_screenshot(f"after_generate_{self.iteration}")

        # Step 4: Wait for generation to complete
        logger.info(f"Step 4: Waiting for image generation (max {max_wait}s)...")
        image_ready = await self._wait_for_generation(max_wait)

        if not image_ready:
            await self._save_screenshot(f"generation_timeout_{self.iteration}")
            return {"success": False, "error": "Image generation timed out", "step": "wait"}

        logger.info("Image generation complete!")
        await self._save_screenshot(f"after_generation_{self.iteration}")

        # Step 5: Download the image
        logger.info("Step 5: Downloading image...")
        download_result = await self._download_image()

        return {
            "success": download_result.get("success", False),
            "iteration": self.iteration,
            "prompt": prompt,
            **download_result,
        }

    async def _wait_for_generation(self, max_wait: int) -> bool:
        """Poll the page until an image appears or loading stops."""
        start = time.time()
        check_interval = 3  # seconds between checks
        last_state = None

        while time.time() - start < max_wait:
            # Check for new/large images on the page
            result = await self.server.execute_script("""
                const state = { loading: false, images: 0, hasResult: false };

                // Check for loading indicators
                const loaders = document.querySelectorAll(
                    '[class*="loading"], [class*="spinner"], [class*="progress"], ' +
                    '[role="progressbar"], [class*="generating"]'
                );
                for (const l of loaders) {
                    if (l.getBoundingClientRect().width > 0) { state.loading = true; break; }
                }

                // Check for result images (large images that appeared)
                document.querySelectorAll('img, canvas').forEach(el => {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 200 && rect.height > 200) {
                        state.images++;
                        state.hasResult = true;
                    }
                });

                // Check for download button appearing
                const dl = document.querySelector('[download], [aria-label*="ownload"], [title*="ownload"]');
                if (dl && dl.getBoundingClientRect().width > 0) state.hasResult = true;

                return JSON.stringify(state);
            """)

            try:
                raw = result.data.extracted_data.get("result", "{}")
                state = json.loads(raw) if isinstance(raw, str) else raw
            except:
                state = {}

            elapsed = int(time.time() - start)
            if state != last_state:
                logger.info(f"  [{elapsed}s] Loading: {state.get('loading')}, Images: {state.get('images', 0)}, Ready: {state.get('hasResult')}")
                last_state = state

            if state.get("hasResult") and not state.get("loading"):
                return True

            await asyncio.sleep(check_interval)

        return False

    async def _download_image(self) -> dict:
        """Try to download the generated image."""

        # Strategy 1: Click download button if found
        if self.selectors.get("download_btn"):
            logger.info(f"  Trying download button: {self.selectors['download_btn']}")
            resp = await self.server.click(self.selectors["download_btn"])
            if resp.status == ResponseStatus.SUCCESS:
                await asyncio.sleep(2)
                return {"success": True, "method": "download_button"}

        # Strategy 2: Find and click any download-like button
        result = await self.server.execute_script("""
            // Look for download buttons
            const candidates = [
                ...document.querySelectorAll('[download]'),
                ...document.querySelectorAll('[aria-label*="ownload"]'),
                ...document.querySelectorAll('[title*="ownload"]'),
                ...document.querySelectorAll('button'),
            ];
            for (const el of candidates) {
                const text = (el.textContent + el.getAttribute('aria-label') + el.getAttribute('title')).toLowerCase();
                if (text.includes('download') || text.includes('save')) {
                    el.click();
                    return JSON.stringify({ clicked: true, text: el.textContent?.trim().slice(0, 40) });
                }
            }
            return JSON.stringify({ clicked: false });
        """)

        try:
            raw = result.data.extracted_data.get("result", "{}")
            dl_result = json.loads(raw) if isinstance(raw, str) else raw
        except:
            dl_result = {}

        if dl_result.get("clicked"):
            await asyncio.sleep(2)
            return {"success": True, "method": "auto_click", "detail": dl_result.get("text")}

        # Strategy 3: Extract image src and save directly
        logger.info("  No download button found, extracting image src...")
        img_result = await self.server.execute_script("""
            const images = [];
            document.querySelectorAll('img, canvas').forEach(el => {
                const rect = el.getBoundingClientRect();
                if (rect.width > 200 && rect.height > 200) {
                    if (el.tagName === 'CANVAS') {
                        try { images.push({ src: el.toDataURL('image/png'), type: 'canvas' }); } catch {}
                    } else if (el.src) {
                        images.push({ src: el.src, type: 'img' });
                    }
                }
            });
            return JSON.stringify(images);
        """)

        try:
            raw = img_result.data.extracted_data.get("result", "[]")
            images = json.loads(raw) if isinstance(raw, str) else raw
        except:
            images = []

        if images:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            for i, img in enumerate(images):
                src = img.get("src", "")
                if src.startswith("data:image"):
                    # Base64 data URL
                    b64 = src.split(",", 1)[1] if "," in src else ""
                    path = OUTPUT_DIR / f"imagefx_{ts}_{i}.png"
                    path.write_bytes(base64.b64decode(b64))
                    logger.info(f"  Image saved: {path}")
                else:
                    logger.info(f"  Image URL: {src}")
            return {"success": True, "method": "src_extract", "count": len(images)}

        # Strategy 4: Take a screenshot of the result as last resort
        logger.info("  Falling back to screenshot capture...")
        ss = await self.server.screenshot()
        if ss.data.screenshot:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = OUTPUT_DIR / f"imagefx_screenshot_{ts}.png"
            path.write_bytes(base64.b64decode(ss.data.screenshot))
            logger.info(f"  Screenshot saved: {path}")
            return {"success": True, "method": "screenshot", "path": str(path)}

        return {"success": False, "error": "Could not download image by any method"}

    async def _save_screenshot(self, label: str):
        ss = await self.server.screenshot()
        if ss.data.screenshot:
            DISCOVER_DIR.mkdir(parents=True, exist_ok=True)
            path = DISCOVER_DIR / f"{label}.png"
            path.write_bytes(base64.b64decode(ss.data.screenshot))
            logger.debug(f"Screenshot: {path}")


# ── CLI Entry Point ──────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="ImageFX Automation")
    parser.add_argument("--prompt", default="A cyberpunk city at sunset with neon lights reflecting on wet streets",
                        help="Image generation prompt")
    parser.add_argument("--discover", action="store_true", help="Just discover page selectors, don't generate")
    parser.add_argument("--iterations", type=int, default=1, help="Number of images to generate")
    parser.add_argument("--max-wait", type=int, default=120, help="Max seconds to wait for generation")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = WebSocketServer(port=args.port)
    await server.start()
    logger.info("Waiting for Chrome extension...")
    await server.wait_for_connection(timeout=120)
    logger.info("Extension connected!")

    auto = ImageFXAutomation(server)

    if args.discover:
        result = await auto.discover()
        print("\n=== DISCOVERED SELECTORS ===")
        print(json.dumps(result.get("selectors", {}), indent=2))
        print(f"\nFull data saved to {DISCOVER_DIR}/")
    else:
        for i in range(args.iterations):
            if i > 0:
                logger.info(f"--- Waiting 5s before next iteration ---")
                await asyncio.sleep(5)

            result = await auto.generate_image(args.prompt, max_wait=args.max_wait)
            print(f"\n=== RESULT (iteration {i+1}) ===")
            print(json.dumps(result, indent=2, default=str))

            if not result["success"]:
                logger.warning("Generation failed, running discover to update selectors...")
                await auto.discover()
                logger.info("Selectors updated. Retrying...")
                result = await auto.generate_image(args.prompt, max_wait=args.max_wait)
                print(f"\n=== RETRY RESULT ===")
                print(json.dumps(result, indent=2, default=str))

    await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
