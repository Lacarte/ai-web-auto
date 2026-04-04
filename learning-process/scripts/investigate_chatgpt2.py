# -*- coding: utf-8 -*-
"""Investigate ChatGPT UI with visual element highlighting."""
import asyncio
import json
import base64
import os
import time
import sys

# Fix Windows console encoding
sys.stdout.reconfigure(encoding='utf-8')

from ai_web_auto_python.core.server import WebSocketServer
from ai_web_auto_python.core.models import Command


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


HIGHLIGHT_JS = """
(() => {
    // Remove old highlights
    document.querySelectorAll('.debug-highlight').forEach(e => e.remove());

    const selectors = 'a, button, input, select, textarea, [role="button"], [role="tab"], [role="textbox"], [contenteditable="true"], [data-testid]';
    const colors = {
        'button': '#ff4444', 'a': '#44ff44', 'input': '#4444ff',
        'textarea': '#ff44ff', 'select': '#ffff44', 'div': '#44ffff',
    };
    let count = 0;
    for (const el of document.querySelectorAll(selectors)) {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) continue;
        if (rect.y < -10 || rect.x < -10) continue;
        if (count++ > 40) break;

        const tag = el.tagName.toLowerCase();
        const color = colors[tag] || '#ff8800';
        const label = [
            tag,
            el.id ? '#' + el.id : '',
            el.getAttribute('aria-label') ? 'aria=' + el.getAttribute('aria-label') : '',
            el.getAttribute('data-testid') ? 'tid=' + el.getAttribute('data-testid') : '',
            el.getAttribute('role') ? 'role=' + el.getAttribute('role') : '',
            el.contentEditable === 'true' ? 'CE' : '',
            el.getAttribute('placeholder') || '',
        ].filter(Boolean).join(' ').substring(0, 60);

        // Border
        el.style.outline = `2px solid ${color}`;
        el.style.outlineOffset = '-1px';

        // Label
        const lbl = document.createElement('div');
        lbl.className = 'debug-highlight';
        lbl.style.cssText = `position:fixed;left:${Math.max(0,rect.x)}px;top:${Math.max(0,rect.y-14)}px;background:${color};color:#000;font:bold 9px monospace;padding:1px 3px;z-index:99999;white-space:nowrap;pointer-events:none;opacity:0.9;`;
        lbl.textContent = label;
        document.body.appendChild(lbl);
    }
    return count;
})()
"""

CLEAR_HIGHLIGHTS_JS = """
(() => {
    document.querySelectorAll('.debug-highlight').forEach(e => e.remove());
    for (const el of document.querySelectorAll('*')) {
        if (el.style.outline) el.style.outline = '';
        if (el.style.outlineOffset) el.style.outlineOffset = '';
    }
})()
"""


async def investigate():
    s = WebSocketServer()
    await s.start()
    await s.wait_for_connection(timeout=30)

    # Navigate
    print("=== Navigate to chatgpt.com ===")
    await s.navigate("https://chatgpt.com/")
    await asyncio.sleep(8)
    await save_ss(s, "phase1_clean")

    # Highlight all elements
    print("\n=== Highlight elements ===")
    hl_result = await s.execute_script(HIGHLIGHT_JS)
    print(f"  Highlighted: {hl_result.data.extracted_data}")
    await save_ss(s, "phase1_highlighted")

    # Clear highlights
    await s.execute_script(CLEAR_HIGHLIGHTS_JS)

    # Find the input
    print("\n=== Find prompt input ===")
    input_result = await s.execute_script("""
    (() => {
        const candidates = [
            {sel: '#prompt-textarea', name: '#prompt-textarea'},
            {sel: '.ProseMirror', name: '.ProseMirror'},
            {sel: '[contenteditable="true"]', name: 'contentEditable'},
            {sel: 'textarea', name: 'textarea'},
            {sel: '[data-testid="composer-input"]', name: 'composer-input'},
        ];
        const results = [];
        for (const c of candidates) {
            const el = document.querySelector(c.sel);
            if (el) {
                const r = el.getBoundingClientRect();
                results.push({
                    name: c.name,
                    tag: el.tagName,
                    id: el.id,
                    className: el.className.substring(0, 80),
                    contentEditable: el.contentEditable,
                    role: el.getAttribute('role'),
                    placeholder: el.getAttribute('placeholder') || el.dataset?.placeholder || '',
                    rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)},
                });
            }
        }
        return results;
    })()
    """)
    print(f"  Input candidates: {json.dumps(input_result.data.extracted_data.get('result', []), indent=2)}")

    # Find send button
    print("\n=== Find send button ===")
    send_result = await s.execute_script("""
    (() => {
        const candidates = [
            '[data-testid="send-button"]',
            'button[aria-label="Send prompt"]',
            'button[aria-label="Send"]',
        ];
        for (const sel of candidates) {
            const el = document.querySelector(sel);
            if (el) {
                const r = el.getBoundingClientRect();
                return {
                    sel,
                    tag: el.tagName,
                    ariaLabel: el.getAttribute('aria-label'),
                    testId: el.getAttribute('data-testid'),
                    disabled: el.disabled,
                    rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)},
                };
            }
        }
        return {error: 'no send button found'};
    })()
    """)
    print(f"  Send button: {send_result.data.extracted_data}")

    # Highlight just the input and send button
    print("\n=== Highlight input + send only ===")
    await s.execute_script("""
    (() => {
        const input = document.querySelector('#prompt-textarea, .ProseMirror, [contenteditable="true"]');
        const send = document.querySelector('[data-testid="send-button"], button[aria-label="Send prompt"]');
        if (input) {
            input.style.outline = '3px solid #00ff00';
            const r = input.getBoundingClientRect();
            const lbl = document.createElement('div');
            lbl.className = 'debug-highlight';
            lbl.style.cssText = `position:fixed;left:${r.x}px;top:${r.y-16}px;background:#00ff00;color:#000;font:bold 11px monospace;padding:2px 4px;z-index:99999;`;
            lbl.textContent = 'INPUT: ' + input.tagName + (input.id ? '#'+input.id : '') + ' CE=' + input.contentEditable;
            document.body.appendChild(lbl);
        }
        if (send) {
            send.style.outline = '3px solid #ff0000';
            const r = send.getBoundingClientRect();
            const lbl = document.createElement('div');
            lbl.className = 'debug-highlight';
            lbl.style.cssText = `position:fixed;left:${r.x}px;top:${r.y-16}px;background:#ff0000;color:#fff;font:bold 11px monospace;padding:2px 4px;z-index:99999;`;
            lbl.textContent = 'SEND: ' + (send.getAttribute('data-testid') || send.getAttribute('aria-label') || 'button');
            document.body.appendChild(lbl);
        }
    })()
    """)
    await save_ss(s, "phase1_input_send_highlighted")
    await s.execute_script(CLEAR_HIGHLIGHTS_JS)

    # Type a test prompt
    print("\n=== Type test prompt ===")
    find_input = await s.send_command(
        Command(action="find", params={"selector": "#prompt-textarea"})
    )
    ed = find_input.data.extracted_data
    if not ed.get("found") or ed.get("width", 0) == 0:
        find_input = await s.send_command(
            Command(action="find", params={"selector": '//div[@contenteditable="true"]'})
        )
        ed = find_input.data.extracted_data

    print(f"  Input: {ed}")
    if ed.get("found") and ed.get("width", 0) > 0:
        await s.click_cdp(ed["x"], ed["y"])
        await asyncio.sleep(0.5)
        await s.type_cdp('respond with only: {"status":"ok","message":"test"} and nothing else, no markdown')
        await asyncio.sleep(1)
        await save_ss(s, "phase2_typed")

    # Find and click send
    print("\n=== Click send ===")
    find_send = await s.send_command(
        Command(action="find", params={"selector": '[data-testid="send-button"]'})
    )
    ed_send = find_send.data.extracted_data
    if not ed_send.get("found"):
        find_send = await s.send_command(
            Command(action="find", params={"selector": '//button[@aria-label="Send prompt"]'})
        )
        ed_send = find_send.data.extracted_data

    print(f"  Send: {ed_send}")
    if ed_send.get("found"):
        await s.click_cdp(ed_send["x"], ed_send["y"])
        await asyncio.sleep(3)
        await save_ss(s, "phase3_sent")

    # Poll for response completion
    print("\n=== Wait for response ===")
    start = time.time()
    while time.time() - start < 45:
        elapsed = int(time.time() - start)
        check = await s.execute_script("""
        (() => {
            const msgs = document.querySelectorAll('[data-message-author-role="assistant"]');
            const last = msgs[msgs.length - 1];
            if (!last) return {status: 'no_response', count: 0};
            const stopBtn = document.querySelector('[data-testid="stop-button"], button[aria-label="Stop generating"]');
            const text = last.innerText.trim();
            return {
                status: stopBtn ? 'streaming' : 'done',
                text: text.substring(0, 300),
                count: msgs.length,
            };
        })()
        """)
        result = check.data.extracted_data.get("result", {})
        status = result.get("status", "?") if isinstance(result, dict) else "?"
        text = (result.get("text", "")[:80] if isinstance(result, dict) else "")
        print(f"  [{elapsed}s] status={status} text={text!r}")

        if status == "done" and isinstance(result, dict) and result.get("text"):
            print("  Response complete!")
            break
        await asyncio.sleep(2)

    await save_ss(s, "phase4_response")

    # Highlight the response area
    await s.execute_script("""
    (() => {
        const msgs = document.querySelectorAll('[data-message-author-role="assistant"]');
        const last = msgs[msgs.length - 1];
        if (last) {
            last.style.outline = '3px solid #ffff00';
            const r = last.getBoundingClientRect();
            const lbl = document.createElement('div');
            lbl.className = 'debug-highlight';
            lbl.style.cssText = `position:fixed;left:${r.x}px;top:${r.y-16}px;background:#ffff00;color:#000;font:bold 11px monospace;padding:2px 4px;z-index:99999;`;
            lbl.textContent = 'RESPONSE: [data-message-author-role="assistant"]';
            document.body.appendChild(lbl);
        }
    })()
    """)
    await save_ss(s, "phase4_response_highlighted")

    # Extract response text
    print("\n=== Extract response ===")
    extract = await s.execute_script("""
    (() => {
        const msgs = document.querySelectorAll('[data-message-author-role="assistant"]');
        const last = msgs[msgs.length - 1];
        if (!last) return {error: 'no response'};
        return {text: last.innerText.trim(), html: last.innerHTML.substring(0, 1000)};
    })()
    """)
    print(f"  Response: {extract.data.extracted_data}")

    await s.stop()


asyncio.run(investigate())
