"""Test CDP hover + click download on Meta AI generated images."""
import asyncio
import json
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

    # Click Create image
    find_r = await s.send_command(
        Command(action="find", params={"selector": '//button[contains(.,"Create image")]'})
    )
    ed = find_r.data.extracted_data
    if ed.get("found"):
        await s.click_cdp(ed["x"], ed["y"])
        await asyncio.sleep(2)

    # Type and send
    print("=== Type and send ===")
    find_input = await s.send_command(
        Command(action="find", params={"selector": '//div[@contenteditable="true"]'})
    )
    ed_input = find_input.data.extracted_data
    if ed_input.get("found") and ed_input.get("width", 0) > 0:
        await s.click_cdp(ed_input["x"], ed_input["y"])
        await asyncio.sleep(0.5)
        await s.type_cdp("a majestic lion portrait in golden hour light")
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
        print(f"  [{elapsed}s] els={len(els)} download={has_download}")
        if has_download:
            print("  Generation complete!")
            break
        await asyncio.sleep(3)

    await save_ss(s, "test_hover_generated")

    # Get all View media (image containers) and Download buttons
    disc_final = await s.send_command(Command(action="discover", params={}))
    els_final = disc_final.data.extracted_data.get("elements", [])

    view_buttons = [e for e in els_final if e.get("ariaLabel") == "View media"]
    dl_buttons = [e for e in els_final if e.get("ariaLabel") == "Download"]

    print(f"\n=== HOVER + DOWNLOAD TEST ===")
    print(f"  View media containers: {len(view_buttons)}")
    print(f"  Download buttons: {len(dl_buttons)}")

    for i, (view, dl) in enumerate(zip(view_buttons, dl_buttons)):
        v_rect = view["rect"]
        d_rect = dl["rect"]
        hover_x = v_rect["x"] + v_rect["w"] // 2
        hover_y = v_rect["y"] + v_rect["h"] // 2
        dl_x = d_rect["x"] + d_rect["w"] // 2
        dl_y = d_rect["y"] + d_rect["h"] // 2

        print(f"\n  Image {i+1}:")
        print(f"    Step 1: CDP hover at ({hover_x}, {hover_y})")

        # CDP hover over image container
        hover_r = await s.send_command(
            Command(action="hover", params={"x": hover_x, "y": hover_y, "delay_after": 500})
        )
        print(f"    Hover result: {hover_r.status.value} {hover_r.data.extracted_data}")

        # Take screenshot to see if buttons became visible
        await save_ss(s, f"test_hover_image_{i+1}")

        print(f"    Step 2: CDP click download at ({dl_x}, {dl_y})")
        await s.click_cdp(dl_x, dl_y)
        await asyncio.sleep(2)
        await save_ss(s, f"test_hover_download_{i+1}")

    print("\n=== DONE ===")
    await s.stop()


asyncio.run(test())
