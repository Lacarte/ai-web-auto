"""Debug what happens when clicking Meta AI download button."""
import asyncio
import base64
import os
import time

from ai_web_auto_backend.core.server import WebSocketServer
from ai_web_auto_backend.core.models import Command


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

    # Click Create image
    find_r = await s.send_command(
        Command(action="find", params={"selector": '//button[contains(.,"Create image")]'})
    )
    ed = find_r.data.extracted_data
    if ed.get("found"):
        await s.click_cdp(ed["x"], ed["y"])
        await asyncio.sleep(2)

    # Type and send
    find_input = await s.send_command(
        Command(action="find", params={"selector": '//div[@contenteditable="true"]'})
    )
    ed_input = find_input.data.extracted_data
    if ed_input.get("found") and ed_input.get("width", 0) > 0:
        await s.click_cdp(ed_input["x"], ed_input["y"])
        await asyncio.sleep(0.5)
        await s.type_cdp("a red sports car on a mountain road")
        await asyncio.sleep(1)

    find_send = await s.send_command(
        Command(action="find", params={"selector": '//button[@aria-label="Send"]'})
    )
    ed_send = find_send.data.extracted_data
    if ed_send.get("found"):
        await s.click_cdp(ed_send["x"], ed_send["y"])
        await asyncio.sleep(3)

    # Wait for generation
    print("=== Wait for generation ===")
    start = time.time()
    while time.time() - start < 60:
        disc = await s.send_command(Command(action="discover", params={}))
        els = disc.data.extracted_data.get("elements", [])
        has_download = any("download" in e.get("ariaLabel", "").lower() for e in els)
        elapsed = int(time.time() - start)
        if has_download:
            print(f"  [{elapsed}s] Generation complete!")
            break
        if elapsed % 5 == 0:
            print(f"  [{elapsed}s] waiting...")
        await asyncio.sleep(2)

    await save_ss(s, "debug_generated")

    # Force-enable buttons
    print("\n=== Force-enable download buttons ===")
    force_result = await s.execute_script("""
    (() => {
        const buttons = document.querySelectorAll('button[aria-label="Download"]');
        let count = 0;
        for (const btn of buttons) {
            let el = btn;
            while (el && el !== document.body) {
                el.classList.remove('pointer-events-none', 'opacity-0');
                el.style.pointerEvents = 'auto';
                el.style.opacity = '1';
                el = el.parentElement;
            }
            btn.style.pointerEvents = 'auto';
            btn.style.opacity = '1';
            count++;
        }
        return count;
    })()
    """)
    print(f"  Force-enabled: {force_result.data.extracted_data}")
    await asyncio.sleep(0.5)
    await save_ss(s, "debug_buttons_visible")

    # Click ONLY the first download button and watch carefully
    print("\n=== Click first download button ===")
    disc2 = await s.send_command(Command(action="discover", params={}))
    els2 = disc2.data.extracted_data.get("elements", [])
    dl_buttons = [e for e in els2 if e.get("ariaLabel") == "Download"]
    print(f"  Found {len(dl_buttons)} download buttons")

    if dl_buttons:
        rect = dl_buttons[0]["rect"]
        x = rect["x"] + rect["w"] // 2
        y = rect["y"] + rect["h"] // 2
        print(f"  Clicking at ({x}, {y})")
        await s.click_cdp(x, y)
        await asyncio.sleep(1)
        await save_ss(s, "debug_after_dl_click_1s")

        # Check if any modal/dialog appeared
        disc3 = await s.send_command(Command(action="discover", params={}))
        els3 = disc3.data.extracted_data.get("elements", [])
        new_els = []
        old_texts = {e.get("ariaLabel", "") + e.get("text", "") for e in els2}
        for e in els3:
            key = e.get("ariaLabel", "") + e.get("text", "")
            if key and key not in old_texts:
                new_els.append(e)
        if new_els:
            print(f"  NEW elements after click ({len(new_els)}):")
            for e in new_els:
                print(f"    {e['tag']:8s} | text={e.get('text','')[:50]!r} | aria={e.get('ariaLabel','')!r} | rect={e.get('rect',{})}")
        else:
            print("  No new elements appeared after click")

        await asyncio.sleep(3)
        await save_ss(s, "debug_after_dl_click_4s")

        # Check again
        disc4 = await s.send_command(Command(action="discover", params={}))
        els4 = disc4.data.extracted_data.get("elements", [])
        print(f"  Elements: before={len(els2)}, after_1s={len(els3)}, after_4s={len(els4)}")

    # Try alternative: click View media first, then look for download in modal
    print("\n=== Try View media -> download ===")
    view_buttons = [e for e in els2 if e.get("ariaLabel") == "View media"]
    if view_buttons:
        v = view_buttons[0]["rect"]
        vx = v["x"] + v["w"] // 2
        vy = v["y"] + v["h"] // 2
        print(f"  Clicking View media at ({vx}, {vy})")
        await s.click_cdp(vx, vy)
        await asyncio.sleep(2)
        await save_ss(s, "debug_view_media")

        disc5 = await s.send_command(Command(action="discover", params={}))
        els5 = disc5.data.extracted_data.get("elements", [])
        print(f"  Elements in view: {len(els5)}")
        for e in els5:
            text = e.get("text", "")[:50]
            aria = e.get("ariaLabel", "")
            tag = e.get("tag", "?")
            if any(kw in (text + aria).lower() for kw in ["download", "save", "close", "back", "next", "prev", "share", "copy"]):
                print(f"    {tag:8s} | text={text!r} | aria={aria!r} | rect={e.get('rect',{})}")

    # Try extracting image URLs directly
    print("\n=== Extract image URLs ===")
    urls_result = await s.execute_script("""
    (() => {
        const urls = [];
        // Check all img tags
        for (const img of document.querySelectorAll('img')) {
            if (img.naturalWidth > 100) urls.push({type: 'img', src: img.src.substring(0, 200), w: img.naturalWidth, h: img.naturalHeight});
        }
        // Check canvas elements
        for (const c of document.querySelectorAll('canvas')) {
            if (c.width > 100) urls.push({type: 'canvas', w: c.width, h: c.height});
        }
        // Check background images
        for (const el of document.querySelectorAll('.group\\/media-item *')) {
            const bg = getComputedStyle(el).backgroundImage;
            if (bg && bg !== 'none') urls.push({type: 'bg', bg: bg.substring(0, 200)});
        }
        // Check video/source
        for (const v of document.querySelectorAll('video, source')) {
            urls.push({type: v.tagName, src: (v.src || v.currentSrc || '').substring(0, 200)});
        }
        return urls;
    })()
    """)
    print(f"  Image sources: {urls_result.data.extracted_data}")

    await s.stop()


asyncio.run(test())
