# AI Web-Auto Platform

## What This Is
A CDP-first browser automation platform that learns how to interact with any website and produces standalone Chrome extensions.

## Project Structure
```
chrome-extension/          # AI Web-Auto - the learning/discovery extension
created-extensions/        # Standalone extensions produced by the platform
python/                    # WebSocket server + AI agent + utilities
scripts/                   # Test, discovery, learner, and execution scripts
output/                    # Screenshots, element data, recipes, learned selectors
.claude/skills/web-auto.md # Skill file - teaches Claude how to build automations
```

## Quick Start

### 1. Install dependencies
```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Load the extension
- Open `chrome://extensions` -> Developer mode ON
- Click "Load unpacked" -> select `chrome-extension/`
- The extension auto-connects to `ws://localhost:8765` with token `local-dev-token`

### 3. Run the server
```
runner.bat
```
Or directly:
```
venv\Scripts\python -m python.automation_controller
```

### 4. Use commands
At the `>` prompt:
- `navigate <url>` - Open a URL in the browser
- `screenshot` - Take a screenshot (saved to `output/`)
- `discover` - List all interactive elements on the page
- `type <text>` - Type into focused input via CDP
- `click <selector>` - Click element by CSS selector or XPath
- `extract` - Extract visible page text
- `task <objective>` - Run AI-driven automation task
- `quit` - Exit

## Building a New Automation Extension

### Option A: Ask Claude (recommended)
Tell Claude: "automate [website] to do [task]"

Claude uses the `web-auto` skill to:
1. Run preflight checks: confirm server/extension connection, blockers, and whether prior selectors or recipes already exist.
2. Reuse `output/discover/`, `output/learner/recipe.json`, `output/execution/learned_selectors.json`, and similar `created-extensions/` work before rediscovering.
3. Navigate to the deepest useful page and capture a minimal baseline with a screenshot and targeted DOM analysis.
4. Learn the smallest proven recipe: action -> verify -> save, with targeted retries before full rediscovery.
5. Build a standalone extension in `created-extensions/` from confirmed steps only.
6. Cold-start test the generated extension and iterate only on failed steps.

### Option B: Use the learner script
```
venv\Scripts\python scripts/learner.py --url "https://example.com" --prompt "test input"
```
This runs the self-learning loop: screenshot -> discover -> interact -> verify -> save recipe.

### Option C: Use the execution runner
```
venv\Scripts\python scripts/runner-execution.py --list scripts\execution-list.txt
```
This runs a step list with screenshots, verification, selector learning, and self-correction.

### Option D: Manual discovery
```python
from python.core.server import WebSocketServer
s = WebSocketServer()
await s.start()
await s.wait_for_connection(timeout=60)

await s.navigate("https://example.com")
await s.screenshot()                           # see the page
await s.execute_script("return JSON.stringify({...})")  # read DOM (MAIN world)
await s.type_cdp("hello", xpath="/html/.../input")      # type via CDP
await s.click_cdp(x, y)                                 # click via CDP
```

## Recommended Claude Workflow

1. Reuse before rediscovery
- Check prior selectors, recipes, and generated extensions for the same site or UI pattern.
- Prefer refining a known-good flow over starting from scratch.

2. Prove the recipe first
- Start with the minimum happy path.
- Save only steps that have a clear verification signal.

3. Build from confirmed steps
- Centralize selectors and helper functions in the generated extension.
- Keep fallback selectors grouped in one place.

4. Test from a clean state
- Re-run from a fresh tab.
- Fix brittle selectors or waits, not the whole flow.

## Critical Technical Rules

### Efficiency and Reliability
```
DO reuse learned selectors and recipes before rediscovering the whole page
DO prefer targeted rediscovery and polling over long fixed sleeps
DO require a success condition for every saved step
DO persist only stable selectors; treat coordinates as one-off fallbacks
DON'T rediscover the full page after every action
DON'T repeat the same failed selector without changing strategy
DON'T build the standalone extension before the live recipe is proven
```

### Typing: CDP `Input.insertText` ONLY
```
DO: await s.type_cdp("text", xpath="/html/.../div")
DON'T: el.value = "text"         # React ignores it
DON'T: execCommand('insertText') # React state stays empty
DON'T: new Function() in content # CSP blocks it
```
Always CDP click the input first before Ctrl+A to clear. Otherwise Ctrl+A selects the entire page.

### Clicking: CDP trusted mouse events ONLY
```
DO: findPos() -> cdpClick(x, y)   # trusted event, Radix UI tabs switch
DON'T: el.click() via executeScript
DON'T: hardcoded coordinates
```

### Finding elements: MAIN world `executeScript`
```
DO: chrome.scripting.executeScript({ world: "MAIN" })  # bypasses CSP
DO: reuse prior working selectors first
DO: XPath for stable elements
DO: [role="tab"][id$="-trigger-PORTRAIT"] for Radix UI
DO: textContent.includes("Create") for dynamic buttons
DON'T: new Function() in content scripts
DON'T: document.querySelector in content script eval
```

### Extension architecture
```
DO: Service worker = brain (CDP + MAIN world)
DO: Content script = data extraction only
DO: Single debugger.attach for entire sequence
DO: chrome.alarms keepalive every 15s
DO: centralized selector map + shared CDP helpers
DO: all_frames: false
DON'T: attach/detach between clicks
DON'T: all_frames: true
```

### Verification rules
```
DO: verify typed text exists in the intended input
DO: verify a click changed state, not just that the click returned
DO: poll for ready signals such as active tabs, result images, or download buttons
DO: save only proven steps to recipes and generated extensions
DON'T: assume success because the UI looked right once
```

## Key Files

| File | Purpose |
|------|---------|
| `chrome-extension/js/service-worker.js` | CDP engine + WS client + command router |
| `chrome-extension/js/content/content-main.js` | Data extraction + fallback only |
| `python/core/server.py` | WebSocket server with CDP/type commands |
| `python/core/models.py` | Pydantic models for command protocol |
| `python/automation_controller.py` | BrowserAgent high-level API |
| `python/ai/prompts.py` | Claude action prompt rules for the AI agent |
| `scripts/learner.py` | Self-learning automation loop |
| `scripts/runner-execution.py` | Self-correcting step runner with learned selectors |
| `output/execution/learned_selectors.json` | Persisted working selectors from prior runs |
| `.claude/skills/web-auto.md` | Skill for building new extensions |

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Extension won't connect | Reload it in `chrome://extensions`, wait for keepalive alarm (15s) |
| `ERR_CONNECTION_REFUSED` | Start the Python server first (`runner.bat`) |
| `Auth denied` | Token mismatch - both sides use `local-dev-token` by default |
| `execute_script` returns empty | CSP blocking - use service worker MAIN world, not content script |
| Ctrl+A selects entire page | CDP click the input field first to focus it before clearing |
| Radix UI tabs don't switch | Use CDP click (trusted event), not `.click()` |
| Service worker dies | `chrome.alarms` keepalive should prevent this; reload the extension |
| Port 8765 in use | Kill the old process: `netstat -aon | findstr 8765` then `taskkill /PID <pid> /F` |
