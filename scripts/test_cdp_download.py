"""Test CDP Runtime.evaluate to force-enable downloads on Meta AI."""
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

    # Navigate
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
        await s.type_cdp("a cozy cabin in snowy woods at night")
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
        if elapsed % 5 == 0:
            print(f"  [{elapsed}s] els={len(els)} download={has_download}")
        if has_download:
            print("  Generation complete!")
            break
        await asyncio.sleep(2)

    await save_ss(s, "test_cdp_generated")

    # TEST: Use execute_script (now via CDP Runtime.evaluate) to force-enable buttons
    print("\n=== Force-enable download buttons via CDP Runtime.evaluate ===")
    force_js = """
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
    """
    result = await s.execute_script(force_js)
    print(f"  execute_script result: {result.status.value}")
    if result.data and result.data.extracted_data:
        print(f"  Data: {result.data.extracted_data}")

    await asyncio.sleep(0.5)
    await save_ss(s, "test_cdp_buttons_forced")

    # Now discover to get updated download button positions
    disc2 = await s.send_command(Command(action="discover", params={}))
    els2 = disc2.data.extracted_data.get("elements", [])
    dl_buttons = [e for e in els2 if e.get("ariaLabel") == "Download"]
    print(f"  Download buttons found: {len(dl_buttons)}")

    # Click each download button
    for i, dl in enumerate(dl_buttons):
        rect = dl["rect"]
        x = rect["x"] + rect["w"] // 2
        y = rect["y"] + rect["h"] // 2
        print(f"\n  Clicking download {i+1} at ({x}, {y})")
        await s.click_cdp(x, y)
        await asyncio.sleep(2)
        await save_ss(s, f"test_cdp_download_{i+1}")

    print("\n=== DONE ===")
    await s.stop()


asyncio.run(test())
