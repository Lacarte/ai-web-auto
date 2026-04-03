# AI Web-Auto Platform

## What This Is
A CDP-first browser automation platform that learns how to interact with any website and produces standalone Chrome extensions.

## Project Structure
```
chrome-extension/          # AI Web-Auto — the learning/discovery extension
created-extensions/        # Standalone extensions produced by the platform
python/                    # WebSocket server + AI agent + utilities
scripts/                   # Test, discovery, and learner scripts
output/                    # Screenshots, element data, recipes
.claude/skills/web-auto.md # Skill file — teaches Claude how to build automations
```

## Quick Start

### 1. Install dependencies
```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Load the extension
- Open `chrome://extensions` → Developer mode ON
- Click "Load unpacked" → select `chrome-extension/`
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
- `navigate <url>` — Open a URL in the browser
- `screenshot` — Take a screenshot (saved to output/)
- `discover` — List all interactive elements on the page
- `type <text>` — Type into focused input via CDP
- `click <selector>` — Click element by CSS selector or XPath
- `extract` — Extract visible page text
- `task <objective>` — Run AI-driven automation task
- `quit` — Exit

## Building a New Automation Extension

### Option A: Ask Claude (recommended)
Tell Claude: "automate [website] to do [task]"
Claude uses the `web-auto` skill to:
1. Connect to AI Web-Auto extension
2. Navigate to the site
3. Discover elements (screenshot + DOM analysis)
4. Test interactions (CDP type, CDP click)
5. Build a standalone extension in `created-extensions/`

### Option B: Use the learner script
```
venv\Scripts\python scripts/learner.py --url "https://example.com" --prompt "test input"
```
This runs the self-learning loop: screenshot → discover → interact → verify → save recipe.

### Option C: Manual discovery
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

## Critical Technical Rules

### Typing: CDP `Input.insertText` ONLY
```
✓ await s.type_cdp("text", xpath="/html/.../div")
✗ el.value = "text"           — React ignores it
✗ execCommand('insertText')   — React state stays empty
✗ new Function() in content   — CSP blocks it
```
**Always CDP click the input FIRST before Ctrl+A to clear.** Otherwise Ctrl+A selects the entire page.

### Clicking: CDP trusted mouse events ONLY
```
✓ findPos() → cdpClick(x, y)  — trusted event, Radix UI tabs switch
✗ el.click() via executeScript — not trusted, tabs don't switch
✗ Hardcoded coordinates        — break on different screens
```

### Finding elements: MAIN world `executeScript`
```
✓ chrome.scripting.executeScript({ world: "MAIN" })  — bypasses CSP
✓ XPath for stable elements
✓ [role="tab"][id$="-trigger-PORTRAIT"] for Radix UI
✓ textContent.includes("Create") for dynamic buttons
✗ new Function() in content scripts — CSP blocks
✗ document.querySelector in content script eval — CSP blocks
```

### Extension architecture
```
✓ Service worker = brain (CDP + MAIN world)
✓ Content script = data extraction only
✓ Single debugger.attach for entire sequence
✓ chrome.alarms keepalive every 15s
✓ all_frames: false (avoids reCAPTCHA iframes)
✗ Attach/detach between clicks (panels close)
✗ all_frames: true (captures wrong frames)
```

## Key Files

| File | Purpose |
|------|---------|
| `chrome-extension/js/service-worker.js` | CDP engine + WS client + command router |
| `chrome-extension/js/content/content-main.js` | Data extraction + fallback only |
| `python/core/server.py` | WebSocket server with CDP/type commands |
| `python/core/models.py` | Pydantic models for command protocol |
| `python/automation_controller.py` | BrowserAgent high-level API |
| `scripts/learner.py` | Self-learning automation loop |
| `.claude/skills/web-auto.md` | Skill for building new extensions |

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Extension won't connect | Reload it in chrome://extensions, wait for keepalive alarm (15s) |
| `ERR_CONNECTION_REFUSED` | Start the Python server first (`runner.bat`) |
| `Auth denied` | Token mismatch — both sides use `local-dev-token` by default |
| `execute_script` returns empty | CSP blocking — use service worker MAIN world, not content script |
| Ctrl+A selects entire page | CDP click the input field FIRST to focus it before clearing |
| Radix UI tabs don't switch | Use CDP click (trusted event), not `.click()` |
| Service worker dies | chrome.alarms keepalive should prevent this; reload extension |
| Port 8765 in use | Kill old process: `netstat -aon | grep 8765` then `taskkill /PID <pid> /F` |
