"""Test JS force-enable + CDP click download on Meta AI."""
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

    # Navigate and generate an image
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
        await s.type_cdp("a serene mountain lake at sunrise with mist")
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

    await save_ss(s, "test_force_generated")

    # Force-enable download buttons via discover + exec workaround
    # Since execute_script hits CSP on meta.ai, we need a different approach.
    # The discover command uses exec() which works. But we need a custom command.
    # Let's use hover command with a workaround: hover triggers cdpAttach,
    # then we can use Runtime.evaluate via CDP which bypasses CSP.
    print("\n=== Force-enable download buttons ===")

    # First hover somewhere to ensure debugger is attached
    await s.send_command(Command(action="hover", params={"x": 400, "y": 400}))

    # Now use a creative approach: the discover function runs exec() in MAIN world
    # which passes a real function (not eval). But we can't run arbitrary code.
    # Let's check if we can use the click_cdp flow.

    # Alternative: use CDP Runtime.evaluate directly through the extension
    # Since the debugger is now attached after hover, we can evaluate JS directly

    # Actually, the simplest approach: just click each "View media" button to open
    # the image in a modal, then look for download there.
    # OR: Force the buttons via the extension's exec() by adding a custom command.

    # For now, let's try clicking the View media button to open the image viewer
    # which should have a more accessible download option
    print("\n=== Try View Media approach ===")
    disc_final = await s.send_command(Command(action="discover", params={}))
    els_final = disc_final.data.extracted_data.get("elements", [])

    view_buttons = [e for e in els_final if e.get("ariaLabel") == "View media"]
    dl_buttons = [e for e in els_final if e.get("ariaLabel") == "Download"]
    print(f"  View media: {len(view_buttons)}, Download: {len(dl_buttons)}")

    if view_buttons:
        # Click first View media button
        v = view_buttons[0]["rect"]
        vx = v["x"] + v["w"] // 2
        vy = v["y"] + v["h"] // 2
        print(f"  Clicking View media at ({vx}, {vy})")
        await s.click_cdp(vx, vy)
        await asyncio.sleep(2)
        await save_ss(s, "test_view_media_open")

        # Discover in the modal view
        disc_modal = await s.send_command(Command(action="discover", params={}))
        els_modal = disc_modal.data.extracted_data.get("elements", [])
        print(f"  Modal elements: {len(els_modal)}")
        for e in els_modal:
            text = e.get("text", "")[:50]
            aria = e.get("ariaLabel", "")
            tag = e.get("tag", "?")
            rect = e.get("rect", {})
            if text or aria:
                print(f"    {tag:8s} | text={text!r:50s} | aria={aria!r}")

    await s.stop()


asyncio.run(test())
