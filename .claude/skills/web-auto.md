---
name: web-auto
description: Build standalone Chrome extensions that automate any website using CDP-first techniques learned from real-world testing.
---

# Web Automation Extension Builder

TRIGGER: User asks to automate a website, build a browser extension, scrape a site, or interact with a web UI.

## Process: Autonomous Learn → Build → Test → Iterate

### Phase 1: Connect & Discover
```python
from python.core.server import WebSocketServer
s = WebSocketServer()  # port 8765, token "local-dev-token"
await s.start()
await s.wait_for_connection(timeout=60)
await s.navigate(url)
await asyncio.sleep(8)  # SPA hydration
ss = await s.screenshot()  # see what's there
r = await s.execute_script('return JSON.stringify({...})')  # MAIN world DOM read
```

### Phase 2: Learn Each Step
For each step: find element → highlight → try interaction → screenshot → verify → save if success.

### Phase 3: Build Standalone Extension
Output folder with: manifest.json, js/background.js, html/popup.html, css/popup.css, js/popup.js

### Phase 4: Test & Fix
Load extension → run → screenshot each step → fix failures → iterate.

## RULES (NEVER violate)

### Typing
- **USE**: CDP `Input.insertText` char by char
- **MUST**: CDP click the input FIRST, then Ctrl+A+Backspace to clear, then type
- **NEVER**: `el.value=`, `execCommand('insertText')`, `new Function()` in content scripts
- **WHY**: React contentEditable only accepts CDP insertText. execCommand inserts visually but React state stays empty.

### Clicking
- **USE**: `findPos()` via MAIN world executeScript → CDP `mousePressed+mouseReleased`
- **NEVER**: `el.click()` via executeScript (not trusted, Radix UI tabs won't switch)
- **NEVER**: Hardcoded coordinates (break on different screens)
- **WHY**: Radix UI / MUI require trusted browser events. Programmatic .click() is not trusted.

### Finding Elements
- **USE**: XPath for stable structural targets
- **USE**: `[role="tab"][id$="-trigger-VALUE"]` for Radix UI tabs
- **USE**: `textContent.includes('ButtonText')` for dynamic buttons
- **USE**: `chrome.scripting.executeScript` with `world: "MAIN"` to bypass CSP
- **NEVER**: `eval`/`new Function` in content scripts (CSP blocks them)
- **NEVER**: Fixed pixel coordinates

### Extension Architecture
- Service worker = brain (CDP + MAIN world exec)
- Content script = data extraction ONLY (text, HTML, cookies)
- `all_frames: false` in manifest (avoids reCAPTCHA iframes)
- Single `debugger.attach` at start, single `detach` at end
- `chrome.alarms.create('keepalive', {periodInMinutes: 0.25})` — MV3 kills SW after 30s
- Permissions: debugger, scripting, alarms, downloads, storage, tabs, activeTab

### Radix UI Settings Panels
- Open: CDP click on trigger (`aria-haspopup="menu"` or XPath)
- Select: CDP click on `[role="tab"][id$="-trigger-VALUE"]`
- Close: CDP Escape
- Verify: `el.getAttribute('data-state') === 'active'`

## Template: background.js

```javascript
async function cdpClick(tabId, x, y) {
  await cdp(tabId, "Input.dispatchMouseEvent", {type:"mousePressed", x, y, button:"left", clickCount:1});
  await cdp(tabId, "Input.dispatchMouseEvent", {type:"mouseReleased", x, y, button:"left", clickCount:1});
}

async function cdpType(tabId, text) {
  for (const ch of text) await cdp(tabId, "Input.insertText", {text: ch});
}

async function findPos(tabId, sel) {
  return exec(tabId, (s) => {
    let el = s.startsWith('/')
      ? document.evaluate(s, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue
      : document.querySelector(s);
    if (el) { const r = el.getBoundingClientRect(); return {found:true, x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)}; }
    return {found:false};
  }, [sel]);
}

async function exec(tabId, func, args) {
  const r = await chrome.scripting.executeScript({target:{tabId}, world:"MAIN", func, args});
  return r?.[0]?.result;
}
```

## Python Server API
```python
await s.navigate(url)                    # create tab + wait load
await s.screenshot()                     # ss.data.screenshot = base64
await s.execute_script('return ...')     # MAIN world JS
await s.type_cdp("text", xpath="...")    # CDP typing
await s.click_cdp(x, y)                 # CDP click (coords from findPos only)
await s.extract("text")                 # page text
await s.extract("html")                 # page HTML
```

## Reference: flow-extension/
Complete working example automating Google Labs Flow with all techniques applied.
