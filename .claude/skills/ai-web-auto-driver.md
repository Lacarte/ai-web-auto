---
name: ai-web-auto-driver
description: Drive the existing AI Web-Auto platform via Python scripts to automate one-off tasks on any website — scraping data, reading DOM, posting content, filling forms. Use this when the user wants to "use AI Web-Auto to do X" rather than build a new extension.
---

# AI Web-Auto Driver — Use the existing platform for ad-hoc automation

**TRIGGER:** User says things like "use ai-web-auto to...", "via ai-web-auto read/post/scrape...", "use the extension to check if the tab is open", "post this comment on [website]", or asks to automate a one-off task without building a new extension.

**RELATED SKILL:** For *building* new standalone Chrome extensions from scratch, use `.claude/skills/web-auto.md` instead. This skill is for *driving* the already-installed AI Web-Auto platform via Python scripts from `scratch/`.

---

## What AI Web-Auto actually is

A pre-built platform consisting of:

1. **Chrome extension** at [ai-web-auto-extension/](../../ai-web-auto-extension/) — installed in user's browser, connects to Python via WebSocket (`ws://localhost:8765`, token `local-dev-token`)
2. **Python WebSocket server** at [ai_web_auto_backend/core/server.py](../../ai_web_auto_backend/core/server.py) — exposes `navigate`, `screenshot`, `execute_script`, `type_cdp`, `click_cdp`, `extract`, DOM observation
3. **Scratch scripts** at [scratch/](../../scratch/) — gitignored folder for one-off automation scripts that drive the platform

The extension is **already installed and connected** in 99% of cases. You write a Python script in `scratch/`, run it, and it drives the user's live Chrome tab.

## Quick start — minimal working script

```python
"""scratch/my_task.py"""
import asyncio, json, sys, os
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from ai_web_auto_backend.core.server import WebSocketServer

async def run_js(s, script):
    """Helper: execute JS in the page and parse JSON result."""
    resp = await s.execute_script(script)
    if resp and resp.data and resp.data.extracted_data:
        r = resp.data.extracted_data.get("result", resp.data.extracted_data)
        if isinstance(r, str):
            try: return json.loads(r)
            except: return r
        return r
    return None

async def main():
    s = WebSocketServer()
    await s.start()
    print("Waiting for Chrome extension...")
    await s.wait_for_connection(timeout=30)
    print("Connected!")

    await s.navigate("https://example.com")
    await asyncio.sleep(5)  # SPA hydration

    data = await run_js(s, "JSON.stringify({title: document.title, url: location.href})")
    print(data)

    await s.stop()

if __name__ == '__main__':
    asyncio.run(main())
```

Run with: `venv/Scripts/python scratch/my_task.py`

## Preflight (do this every time)

1. **Check if the server is already running** — port 8765 in use:
   ```bash
   netstat -aon 2>/dev/null | grep 8765
   ```
   If a PID is listening, kill it before starting your own: `taskkill //PID <PID> //F`. The user's extension stays connected and auto-reconnects to the new server.

2. **Verify the tab is what you expect** before navigating — a quick `check_tab.py` that runs a tiny `execute_script` to print `location.href` saves a lot of wasted runs.

3. **Reuse existing scratch scripts** when possible. Before writing a new one, `ls scratch/` and look for a similar pattern.

## The Python API you actually use

```python
await s.navigate(url)                         # opens/reuses tab, waits for load
await s.screenshot()                          # resp.data.screenshot = base64 PNG (with "data:..." prefix)
await s.execute_script(js_code)               # runs JS via CDP Runtime.evaluate in the target tab
await s.type_cdp(text, xpath="...")           # CDP click-to-focus + Input.insertText
await s.click_cdp(x, y)                       # CDP mousePressed/mouseReleased at viewport coords
await s.extract("text")                       # page innerText
await s.extract("html")                       # page outerHTML
```

### Saving screenshots to inspect
```python
import base64
from pathlib import Path
resp = await s.screenshot()
if resp.data.screenshot:
    img = resp.data.screenshot
    if img.startswith("data:"): img = img.split(",", 1)[1]
    Path("scratch/debug.png").write_bytes(base64.b64decode(img))
```

Then use the Read tool on the PNG to view it.

---

## ⚠️ Critical bug: "Cannot access a chrome-extension:// URL of different extension"

**This is the #1 gotcha.** It manifests as: your first `execute_script` or `click_cdp` works, then every subsequent command fails with this error. The tab visually still has the right page open, but CDP commands can't reach it.

**Root cause:** `getTargetTabId()` in [ai-web-auto-extension/js/service-worker.js](../../ai-web-auto-extension/js/service-worker.js) caches `targetTabId` from `navigate`, but after a CDP interaction the cache can be invalidated (or a popup opens), and the fallback `chrome.tabs.query({active: true})` can return a chrome-extension:// URL tab from another extension, which Chrome's debugger API refuses to attach to.

**Workarounds (prefer in this order):**

### 1. Do EVERYTHING in one `execute_script` call (most reliable)

Pack the entire flow — click, wait, fill, click again — into a single async function inside one `execute_script`. This avoids multiple tab-state round trips.

```python
script = r'''
(async function() {
    async function waitFor(fn, timeout = 6000, interval = 150) {
        const start = Date.now();
        while (Date.now() - start < timeout) {
            const r = fn();
            if (r) return r;
            await new Promise(res => setTimeout(res, interval));
        }
        return null;
    }

    // 1. Click a button to open a modal
    const openBtn = document.querySelector('button.open-modal');
    if (!openBtn) return {success: false, step: 'find_open_btn'};
    openBtn.click();

    // 2. Wait for modal input to appear
    const input = await waitFor(() => document.querySelector('input[placeholder="Title"]'));
    if (!input) return {success: false, step: 'find_input'};

    // 3. Fill using the React-compatible setter
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(input, 'Hello World');
    input.dispatchEvent(new Event('input', {bubbles: true}));
    input.dispatchEvent(new Event('change', {bubbles: true}));

    // 4. Find and click the submit button
    const submitBtn = Array.from(document.querySelectorAll('button'))
        .find(b => (b.textContent || '').trim() === 'Submit' && !b.disabled);
    if (!submitBtn) return {success: false, step: 'find_submit'};
    submitBtn.click();

    await new Promise(r => setTimeout(r, 2000));
    return {success: true, url: location.href};
})()
'''
resp = await s.execute_script(script)
```

**Key details:**
- The outer `(async function(){ ... })()` returns a Promise — CDP's `awaitPromise: true` keeps the script alive through re-renders
- Always return a plain object (it becomes the `result` field), **not** a primitive
- `waitFor` is essential: use it instead of fixed `setTimeout` for any dynamic content

### 2. If the user has XPaths, use `type_cdp(xpath=...)`
`type_cdp` uses CDP `Runtime.evaluate` internally (not `chrome.scripting.executeScript`), so it *sometimes* bypasses the tab-targeting bug. But it still calls `getTargetTabId` for the debugger attach, so it can fail the same way. Prefer approach #1.

### 3. Ask the user to reload the AI Web-Auto extension
Tell them to go to `chrome://extensions` and click the refresh icon on AI Web-Auto. That clears the broken `targetTabId` cache. Then restart your Python script.

### 4. Kill and restart the Python server
If the extension is in a bad state, sometimes `taskkill //PID <pid> //F` on the server process + a fresh run fixes it because the extension re-authenticates and refreshes internal state.

---

## React / ProseMirror input filling (always use these patterns)

React tracks input state internally — `input.value = 'x'` is silently ignored. Use the native prototype setter:

```javascript
// Standard <input>
const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
setter.call(input, text);
input.dispatchEvent(new Event('input', {bubbles: true}));
input.dispatchEvent(new Event('change', {bubbles: true}));

// contentEditable div (ProseMirror/TipTap — Skool, Notion, etc.)
editor.focus();
editor.innerHTML = '<p>Paragraph 1</p><p>Paragraph 2</p>';
editor.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: ' '}));
```

**Why both patterns:** contentEditable editors ignore `execCommand('insertText')` on most modern React apps. Setting `innerHTML` + dispatching a synthetic `InputEvent` is the only reliable way.

## Clicking in React/Vue apps

`.click()` works for most buttons that have React `onClick` handlers. It **fails** for:
- Radix UI tabs/menus (need trusted events)
- Material UI Select dropdowns with portal positioning
- Buttons that dispatch native browser events (file pickers, downloads)

**Fallback sequence** when `.click()` doesn't work inside `execute_script`:

```javascript
// Try full mouse event sequence
const rect = btn.getBoundingClientRect();
const opts = {
    bubbles: true, cancelable: true, view: window, composed: true,
    clientX: rect.x + rect.width/2, clientY: rect.y + rect.height/2,
    button: 0, buttons: 1
};
btn.dispatchEvent(new PointerEvent('pointerdown', opts));
btn.dispatchEvent(new MouseEvent('mousedown', opts));
btn.dispatchEvent(new PointerEvent('pointerup', opts));
btn.dispatchEvent(new MouseEvent('mouseup', opts));
btn.dispatchEvent(new MouseEvent('click', opts));
```

If even that fails, the button needs a real trusted event from CDP `Input.dispatchMouseEvent` — but that requires the tab-targeting to work, which may fail. In that case, **tell the user to click it manually** and explain exactly which button.

## Discovering XPaths (when the user provides them)

If selectors keep breaking and the user is willing to help, ask them to:
1. Press **F12** to open DevTools
2. Click the **element inspector** (`Ctrl+Shift+C`)
3. Click the target element
4. Right-click the highlighted Elements panel row → **Copy → Copy full XPath** (not "Copy XPath")
5. Paste in chat

Full XPaths look like `/html/body/div[1]/div/div[1]/.../input`. They're brittle but reliable for one-off tasks.

## Reading data from __NEXT_DATA__ (Next.js sites)

Most modern React sites (Skool, Vercel apps, many SaaS tools) use Next.js, which embeds the entire page state in a `<script id="__NEXT_DATA__">` JSON tag. This is gold — no DOM scraping needed.

```javascript
(function() {
    const el = document.getElementById('__NEXT_DATA__');
    if (!el) return JSON.stringify({error: 'no __NEXT_DATA__'});
    const nd = JSON.parse(el.textContent);
    const pp = nd.props?.pageProps;
    return JSON.stringify({
        pagePropsKeys: Object.keys(pp),
        yourData: pp.somePath?.subPath
    });
})()
```

**Discovery pattern:** when you don't know the shape, first return `Object.keys(pp)` to see what's available, then drill down.

## Verification before risky actions

Before clicking Submit/Post/Delete, **always verify** the form state from the DOM:

```javascript
return JSON.stringify({
    titleValue: document.querySelector('input[placeholder="Title"]').value,
    contentText: document.querySelector('[contenteditable="true"]').textContent.substring(0, 200),
    submitEnabled: !document.querySelector('button[type="submit"]').disabled
});
```

Refuse to click Submit if `titleValue` is empty, `contentText` is empty, or `submitEnabled` is false. This catches silent React-state failures.

## Getting user approval for destructive/visible actions

**Posting, commenting, sending messages, deleting data** — always present the proposed action and wait for explicit approval before executing. Pattern:

1. Read the target page and understand context (existing comments, tone, etc.)
2. Propose the content in chat
3. Say "Reply 'post it' to publish, 'rewrite: X' to refine, or 'cancel'"
4. Only run the write script after explicit approval
5. Verify success and report back

---

## Common one-off tasks and their patterns

### Read a Skool post + its comments
See [scratch/read_post_and_comments.py](../../scratch/read_post_and_comments.py) for a working example.

1. Navigate to the post URL
2. Scroll to bottom (triggers lazy-load of comments)
3. Read `pp.postTree.post` for the post body (Next.js data)
4. Scrape DOM for comments using `[class*="CommentItemBubble"]` — each bubble contains author link + content text

### Post a comment on Skool
See [scratch/post_comment.py](../../scratch/post_comment.py).
- Scroll to bottom to reveal the comment `[contenteditable]`
- `.innerHTML = ...` + dispatch `input` event
- Click the orange "COMMENT" button
- **Known issue:** the click often needs to be manual due to the tab bug — tell the user to click it after the form is filled

### Create a new post on Skool
1. Click the "Write something" div (opens modal)
2. Wait for `input[placeholder="Title"]`
3. Fill title via React setter
4. Fill body contentEditable via `innerHTML`
5. Click the "POST" button
6. Verify the modal closed (title input no longer exists in DOM)

### Download Next.js page state for offline analysis
```python
data = await run_js(s, "JSON.stringify(JSON.parse(document.getElementById('__NEXT_DATA__').textContent).props.pageProps)")
Path("scratch/pageprops.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
```

---

## Hard rules (from real session incidents)

1. **NEVER** navigate (`s.navigate`) while a modal with unsaved changes is open — Chrome will show a "Changes you made may not be saved" dialog and block everything. Ask the user to click Cancel on that dialog manually if it appears.

2. **NEVER** assume `getTargetTabId` will consistently resolve the right tab after any CDP interaction. Plan for the bug and use the single-`execute_script` pattern.

3. **NEVER** `\n` in ProseMirror/TipTap editors without explicitly using `<p>` tags — raw `\n` triggers Enter key handlers and may submit the form.

4. **NEVER** claim data you didn't verify (channel names, earnings numbers, "I tested X channels"). If the user asks you to post something with fabricated numbers, refuse and offer a rewritten version that's defensible.

5. **ALWAYS** call `await s.stop()` at the end of a script so the WebSocket server releases port 8765 cleanly.

6. **ALWAYS** save a screenshot on failure paths. A PNG shows you what really happened when the DOM state doesn't match expectations.

7. **ALWAYS** re-read the current state if there's any doubt — don't trust memory of "the modal should be open." Verify with `execute_script` first.

---

## Troubleshooting matrix

| Symptom | Likely cause | Fix |
|---|---|---|
| `OSError: error while attempting to bind on address ('::1', 8765...` | Another Python server already running | `netstat -aon \| grep 8765` + `taskkill //PID <pid> //F` |
| `Cannot access a chrome-extension:// URL of different extension` | Tab-targeting bug after CDP interaction | Use single `execute_script` for whole flow; or ask user to reload extension |
| `execute_script` returns `None` / status `success` but no data | JS threw silently, or wrong tab targeted | Wrap JS in `try/catch` and return the error; check `resp.error` explicitly |
| Title/content appears typed visually but form submit is disabled | React state not updated — `.value=` was used instead of prototype setter | Use `Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(...)` |
| contentEditable shows the text but `textContent` stays empty in verification | Editor re-rendered and cleared your HTML | Re-focus and re-set `innerHTML` immediately before reading; or use the React internal instance |
| Modal opens but XPath queries return null | Script running in wrong tab (chrome-extension URL) | Log `location.href` first thing in the script; if it's wrong, kill server and restart |
| Comments/lazy content missing from scrape | Not scrolled to trigger lazy load | `window.scrollTo(0, document.body.scrollHeight)` + `await sleep(3000)` before scraping |

---

## File locations (don't guess)

- **This skill:** `.claude/skills/ai-web-auto-driver.md`
- **Extension builder skill (different):** `.claude/skills/web-auto.md`
- **Python server API:** [ai_web_auto_backend/core/server.py](../../ai_web_auto_backend/core/server.py)
- **Extension service worker (where the tab bug lives):** [ai-web-auto-extension/js/service-worker.js](../../ai-web-auto-extension/js/service-worker.js)
- **Models (command/response types):** [ai_web_auto_backend/core/models.py](../../ai_web_auto_backend/core/models.py)
- **Working example scripts:** `scratch/` (gitignored)
- **Project rules and critical technical details:** [CLAUDE.md](../../CLAUDE.md) — especially the "Critical Technical Rules" section
