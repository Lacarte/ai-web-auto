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
   - `learning-process/output/discover/`
   - `learning-process/output/learner/recipe.json`
   - `learning-process/output/execution/learned_selectors.json`
   - `created-extensions/`
5. Reuse proven selectors, recipes, and working flows when the target site or UI pattern matches.

### Phase 1: Connect and Baseline
```python
from ai_web_auto_backend.core.server import WebSocketServer
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

**ALWAYS highlight discovered elements on the page.** After every `discover` or DOM analysis that returns interactive elements with bounding rects, inject colored border overlays so the user (and screenshots) can see what was found:

```python
highlight_js = """
(function(elements) {
    document.querySelectorAll('.discover-highlight').forEach(e => e.remove());
    var colors = { input: '#00ff00', button: '#ff6600', link: '#6699ff', image: '#ff00ff', clickable: '#ffff00' };
    elements.forEach(function(el, i) {
        var div = document.createElement('div');
        div.className = 'discover-highlight';
        var c = colors[el.role] || '#ffffff';
        Object.assign(div.style, {
            position: 'fixed', zIndex: '999999',
            left: el.rect.x + 'px', top: el.rect.y + 'px',
            width: el.rect.w + 'px', height: el.rect.h + 'px',
            border: '2px solid ' + c, borderRadius: '3px',
            pointerEvents: 'none',
        });
        var label = document.createElement('span');
        Object.assign(label.style, {
            position: 'absolute', top: '-16px', left: '0',
            fontSize: '10px', color: c, fontWeight: 'bold',
            backgroundColor: 'rgba(0,0,0,0.8)', padding: '1px 4px',
            borderRadius: '2px', whiteSpace: 'nowrap',
        });
        label.textContent = i + ':' + el.role + (el.text ? '=' + el.text.slice(0,20) : '');
        div.appendChild(label);
        document.body.appendChild(div);
    });
    return 'highlighted ' + elements.length;
})(ELEMENTS_JSON);
"""
await s.execute_script(highlight_js.replace('ELEMENTS_JSON', json.dumps(elements)))
await s.screenshot()  # capture with highlights visible
```

Color key: green=input, orange=button, blue=link, magenta=image, yellow=clickable.
Take a screenshot immediately after highlighting so the visual state is captured.
Remove old highlights before injecting new ones (the script handles this automatically).

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

### Element Highlighting (mandatory)
- ALWAYS inject colored border overlays after discovering or analyzing interactive elements on a page.
- ALWAYS take a screenshot immediately after highlighting so the visual result is captured.
- Use the `discover-highlight` class for overlays so old highlights are cleared before new ones are injected.
- This applies during Phase 1 baseline, Phase 2 learning steps, and any targeted rediscovery.
- Never skip highlighting — the user must be able to see which elements were detected.

### Efficiency
- Prefer targeted rediscovery over full-page rediscovery after every step.
- Prefer polling for a condition over long fixed sleeps.
- Take screenshots at phase boundaries, before risky actions, and on failure. Do not screenshot every trivial success.
- Keep a single `debugger.attach` across an interaction sequence that depends on trusted events.
- Build the extension only after the recipe is proven. Do not debug the live site and generated extension at the same time.

### Waiting for Assets / Processing
- NEVER use fixed `setTimeout` or `asyncio.sleep` to wait for content to load or processing to finish.
- DO poll for the disappearance of loading indicators: skeleton screens, CSS animations, spinners, progress bars, and loader overlays.
- Detection strategy (poll every 200-500ms, timeout after 30s):
  1. **Skeleton screens**: `document.querySelector('[class*="skeleton"], [class*="shimmer"], [class*="placeholder"]')` — wait until gone.
  2. **CSS animations / transitions**: `document.getAnimations().length === 0` or check `el.getAnimations()` on the target container.
  3. **Spinner / loader elements**: `document.querySelector('.loader, .spinner, [class*="loading"], [aria-busy="true"]')` — wait until removed or hidden.
  4. **Opacity / visibility transitions**: poll `getComputedStyle(el).opacity !== '0'` on the expected result element.
  5. **DOM mutation**: use `MutationObserver` (wrapped in a promise with timeout) to detect when the result element is inserted.
- In generated extensions, wrap this as a reusable helper:
  ```javascript
  async function waitForReady(tabId, {gone, appear, timeout = 30000, interval = 300} = {}) {
    const start = Date.now();
    while (Date.now() - start < timeout) {
      const ready = await exec(tabId, (g, a) => {
        const loaderGone = g ? !document.querySelector(g) : true;
        const resultHere = a ? !!document.querySelector(a) : true;
        const noAnims = document.getAnimations().filter(a => a.playState === 'running').length === 0;
        return loaderGone && resultHere && noAnims;
      }, [gone || null, appear || null]);
      if (ready) return true;
      await new Promise(r => setTimeout(r, interval));
    }
    return false; // timed out
  }
  ```
- Combine signals: wait for the loader selector to disappear AND the result selector to appear AND animations to finish.
- The only acceptable fixed sleep is the initial SPA hydration wait (~3-5s) after navigation.

### Verification
- Typing: verify the entered text exists in the correct field, not just visually on the page.
- Clicking: verify a state change such as URL change, active tab state, modal open, element count change, or result text.
- Waiting: poll for the disappearance of skeletons/loaders/CSS animations AND the appearance of the expected result — never use fixed time delays.
- Downloading: prefer direct URL extraction + `chrome.downloads.download()`, then asset extraction, then screenshot as the last resort. Button `.click()` often fails on React download handlers.
- Saved recipe steps must be proven steps only. Failed attempts belong in notes, not in the final recipe.

### Typing
- USE: CDP `Input.insertText` char by char
- MUST: CDP click the input FIRST, then Ctrl+A+Backspace to clear, then type
- MUST: Replace `\n` with spaces before typing into ProseMirror/contentEditable inputs — `\n` triggers Enter which submits forms or creates blocks
- NEVER: `el.value=`, `execCommand('insertText')`, `new Function()` in content scripts
- NEVER: use `\n` as separator in appended suffixes for ProseMirror inputs
- WHY: React contentEditable only accepts CDP insertText. `execCommand` may insert visually but React state stays empty. ProseMirror treats `\n` as Enter key, causing premature form submission.

### Clicking
- USE: `findPos()` via MAIN world `executeScript` -> CDP `mousePressed+mouseReleased`
- NEVER: `el.click()` via `executeScript` for controls that require trusted events
- NEVER: hardcoded coordinates as a stored selector strategy
- WHY: Radix UI / MUI often require trusted browser events. Programmatic `.click()` is not trusted.

### Framework Click Escalation (when CDP click fails)
When CDP click and `.click()` both fail on a React/Vue/Angular element, follow this strict order:
1. **Verify first**: Use `document.elementFromPoint(x,y)` to confirm coordinates hit the right element. Check `window.devicePixelRatio`. Click a known-good element (nav link) to confirm CDP works at all.
2. **Try framework internal props**: React stores handlers under `__reactProps$xxx`, Vue under `__vue__`, Angular under `__ng__`. Walk up the parent chain until you find one with an `onClick` handler, then call it directly with a minimal fake event: `el[propsKey].onClick({preventDefault(){}, stopPropagation(){}, nativeEvent:{stopImmediatePropagation(){}}, target:el, currentTarget:el, type:'click', bubbles:true})`.
3. **If the action breaks the execution context** (all subsequent `execute_script` calls return empty): wrap everything in a **single Promise** inside one `Runtime.evaluate` call. The Promise triggers the action, polls with `setTimeout` for new elements to appear, interacts with them, then resolves. CDP's `awaitPromise:true` keeps the script alive through framework re-renders.

### React-Specific Input Filling
- NEVER set `input.value` directly — React ignores it because it tracks state internally.
- USE the native HTML prototype setter + dispatch input event:
  ```javascript
  var setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(input, 'text');
  input.dispatchEvent(new Event('input', { bubbles: true }));
  ```
- For contentEditable / ProseMirror / TipTap editors:
  ```javascript
  editor.focus();
  editor.innerHTML = '<p>Content</p>';
  editor.dispatchEvent(new Event('input', { bubbles: true }));
  ```

### Single-Promise Pattern (for framework re-render scenarios)
When triggering a UI action causes a full framework re-render that kills the CDP execution context,
do everything in one `Runtime.evaluate` call:
```javascript
new Promise(function(resolve) {
    // 1. Trigger the action (e.g., React onClick via __reactProps)
    triggerAction();
    // 2. Poll for new elements to appear
    var poll = function() {
        var el = document.querySelector('target-selector');
        if (el) {
            // 3. Interact with the new elements
            fillForm(el);
            resolve(JSON.stringify({success: true}));
        } else {
            setTimeout(poll, 200);
        }
    };
    setTimeout(poll, 500);
})
```
This pattern keeps the script alive through re-renders because CDP awaits the Promise.

### Hidden / Hover-Only Buttons (Tailwind `group-hover`, `opacity-0`, `pointer-events-none`)
- Many modern UIs hide action buttons behind CSS hover states (download, share, delete, edit overlays).
- CDP `mouseMoved` does NOT reliably trigger Tailwind `group-hover` or CSS `:hover` pseudo-classes.
- JS `el.click()` and CDP `mousePressed` both fail when `pointer-events: none` is active.
- **Strategy 1 — Force-enable via JS**: Remove `pointer-events-none` and `opacity-0` classes from the button and all ancestors, set inline `pointerEvents:'auto'` + `opacity:'1'`, then CDP click.
- **Strategy 2 — Bypass buttons entirely**: If the button triggers a download/action via a React handler that doesn't respond to programmatic clicks, skip the button. Instead, extract the underlying data (image `src` URLs, `href` values, API endpoints) and use browser APIs directly (`chrome.downloads.download()`, `fetch()`, etc).
- Strategy 2 is more reliable than Strategy 1. Prefer it when the data URL is accessible in the DOM.

### Downloading Assets
- **Prefer direct URL extraction** over clicking download buttons.
- Most AI image generators render results as `<img>` tags with CDN URLs (fbcdn.net, googleusercontent.com, cloudfront.net, etc).
- Extract URLs: `document.querySelectorAll('img')` filtered by `naturalWidth > 200`.
- Download via: `chrome.downloads.download({ url, conflictAction: 'uniquify' })`.
- React download buttons often use synthetic event handlers that don't fire from CDP/JS click.
- Only fall back to button clicking if no extractable URL exists (canvas-rendered, blob-only).

### Finding Elements
- USE: previously learned selectors first when available
- USE: XPath for stable structural targets
- USE: `[role="tab"][id$="-trigger-VALUE"]` for Radix UI tabs
- USE: `[role="option"]` matched by `textContent` for Radix Select dropdowns (IDs are dynamic)
- USE: `textContent.includes('ButtonText')` for dynamic buttons
- USE: `chrome.scripting.executeScript` with `world: "MAIN"` to bypass CSP
- USE: CDP `Runtime.evaluate` when `executeScript` with `new Function()` is blocked by strict CSP
- NEVER: `eval` or `new Function` in content scripts or in MAIN world exec on CSP-strict sites
- NEVER: fixed pixel coordinates as a persisted automation rule

### Hidden Inputs (React/contentEditable)
- Many React apps have a **visible contentEditable div** and a **hidden textarea** (width=0, height=0).
- The hidden textarea is for accessibility/form submission — DO NOT target it for typing.
- Always verify the found input has `width > 0` and `height > 0` before using it.
- Selector priority: `//div[@contenteditable="true"]` > `//textarea` > `//input`
- After finding the visible input, CDP click it to focus, then Ctrl+A+Backspace to clear, then CDP type.

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
- Select tab: CDP click on `[role="tab"][id$="-trigger-VALUE"]`
- Close: CDP Escape
- Verify: `el.getAttribute('data-state') === 'active'`

### Radix UI Select Dropdowns
- IDs are **dynamic** (`radix-_r_4f_`, etc) — never match by ID.
- Click the trigger button (shows current value text) to open.
- Options are `[role="option"]` with `data-state="checked/unchecked"` and `aria-selected`.
- Match by `textContent` — find all `[role="option"]` and compare `.textContent.trim()`.
- Verify: after selecting, the trigger button text should update to the new value.

### CSP-Strict Sites
- Sites with strict CSP block `new Function()`, `eval()`, and `execCommand`.
- `chrome.scripting.executeScript({ world: "MAIN", func })` works IF `func` is a real function reference (not a string).
- For arbitrary string code on CSP-strict sites, use CDP `Runtime.evaluate` which bypasses CSP entirely.
- Always try `executeScript` with a function ref first — it's simpler. Fall back to `Runtime.evaluate` only when needed.

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
