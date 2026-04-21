# AI Web-Auto Platform

## What This Is
A CDP-first browser automation platform that learns how to interact with any website and produces standalone Chrome extensions.

## Project Structure
```
ai-web-auto-extension/     # AI Web-Auto - the learning/discovery Chrome extension
ai_web_auto_backend/        # WebSocket server + AI agent + utilities
created-extensions/        # Standalone extensions produced by the platform
  skool-down-course/
    skool-down-course-extension/  # Skool course downloader Chrome extension
    skool-down-course-backend/    # Python backend + downloader scripts
  chatgpt-ui-to-api/
  meta-ai-extension/
  flow-extension/
learning-process/              # Learning process artifacts
  scripts/                     # Test, discovery, learner, and execution scripts
  output/                      # Screenshots, element data, recipes, learned selectors
skool-output/              # Downloaded Skool course files (gitignored)
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
- Click "Load unpacked" -> select `ai-web-auto-extension/`
- The extension auto-connects to `ws://localhost:8765` with token `local-dev-token`

### 3. Run the server
```
runner.bat
```
Or directly:
```
venv\Scripts\python -m ai_web_auto_backend.automation_controller
```

### 4. Use commands
At the `>` prompt:
- `navigate <url>` - Open a URL in the browser
- `screenshot` - Take a screenshot (saved to `learning-process/output/`)
- `discover` - List all interactive elements on the page
- `type <text>` - Type into focused input via CDP
- `click <selector>` - Click element by CSS selector or XPath
- `extract` - Extract visible page text
- `observe [selector]` - Start DOM observation (default: body) — takes initial snapshot
- `diff` - Show structured DOM changes since last snapshot (adds/removes/attr/text with reasons)
- `unobserve` - Stop DOM observation
- `task <objective>` - Run AI-driven automation task
- `quit` - Exit

## Skool Course Downloader

### Usage
```
cd created-extensions\skool-down-course\skool-down-course-backend
download-runner.bat https://www.skool.com/ai-video-creators/classroom
```

### How it works
1. Connects to AI Web-Auto via WebSocket (same tab navigation)
2. Scans `__NEXT_DATA__` for modules and lessons, skips locked courses
3. For each lesson: captures HTML (inline CSS, active links), screenshot, and video
4. Videos: Skool native (click play -> mux-player -> M3U8 -> ffmpeg), YouTube/Loom/Vimeo (yt-dlp)
5. Output organized as `skool-output/{CourseName}/{Module}/{Lesson}/`

### Proven selectors (verified on live sites)
```
Classroom page:   __NEXT_DATA__.props.pageProps.allCourses (modules)
                  Locked: !hasAccess && minAccessLevel != null
Module page:      __NEXT_DATA__.props.pageProps.course.children (lessons)
                  Flat or nested structure (both handled)
Lesson page:      mux-player[playback-id] (Skool native video)
                  iframe[src*="loom.com|youtube.com|vimeo.com"] (external)
                  [class*="VideoDuration"] -> parent (click target for play)
                  [class*="ResourceWrapper"] (PDF/attachment elements)
Mark complete:    button > svg[viewBox="0 0 40 40"] (done)
                  button > svg[viewBox="0 0 24 24"] with >=2 paths (not done)
```

### Tested communities
- `ai-video-creators` - Skool native videos (Mux M3U8)
- `the-ai-blueprint` - Loom embeds + PDF resources
- `learn-ai` - Mixed Skool native + YouTube

## Skills available to Claude for this project

Two skills live in `.claude/skills/` and handle different use cases:

| Skill | File | When to use |
|---|---|---|
| **ai-web-auto-driver** | `.claude/skills/ai-web-auto-driver.md` | **Drive** the existing platform for one-off tasks: scrape data, read DOM, post a comment, fill a form. Pick this when the user says "use ai-web-auto to...", "via the extension...", or asks for an ad-hoc automation that doesn't need a new standalone extension. Contains all lessons learned about the tab-targeting bug, React input filling, ProseMirror editors, and the single-`execute_script` pattern. |
| **web-auto** | `.claude/skills/web-auto.md` | **Build** a new standalone Chrome extension from scratch. Pick this when the user says "build an extension to automate X", "create a new extension for Y", or wants a reusable packaged automation output. |

Both skills load automatically based on the user's phrasing. If unsure which to use, read the TRIGGER section of each file — `ai-web-auto-driver` handles the "use the platform" case, `web-auto` handles the "build something new" case.

## Building a New Automation Extension

### Option A: Ask Claude (recommended)
Tell Claude: "automate [website] to do [task]"

Claude uses the `web-auto` skill to:
1. Run preflight checks: confirm server/extension connection, blockers, and whether prior selectors or recipes already exist.
2. Reuse `learning-process/output/discover/`, `learning-process/output/learner/recipe.json`, `learning-process/output/execution/learned_selectors.json`, and similar `created-extensions/` work before rediscovering.
3. Navigate to the deepest useful page and capture a minimal baseline with a screenshot and targeted DOM analysis.
4. Learn the smallest proven recipe: action -> verify -> save, with targeted retries before full rediscovery.
5. Build a standalone extension in `created-extensions/` from confirmed steps only.
6. Cold-start test the generated extension and iterate only on failed steps.

### Option B: Use the learner script
```
venv\Scripts\python learning-process/scripts/learner.py --url "https://example.com" --prompt "test input"
```
This runs the self-learning loop: screenshot -> discover -> interact -> verify -> save recipe.

### Option C: Use the execution runner
```
venv\Scripts\python learning-process/scripts/runner-execution.py --list learning-process/scripts/execution-list.txt
```
This runs a step list with screenshots, verification, selector learning, and self-correction.

### Option D: Manual discovery
```python
from ai_web_auto_backend.core.server import WebSocketServer
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

### Working Files
```
DO: put all scratch/experimental/temporary scripts in scratch/
DON'T: create working files in the project root
```
The scratch/ folder is gitignored. Use it for one-off scripts, experiments, and exploratory code.

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

### Framework Click Escalation (when CDP click fails on React/Vue/Angular)
```
1. VERIFY: elementFromPoint(x,y) confirms right element; click a nav link to prove CDP works
2. TRY __reactProps: walk up parent chain, find __reactProps$xxx.onClick, call it with fake event
3. IF CONTEXT BREAKS (execute_script returns empty after action): wrap trigger + poll + interact
   in a single Promise inside one Runtime.evaluate call — CDP awaitPromise keeps it alive
```

### React Input Filling
```
DO: Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, text)
    + input.dispatchEvent(new Event('input', {bubbles: true}))
DO: editor.innerHTML = '<p>text</p>' + dispatch input event (for ProseMirror/TipTap)
DON'T: input.value = 'text'  # React ignores it, internal state stays empty
```

### Finding elements: MAIN world `executeScript`
```
DO: chrome.scripting.executeScript({ world: "MAIN" })  # bypasses CSP
DO: CDP Runtime.evaluate for string code on CSP-strict sites (bypasses CSP entirely)
DO: reuse prior working selectors first
DO: XPath for stable elements
DO: [role="tab"][id$="-trigger-PORTRAIT"] for Radix UI tabs
DO: [role="option"] matched by textContent for Radix Select dropdowns (IDs are dynamic)
DO: textContent.includes("Create") for dynamic buttons
DO: verify found input has width>0 and height>0 (React hides real textarea, shows contentEditable div)
DON'T: new Function() in content scripts or MAIN world on CSP-strict sites
DON'T: document.querySelector in content script eval
DON'T: target hidden textarea (width=0) when a visible contentEditable div exists
```

### Hidden/hover-only buttons (Tailwind `group-hover`, `opacity-0`, `pointer-events-none`)
```
DO: extract underlying URLs (img.src, href) and use chrome.downloads.download() directly
DO: JS force-remove pointer-events-none + opacity-0 from button ancestors as fallback
DON'T: assume CDP mouseMoved triggers Tailwind group-hover (it often doesn't)
DON'T: assume el.click() or CDP click works on React download handlers
DON'T: click hidden buttons without first checking pointer-events and opacity CSS
```

### Downloading assets
```
DO: extract <img> src URLs (naturalWidth>200) from CDN domains (fbcdn, googleusercontent, cloudfront)
DO: chrome.downloads.download({ url, conflictAction: 'uniquify' })
DO: check for blob: URLs or canvas elements as fallback sources
DON'T: rely on clicking download buttons — React synthetic events often don't fire from CDP/JS
DON'T: assume the download button triggers a real browser download
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

### DOM Observation (structured change detection)
The platform includes a DOM diffing engine (`dom-observer.js`) that provides structured
analysis of page changes — not just "something changed" but exactly WHAT changed and WHY.

```
DO: use observe_start before actions, observe_diff after to verify
DO: check diff summary for loading/error/success states before proceeding
DO: use DOM diff data alongside screenshots for AI decision-making
DO: check for "Loading state applied" in diff → wait before next action
DO: check for "Error state applied" in diff → change strategy
DON'T: rely solely on screenshots for verification — DOM diff catches invisible state changes
```

The AI agent automatically uses DOM observation during `task` execution. For manual/scripted
flows, use `observe`/`diff`/`unobserve` CLI commands or the Python API:
```python
await server.observe_start("#my-form")   # snapshot a specific section
# ... perform actions ...
resp = await server.observe_diff()        # structured diff with reasons
changes = resp.data.extracted_data["changes"]  # [{type, tag, selector, reason, ...}]
summary = resp.data.extracted_data["summary"]  # human-readable AI-friendly summary
await server.observe_stop()
```

### Verification rules
```
DO: verify typed text exists in the intended input
DO: verify a click changed state, not just that the click returned
DO: use observe_diff to confirm DOM state changes after interactions
DO: poll for ready signals such as active tabs, result images, or download buttons
DO: save only proven steps to recipes and generated extensions
DON'T: assume success because the UI looked right once
DON'T: ignore DOM diff showing error/loading states
```

## Key Files

| File | Purpose |
|------|---------|
| `ai-web-auto-extension/js/service-worker.js` | CDP engine + WS client + command router |
| `ai-web-auto-extension/js/modules/dom-observer.js` | DOM diffing engine — snapshot comparison, reason inference |
| `ai-web-auto-extension/js/content/content-main.js` | Data extraction + fallback only |
| `ai_web_auto_backend/core/server.py` | WebSocket server with CDP/type commands |
| `ai_web_auto_backend/core/models.py` | Pydantic models for command protocol |
| `ai_web_auto_backend/automation_controller.py` | BrowserAgent high-level API |
| `ai_web_auto_backend/ai/prompts.py` | Claude action prompt rules for the AI agent |
| `learning-process/scripts/learner.py` | Self-learning automation loop |
| `learning-process/scripts/runner-execution.py` | Self-correcting step runner with learned selectors |
| `learning-process/output/execution/learned_selectors.json` | Persisted working selectors from prior runs |
| `.claude/skills/web-auto.md` | Skill for building new extensions |
| `created-extensions/skool-down-course/skool-down-course-backend/sdc_downloader.py` | Skool course downloader (proven, reliable) |

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
| Navigate opens new tab | Fixed: reuses existing tab via `chrome.tabs.update()` |
