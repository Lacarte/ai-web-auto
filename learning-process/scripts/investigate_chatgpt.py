"""Investigate ChatGPT UI structure for automation."""
import asyncio
import json
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
        os.makedirs("output/chatgpt", exist_ok=True)
        path = f"output/chatgpt/{name}.png"
        with open(path, "wb") as f:
            f.write(base64.b64decode(img))
        print(f"  SS: {path}")


async def investigate():
    s = WebSocketServer()
    await s.start()
    await s.wait_for_connection(timeout=30)

    # Phase 1: Navigate
    print("=== Navigate to chatgpt.com ===")
    await s.navigate("https://chatgpt.com/")
    await asyncio.sleep(8)
    await save_ss(s, "phase1_landing")

    # Discover elements
    print("\n=== Discover elements ===")
    disc = await s.send_command(Command(action="discover", params={}))
    els = disc.data.extracted_data.get("elements", [])
    os.makedirs("output/chatgpt", exist_ok=True)
    with open("output/chatgpt/phase1_discover.json", "w") as f:
        json.dump(els, f, indent=2, default=str)

    print(f"Found {len(els)} elements:")
    for e in els:
        text = e.get("text", "")[:60]
        tag = e.get("tag", "?")
        aria = e.get("ariaLabel", "")
        rect = e.get("rect", {})
        ce = e.get("contentEditable", False)
        sel = e.get("selector", "")[:80]
        if text or aria or ce:
            print(f"  {tag:10s} | text={text!r:60s} | aria={aria!r} | ce={ce} | rect={rect}")

    # Find the prompt input
    print("\n=== Find prompt input ===")
    # Try contentEditable
    find_ce = await s.send_command(
        Command(action="find", params={"selector": '//div[@contenteditable="true"]'})
    )
    print(f"  contentEditable: {find_ce.data.extracted_data}")

    # Try textarea
    find_ta = await s.send_command(
        Command(action="find", params={"selector": "//textarea"})
    )
    print(f"  textarea: {find_ta.data.extracted_data}")

    # Try #prompt-textarea
    find_pt = await s.send_command(
        Command(action="find", params={"selector": "#prompt-textarea"})
    )
    print(f"  #prompt-textarea: {find_pt.data.extracted_data}")

    # Try ProseMirror (ChatGPT uses it)
    find_pm = await s.send_command(
        Command(action="find", params={"selector": ".ProseMirror"})
    )
    print(f"  .ProseMirror: {find_pm.data.extracted_data}")

    # Try to type something
    print("\n=== Type test prompt ===")
    # Find the best input
    for sel_name, sel in [
        ("contentEditable", '//div[@contenteditable="true"]'),
        ("#prompt-textarea", "#prompt-textarea"),
        (".ProseMirror", ".ProseMirror"),
        ("textarea", "//textarea"),
    ]:
        find_r = await s.send_command(Command(action="find", params={"selector": sel}))
        ed = find_r.data.extracted_data
        if ed.get("found") and ed.get("width", 0) > 0:
            print(f"  Using {sel_name} at ({ed['x']}, {ed['y']}) w={ed.get('width')} h={ed.get('height')}")
            await s.click_cdp(ed["x"], ed["y"])
            await asyncio.sleep(0.5)
            await s.type_cdp("Hello, respond with just 'test ok' and nothing else")
            await asyncio.sleep(1)
            await save_ss(s, "phase2_typed")
            break

    # Find send button
    print("\n=== Find send button ===")
    for sel_name, sel in [
        ("aria-label Send", '//button[@aria-label="Send prompt"]'),
        ("data-testid send", '[data-testid="send-button"]'),
        ("aria Send", '//button[@aria-label="Send"]'),
        ("send button", '//button[contains(@class,"send")]'),
    ]:
        find_r = await s.send_command(Command(action="find", params={"selector": sel}))
        ed = find_r.data.extracted_data
        print(f"  {sel_name}: {ed}")
        if ed.get("found"):
            print(f"    -> Clicking send at ({ed['x']}, {ed['y']})")
            await s.click_cdp(ed["x"], ed["y"])
            await asyncio.sleep(3)
            await save_ss(s, "phase3_after_send")
            break

    # Wait for response
    print("\n=== Wait for response ===")
    start = time.time()
    while time.time() - start < 30:
        elapsed = int(time.time() - start)
        # Check for streaming completion
        check_result = await s.execute_script("""
        (() => {
            // Check for the response area
            const assistantMsgs = document.querySelectorAll('[data-message-author-role="assistant"]');
            const lastMsg = assistantMsgs[assistantMsgs.length - 1];
            if (!lastMsg) return {status: 'no_response'};

            // Check if still streaming (look for stop button or cursor)
            const stopBtn = document.querySelector('[data-testid="stop-button"]');
            const isStreaming = !!stopBtn;

            const text = lastMsg.innerText.trim();
            return {
                status: isStreaming ? 'streaming' : 'done',
                text: text.substring(0, 500),
                msgCount: assistantMsgs.length,
            };
        })()
        """)
        result = check_result.data.extracted_data.get("result", {})
        print(f"  [{elapsed}s] {result}")
        if isinstance(result, dict) and result.get("status") == "done":
            print(f"  Response complete!")
            break
        await asyncio.sleep(2)

    await save_ss(s, "phase4_response")

    # Extract the full response
    print("\n=== Extract response ===")
    extract_result = await s.execute_script("""
    (() => {
        const msgs = document.querySelectorAll('[data-message-author-role="assistant"]');
        const last = msgs[msgs.length - 1];
        if (!last) return {error: 'no response'};
        return {
            text: last.innerText.trim(),
            html: last.innerHTML.substring(0, 2000),
        };
    })()
    """)
    print(f"  Response: {extract_result.data.extracted_data}")

    # Discover elements in response state for download/copy etc
    print("\n=== Discover response state ===")
    disc2 = await s.send_command(Command(action="discover", params={}))
    els2 = disc2.data.extracted_data.get("elements", [])
    with open("output/chatgpt/phase4_discover.json", "w") as f:
        json.dump(els2, f, indent=2, default=str)

    # Get page HTML structure info
    print("\n=== Page structure ===")
    struct_result = await s.execute_script("""
    (() => {
        // Find input structure
        const input = document.querySelector('#prompt-textarea, .ProseMirror, [contenteditable="true"], textarea');
        const inputInfo = input ? {
            tag: input.tagName,
            id: input.id,
            className: input.className.substring(0, 100),
            contentEditable: input.contentEditable,
            role: input.getAttribute('role'),
        } : null;

        // Find send button structure
        const sendBtn = document.querySelector('[data-testid="send-button"], button[aria-label="Send prompt"]');
        const sendInfo = sendBtn ? {
            tag: sendBtn.tagName,
            ariaLabel: sendBtn.getAttribute('aria-label'),
            testId: sendBtn.getAttribute('data-testid'),
            disabled: sendBtn.disabled,
        } : null;

        return {input: inputInfo, send: sendInfo};
    })()
    """)
    print(f"  Structure: {struct_result.data.extracted_data}")

    await s.stop()


asyncio.run(investigate())
