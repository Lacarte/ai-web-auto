"""Test the fixed ratio selection and hover-download on Meta AI."""
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
    print("=== Click Create image ===")
    find_r = await s.send_command(
        Command(action="find", params={"selector": '//button[contains(.,"Create image")]'})
    )
    ed = find_r.data.extracted_data
    if ed.get("found"):
        await s.click_cdp(ed["x"], ed["y"])
        await asyncio.sleep(2)

    # TEST 1: Ratio selection — switch to 16:9, then back to 9:16
    print("\n=== TEST: Select 16:9 ratio ===")
    # Click ratio trigger
    find_ratio = await s.send_command(
        Command(
            action="find",
            params={
                "selector": '//button[contains(.,"9:16") or contains(.,"1:1") or contains(.,"16:9")]'
            },
        )
    )
    ed_ratio = find_ratio.data.extracted_data
    print(f"  Trigger: {ed_ratio}")

    if ed_ratio.get("found"):
        await s.click_cdp(ed_ratio["x"], ed_ratio["y"])
        await asyncio.sleep(1)
        await save_ss(s, "test_ratio_dropdown_open")

        # Find 16:9 option by role="option"
        # Use discover to see the options
        disc = await s.send_command(Command(action="discover", params={}))
        els = disc.data.extracted_data.get("elements", [])
        for e in els:
            if e.get("text", "") in ["1:1", "9:16", "16:9"]:
                print(f"  Option: text={e['text']!r} tag={e['tag']} rect={e['rect']}")

        # Click 16:9 using find with a CSS selector for role="option"
        # We need to use exec via the server to find it
        # Since execute_script hits CSP, use the find command with XPath
        # But find doesn't support text matching on role="option"...
        # The discover data showed us the positions. Let's click 16:9 by its rect.
        opt_16_9 = [
            e for e in els if e.get("text", "").strip() == "16:9"
        ]
        if opt_16_9:
            rect = opt_16_9[0]["rect"]
            cx = rect["x"] + rect["w"] // 2
            cy = rect["y"] + rect["h"] // 2
            print(f"  Clicking 16:9 at ({cx}, {cy})")
            await s.click_cdp(cx, cy)
            await asyncio.sleep(1)
            await save_ss(s, "test_ratio_16_9_selected")

            # Verify
            disc2 = await s.send_command(Command(action="discover", params={}))
            els2 = disc2.data.extracted_data.get("elements", [])
            for e in els2:
                if e.get("text", "").strip() in ["1:1", "9:16", "16:9"]:
                    print(f"  After select: text={e['text']!r} (button)")

    # Switch back to 9:16
    print("\n=== Select 9:16 ratio ===")
    find_ratio2 = await s.send_command(
        Command(
            action="find",
            params={
                "selector": '//button[contains(.,"9:16") or contains(.,"1:1") or contains(.,"16:9")]'
            },
        )
    )
    ed_ratio2 = find_ratio2.data.extracted_data
    if ed_ratio2.get("found"):
        await s.click_cdp(ed_ratio2["x"], ed_ratio2["y"])
        await asyncio.sleep(1)

        disc3 = await s.send_command(Command(action="discover", params={}))
        els3 = disc3.data.extracted_data.get("elements", [])
        opt_9_16 = [e for e in els3 if e.get("text", "").strip() == "9:16"]
        if opt_9_16:
            rect = opt_9_16[0]["rect"]
            cx = rect["x"] + rect["w"] // 2
            cy = rect["y"] + rect["h"] // 2
            await s.click_cdp(cx, cy)
            await asyncio.sleep(1)
            await save_ss(s, "test_ratio_9_16_selected")
            print("  9:16 selected")

    # Type prompt and generate
    print("\n=== Type and send ===")
    find_input = await s.send_command(
        Command(action="find", params={"selector": '//div[@contenteditable="true"]'})
    )
    ed_input = find_input.data.extracted_data
    if ed_input.get("found") and ed_input.get("width", 0) > 0:
        await s.click_cdp(ed_input["x"], ed_input["y"])
        await asyncio.sleep(0.5)
        await s.type_cdp("a playful golden retriever puppy in a field of sunflowers")
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
        has_download = any(
            "download" in e.get("ariaLabel", "").lower() for e in els
        )
        elapsed = int(time.time() - start)
        print(f"  [{elapsed}s] els={len(els)} download={has_download}")
        if has_download:
            print("  Generation complete!")
            break
        await asyncio.sleep(3)

    await save_ss(s, "test_generated")

    # TEST 2: Hover + Download
    print("\n=== TEST: Hover + Download ===")
    disc_final = await s.send_command(Command(action="discover", params={}))
    els_final = disc_final.data.extracted_data.get("elements", [])

    # Find all Download buttons and their parent View media buttons (same container)
    dl_buttons = [e for e in els_final if e.get("ariaLabel") == "Download"]
    view_buttons = [e for e in els_final if e.get("ariaLabel") == "View media"]

    print(f"  Download buttons: {len(dl_buttons)}")
    print(f"  View media (image containers): {len(view_buttons)}")

    for i, (view, dl) in enumerate(zip(view_buttons, dl_buttons)):
        v_rect = view["rect"]
        d_rect = dl["rect"]
        # Hover center of the image container
        hover_x = v_rect["x"] + v_rect["w"] // 2
        hover_y = v_rect["y"] + v_rect["h"] // 2
        # Download button center
        dl_x = d_rect["x"] + d_rect["w"] // 2
        dl_y = d_rect["y"] + d_rect["h"] // 2

        print(f"\n  Image {i+1}: hover ({hover_x},{hover_y}) -> dl ({dl_x},{dl_y})")

        # Step 1: CDP hover over image container
        await s.send_command(
            Command(
                action="click_cdp",
                params={"x": hover_x, "y": hover_y},
            )
        )
        # Actually we need mouseMoved, not click. Use click_cdp coordinates
        # but we don't have a hover command. Let's just test the click on dl directly
        # after hovering via clicking the view_media area first...
        # Actually the learning extension doesn't have a hover command.
        # The standalone extension will use cdpHover. For now, test with a click
        # on the image area first, then click download.
        await asyncio.sleep(0.5)

        # Step 2: Click download button
        await s.click_cdp(dl_x, dl_y)
        await asyncio.sleep(2)
        await save_ss(s, f"test_download_{i+1}")
        print(f"  Clicked download {i+1}")

    print("\n=== DONE ===")
    await s.stop()


asyncio.run(test())
