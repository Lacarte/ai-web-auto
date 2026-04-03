---
name: web-auto
description: Build standalone Chrome extensions that automate any website using CDP-first techniques learned from real-world testing.
---

# Web Automation Extension Builder

TRIGGER: User asks to automate a website, build a browser extension, scrape a site, or interact with a web UI.

## Process: Preflight -> Reuse -> Learn -> Build -> Test

### Phase 0: Preflight
1. Confirm the Python server and Chrome learning extension are connected.
2. Confirm the site is reachable and note blockers such as login, CAPTCHA, region gates, modal walls, or permission prompts.
3. Open the deepest useful page for the task, not just the homepage.
4. Check for reusable artifacts before rediscovering:
   - `output/discover/`
   - `output/learner/recipe.json`
   - `output/execution/learned_selectors.json`
   - `created-extensions/`
5. Reuse proven selectors, recipes, and working flows when the target site or UI pattern matches.

### Phase 1: Connect and Baseline
```python
from python.core.server import WebSocketServer
s = WebSocketServer()  # port 8765, token "local-dev-token"
await s.start()
await s.wait_for_connection(timeout=60)
await s.navigate(url)
await asyncio.sleep(8)  # SPA hydration
ss = await s.screenshot()  # baseline visual state
r = await s.execute_script('return JSON.stringify({...})')  # MAIN world DOM read
```

Collect only the minimum baseline needed to move forward:
- 1 screenshot
- current URL/title
- visible interactive elements
- page text or HTML only when needed for selector discovery or verification

### Phase 2: Learn the Smallest Proven Recipe
Break the task into explicit steps with a success condition for each one.

For each step:
1. Reuse the best known selector first.
2. Try the interaction.
3. Verify with an observable signal.
4. Save the step only if verified.
5. Retry once with a different selector strategy if needed.
6. Rediscover only the affected region or page state if both attempts fail.

Preferred selector order:
1. Previously learned selector for the same role
2. Stable CSS by `id`, `name`, `aria-label`, or placeholder
3. Semantic role selectors such as Radix/MUI tabs
4. Text-anchored matching for dynamic buttons
5. Stable XPath for structural targets
6. Coordinate click only as a temporary fallback, never as a saved selector

### Phase 3: Build the Standalone Extension
Output folder with: `manifest.json`, `js/background.js`, `html/popup.html`, `css/popup.css`, `js/popup.js`

Build from confirmed steps, not guesses. Centralize selectors and helper functions so the generated extension can be corrected quickly.

### Phase 4: Cold-Start Test and Fix
Load the generated extension, run the full flow from a fresh tab, and only fix failed steps.

Validate:
- happy-path run from a clean state
- retry path for brittle selectors
- async wait and poll behavior
- download and result extraction path

## RULES (NEVER violate)

### Workflow
- Reuse existing recipes and selectors before running full discovery again.
- Prove the flow in the learning extension first, then build the standalone extension.
- Start with the minimum happy path. Add optional settings only after the base flow works.
- Every action must have an expected outcome and a concrete verification step.
- If the same selector fails twice, change strategy instead of repeating it.
- Stop and ask for human help on login, CAPTCHA, MFA, payment, or consent walls.
- Persist stable selectors and learned patterns so Claude does not rediscover the same UI every run.

### Efficiency
- Prefer targeted rediscovery over full-page rediscovery after every step.
- Prefer polling for a condition over long fixed sleeps.
- Take screenshots at phase boundaries, before risky actions, and on failure. Do not screenshot every trivial success.
- Keep a single `debugger.attach` across an interaction sequence that depends on trusted events.
- Build the extension only after the recipe is proven. Do not debug the live site and generated extension at the same time.

### Verification
- Typing: verify the entered text exists in the correct field, not just visually on the page.
- Clicking: verify a state change such as URL change, active tab state, modal open, element count change, or result text.
- Waiting: poll for a concrete ready signal such as result images, active state, or a download button.
- Downloading: prefer real download controls first, then asset extraction, then screenshot as the last resort.
- Saved recipe steps must be proven steps only. Failed attempts belong in notes, not in the final recipe.

### Typing
- USE: CDP `Input.insertText` char by char
- MUST: CDP click the input FIRST, then Ctrl+A+Backspace to clear, then type
- NEVER: `el.value=`, `execCommand('insertText')`, `new Function()` in content scripts
- WHY: React contentEditable only accepts CDP insertText. `execCommand` may insert visually but React state stays empty.

### Clicking
- USE: `findPos()` via MAIN world `executeScript` -> CDP `mousePressed+mouseReleased`
- NEVER: `el.click()` via `executeScript` for controls that require trusted events
- NEVER: hardcoded coordinates as a stored selector strategy
- WHY: Radix UI / MUI often require trusted browser events. Programmatic `.click()` is not trusted.

### Finding Elements
- USE: previously learned selectors first when available
- USE: XPath for stable structural targets
- USE: `[role="tab"][id$="-trigger-VALUE"]` for Radix UI tabs
- USE: `textContent.includes('ButtonText')` for dynamic buttons
- USE: `chrome.scripting.executeScript` with `world: "MAIN"` to bypass CSP
- NEVER: `eval` or `new Function` in content scripts
- NEVER: fixed pixel coordinates as a persisted automation rule

### Extension Architecture
- Service worker = brain (CDP + MAIN world exec)
- Content script = data extraction ONLY (text, HTML, cookies)
- `all_frames: false` in manifest (avoids reCAPTCHA iframes)
- Single `debugger.attach` at start, single `detach` at end
- `chrome.alarms.create('keepalive', {periodInMinutes: 0.25})` because MV3 kills the service worker after about 30s
- Permissions: debugger, scripting, alarms, downloads, storage, tabs, activeTab
- Keep selectors in one map and keep CDP helpers in one place
- Prefer arrays of fallback selectors over duplicated retry code

### Radix UI Settings Panels
- Open: CDP click on trigger (`aria-haspopup="menu"` or XPath)
- Select: CDP click on `[role="tab"][id$="-trigger-VALUE"]`
- Close: CDP Escape
- Verify: `el.getAttribute('data-state') === 'active'`

## Template: background.js

```javascript
async function cdpClick(tabId, x, y) {
  await cdp(tabId, "Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: 1 });
  await cdp(tabId, "Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: 1 });
}

async function cdpType(tabId, text) {
  for (const ch of text) await cdp(tabId, "Input.insertText", { text: ch });
}

async function findPos(tabId, sel) {
  return exec(tabId, (s) => {
    let el = s.startsWith("/")
      ? document.evaluate(s, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue
      : document.querySelector(s);
    if (el) {
      const r = el.getBoundingClientRect();
      return { found: true, x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
    }
    return { found: false };
  }, [sel]);
}

async function exec(tabId, func, args) {
  const r = await chrome.scripting.executeScript({ target: { tabId }, world: "MAIN", func, args });
  return r?.[0]?.result;
}
```

## Python Server API
```python
await s.navigate(url)                    # create tab + wait load
await s.screenshot()                     # ss.data.screenshot = base64
await s.execute_script('return ...')     # MAIN world JS
await s.type_cdp("text", xpath="...")    # CDP typing
await s.click_cdp(x, y)                  # CDP click (coords from findPos only)
await s.extract("text")                  # page text
await s.extract("html")                  # page HTML
```

## Reference: flow-extension/
Complete working example automating Google Labs Flow with all techniques applied.
