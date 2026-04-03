"""Test JS .click() on download buttons instead of CDP click."""
import asyncio
import base64
import os
import time

from python.core.server import WebSocketServer
from python.core.models import Command


async def save_ss(s, name):
    ss = await s.screenshot()
    if ss and ss.data and ss.data.screenshot:
        img = ss.data.screenshot
        if "," in img:
            img = img.split(",", 1)[1]
        os.makedirs("output/meta-ai", exist_ok=True)
        path = f"output/meta-ai/{name}.png"
        with open(path, "wb") as f:
            f.write(base64.b64decode(img))
        print(f"  SS: {path}")


async def test():
    s = WebSocketServer()
    await s.start()
    await s.wait_for_connection(timeout=30)

    print("=== Navigate ===")
    await s.navigate("https://www.meta.ai/")
    await asyncio.sleep(8)

    # Click Create image, type, send
    find_r = await s.send_command(
        Command(action="find", params={"selector": '//button[contains(.,"Create image")]'})
    )
    if find_r.data.extracted_data.get("found"):
        await s.click_cdp(find_r.data.extracted_data["x"], find_r.data.extracted_data["y"])
        await asyncio.sleep(2)

    find_input = await s.send_command(
        Command(action="find", params={"selector": '//div[@contenteditable="true"]'})
    )
    ei = find_input.data.extracted_data
    if ei.get("found") and ei.get("width", 0) > 0:
        await s.click_cdp(ei["x"], ei["y"])
        await asyncio.sleep(0.5)
        await s.type_cdp("a neon lit tokyo street at night")
        await asyncio.sleep(1)

    find_send = await s.send_command(
        Command(action="find", params={"selector": '//button[@aria-label="Send"]'})
    )
    if find_send.data.extracted_data.get("found"):
        await s.click_cdp(find_send.data.extracted_data["x"], find_send.data.extracted_data["y"])
        await asyncio.sleep(3)

    # Wait for generation
    print("=== Wait for generation ===")
    start = time.time()
    while time.time() - start < 60:
        disc = await s.send_command(Command(action="discover", params={}))
        els = disc.data.extracted_data.get("elements", [])
        has_download = any("download" in e.get("ariaLabel", "").lower() for e in els)
        if has_download:
            print(f"  [{int(time.time()-start)}s] Generation complete!")
            break
        await asyncio.sleep(2)

    # Wait extra for images to fully render
    await asyncio.sleep(5)
    await save_ss(s, "jsclick_generated")

    # Force-enable buttons first
    print("\n=== Force-enable ===")
    await s.execute_script("""
    (() => {
        const buttons = document.querySelectorAll('button[aria-label="Download"]');
        for (const btn of buttons) {
            let el = btn;
            while (el && el !== document.body) {
                el.classList.remove('pointer-events-none', 'opacity-0');
                el.style.pointerEvents = 'auto';
                el.style.opacity = '1';
                el = el.parentElement;
            }
        }
        return buttons.length;
    })()
    """)

    # Method 1: Try JS .click() on download buttons via Runtime.evaluate
    print("\n=== Method 1: JS .click() on first download button ===")
    click_result = await s.execute_script("""
    (() => {
        const btn = document.querySelector('button[aria-label="Download"]');
        if (!btn) return {error: 'no button found'};
        btn.click();
        return {clicked: true, text: btn.textContent, tag: btn.tagName};
    })()
    """)
    print(f"  Result: {click_result.data.extracted_data}")
    await asyncio.sleep(3)
    await save_ss(s, "jsclick_after_method1")

    # Method 2: Dispatch custom pointer events
    print("\n=== Method 2: dispatchEvent on second download button ===")
    dispatch_result = await s.execute_script("""
    (() => {
        const btns = document.querySelectorAll('button[aria-label="Download"]');
        if (btns.length < 2) return {error: 'not enough buttons'};
        const btn = btns[1];
        const rect = btn.getBoundingClientRect();
        const cx = rect.x + rect.width/2;
        const cy = rect.y + rect.height/2;
        // Simulate full pointer event sequence
        ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(type => {
            btn.dispatchEvent(new PointerEvent(type, {
                bubbles: true, cancelable: true, composed: true,
                clientX: cx, clientY: cy, button: 0, isTrusted: false
            }));
        });
        return {dispatched: true};
    })()
    """)
    print(f"  Result: {dispatch_result.data.extracted_data}")
    await asyncio.sleep(3)
    await save_ss(s, "jsclick_after_method2")

    # Method 3: Find image src URLs and trigger download via anchor tag
    print("\n=== Method 3: Extract image URLs and trigger <a> download ===")
    extract_result = await s.execute_script("""
    (() => {
        // Find all images in the media containers
        const containers = document.querySelectorAll('[class*="group/media-item"]');
        const results = [];
        for (const c of containers) {
            // Look for img elements
            const imgs = c.querySelectorAll('img');
            for (const img of imgs) {
                if (img.src && img.naturalWidth > 50) {
                    results.push({src: img.src, w: img.naturalWidth, h: img.naturalHeight});
                }
            }
            // Look for div with background-image
            for (const el of c.querySelectorAll('*')) {
                const bg = getComputedStyle(el).backgroundImage;
                if (bg && bg !== 'none' && bg.startsWith('url(')) {
                    results.push({bg: bg.slice(5, -2)});
                }
            }
        }
        // Also check all page imgs
        const allImgs = [];
        for (const img of document.querySelectorAll('img')) {
            if (img.naturalWidth > 200) {
                allImgs.push({src: img.src.substring(0, 150), w: img.naturalWidth, h: img.naturalHeight});
            }
        }
        return {containers: containers.length, mediaResults: results.length, allLargeImgs: allImgs};
    })()
    """)
    print(f"  Result: {extract_result.data.extracted_data}")

    # Method 4: Check if clicking View media opens a full-res version
    print("\n=== Method 4: Click View media and check for full image ===")
    view_result = await s.execute_script("""
    (() => {
        const viewBtn = document.querySelector('button[aria-label="View media"]');
        if (!viewBtn) return {error: 'no view media button'};
        viewBtn.click();
        return {clicked: true};
    })()
    """)
    print(f"  View media click: {view_result.data.extracted_data}")
    await asyncio.sleep(2)
    await save_ss(s, "jsclick_view_media")

    # Discover in modal
    disc_modal = await s.send_command(Command(action="discover", params={}))
    els_modal = disc_modal.data.extracted_data.get("elements", [])
    print(f"  Elements: {len(els_modal)}")
    for e in els_modal:
        aria = e.get("ariaLabel", "")
        text = e.get("text", "")[:40]
        tag = e.get("tag", "?")
        rect = e.get("rect", {})
        if any(kw in (aria + text).lower() for kw in ["download", "save", "close", "back", "next", "share"]):
            print(f"    {tag:8s} | aria={aria!r} | text={text!r} | rect={rect}")

    # Check for full-size image in modal
    img_check = await s.execute_script("""
    (() => {
        const imgs = [];
        for (const img of document.querySelectorAll('img')) {
            const r = img.getBoundingClientRect();
            if (r.width > 300 && r.height > 300) {
                imgs.push({src: img.src.substring(0, 150), w: r.width, h: r.height, natW: img.naturalWidth, natH: img.naturalHeight});
            }
        }
        return imgs;
    })()
    """)
    print(f"  Large images: {img_check.data.extracted_data}")

    await s.stop()


asyncio.run(test())
