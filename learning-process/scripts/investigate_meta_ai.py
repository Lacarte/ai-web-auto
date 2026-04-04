"""Investigate Meta AI HTML structure for ratio selector and download buttons."""
import asyncio
import json
import base64
import os
import time

from ai_web_auto_python.core.server import WebSocketServer
from ai_web_auto_python.core.models import Command


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


async def investigate():
    s = WebSocketServer()
    await s.start()
    await s.wait_for_connection(timeout=30)

    print("=== Navigate to meta.ai ===")
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

    await save_ss(s, "investigate_create_image")

    # Get full page HTML
    print("\n=== Get page HTML ===")
    html_r = await s.extract("html")
    if html_r and html_r.data and html_r.data.extracted_data:
        html = html_r.data.extracted_data.get("html", "")
        with open("output/meta-ai/investigate_page_html.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  Saved full HTML ({len(html)} chars)")

    # Click ratio button to open dropdown
    print("\n=== Click ratio button to open dropdown ===")
    find_ratio = await s.send_command(
        Command(action="find", params={"selector": '//button[contains(.,"9:16")]'})
    )
    ed_ratio = find_ratio.data.extracted_data
    print(f"  Ratio button: {ed_ratio}")
    if ed_ratio.get("found"):
        await s.click_cdp(ed_ratio["x"], ed_ratio["y"])
        await asyncio.sleep(1)
        await save_ss(s, "investigate_ratio_open")

        # Discover while dropdown is open
        disc = await s.send_command(Command(action="discover", params={}))
        els = disc.data.extracted_data.get("elements", [])
        with open("output/meta-ai/investigate_ratio_discover.json", "w") as f:
            json.dump(els, f, indent=2, default=str)
        print(f"  Elements while dropdown open: {len(els)}")
        for e in els:
            text = e.get("text", "")[:60]
            tag = e.get("tag", "?")
            sel = e.get("selector", "")[:120]
            aria = e.get("ariaLabel", "")
            rect = e.get("rect", {})
            if text in ["1:1", "9:16", "16:9"] or "radix" in sel.lower() or "select" in sel.lower():
                print(f"    {tag:6s} | text={text!r} | sel={sel} | rect={rect} | aria={aria!r}")

        # Get HTML while dropdown is open
        html2_r = await s.extract("html")
        if html2_r and html2_r.data and html2_r.data.extracted_data:
            html2 = html2_r.data.extracted_data.get("html", "")
            with open("output/meta-ai/investigate_ratio_open_html.html", "w", encoding="utf-8") as f:
                f.write(html2)
            print(f"  Saved dropdown-open HTML ({len(html2)} chars)")

    # Close dropdown
    await s.click_cdp(700, 200)
    await asyncio.sleep(1)

    # Type prompt and send
    print("\n=== Type prompt and send ===")
    find_input = await s.send_command(
        Command(action="find", params={"selector": '//div[@contenteditable="true"]'})
    )
    ed_input = find_input.data.extracted_data
    if ed_input.get("found") and ed_input.get("width", 0) > 0:
        await s.click_cdp(ed_input["x"], ed_input["y"])
        await asyncio.sleep(0.5)
        await s.type_cdp("a cute cat wearing sunglasses")
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
            "download" in (e.get("ariaLabel", "") + e.get("text", "")).lower() for e in els
        )
        elapsed = int(time.time() - start)
        print(f"  [{elapsed}s] els={len(els)} download={has_download}")
        if has_download:
            print("  Generation complete!")
            break
        await asyncio.sleep(3)

    await save_ss(s, "investigate_generated")

    # INVESTIGATE download buttons
    print("\n=== INVESTIGATE: Download buttons ===")
    disc_final = await s.send_command(Command(action="discover", params={}))
    els_final = disc_final.data.extracted_data.get("elements", [])
    with open("output/meta-ai/investigate_download_discover.json", "w") as f:
        json.dump(els_final, f, indent=2, default=str)

    for e in els_final:
        text = e.get("text", "")[:60]
        tag = e.get("tag", "?")
        sel = e.get("selector", "")[:120]
        aria = e.get("ariaLabel", "")
        rect = e.get("rect", {})
        if any(kw in (text + aria).lower() for kw in ["download", "share", "view", "save", "copy"]):
            print(f"  {tag:8s} | text={text!r} | aria={aria!r} | rect={rect}")
            print(f"           | sel={sel}")

    # Get generated page HTML
    html3_r = await s.extract("html")
    if html3_r and html3_r.data and html3_r.data.extracted_data:
        html3 = html3_r.data.extracted_data.get("html", "")
        with open("output/meta-ai/investigate_download_html.html", "w", encoding="utf-8") as f:
            f.write(html3)
        print(f"\n  Saved generated page HTML ({len(html3)} chars)")

    # Try clicking first Download button
    print("\n=== Try clicking first Download button ===")
    dl_els = [e for e in els_final if e.get("ariaLabel", "").lower() == "download"]
    if dl_els:
        first_dl = dl_els[0]
        rect = first_dl.get("rect", {})
        cx = rect["x"] + rect["w"] // 2
        cy = rect["y"] + rect["h"] // 2
        print(f"  First download btn: rect={rect} clicking ({cx},{cy})")
        await s.click_cdp(cx, cy)
        await asyncio.sleep(3)
        await save_ss(s, "investigate_after_download_click")

        # Discover after clicking download
        disc_dl = await s.send_command(Command(action="discover", params={}))
        els_dl = disc_dl.data.extracted_data.get("elements", [])
        with open("output/meta-ai/investigate_after_download_click.json", "w") as f:
            json.dump(els_dl, f, indent=2, default=str)
        print(f"  After download click: {len(els_dl)} elements")
        for e in els_dl:
            text = e.get("text", "")[:60]
            aria = e.get("ariaLabel", "")
            tag = e.get("tag", "?")
            rect2 = e.get("rect", {})
            if any(
                kw in (text + aria).lower()
                for kw in ["download", "save", "share", "close", "dialog", "modal", "copy"]
            ):
                print(f"    {tag:8s} | text={text!r} | aria={aria!r} | rect={rect2}")

    await s.stop()


asyncio.run(investigate())
