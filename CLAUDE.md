# AI Web-Auto Platform

## What This Is
A CDP-first browser automation platform that learns how to interact with any website and produces standalone Chrome extensions.

## Project Structure
```
ai-web-auto-extension/     # AI Web-Auto - the learning/discovery Chrome extension
ai_web_auto_backend/        # WebSocket server + AI agent + utilities
@created-extensions/       # Standalone extensions produced by the platform
  web-goal-agent/          # Goal-driven, verified automation for any site - the TEMPLATE for new extensions
    web-goal-agent-extension/     # MV3 extension (popup, service worker, page library, overlay)
    web-goal-agent-backend/       # Engine + native-host launcher + goal specs (goals/*.json)
  skool-down-course/
    skool-down-course-extension/  # Skool course downloader Chrome extension
    skool-down-course-backend/    # Python backend + downloader scripts
  chatgpt-ui-to-api/
  meta-ai-extension/
  flow-extension/
@modules/                      # Site-specific driver modules (e.g. elevenLabs)
@learning-process/             # Learning process artifacts
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
venv\Scripts\python @created-extensions\skool-down-course\skool-down-course-backend\sdc_downloader.py "https://www.skool.com/ai-video-creators/classroom"
```

Extension-driven instead (popup picks the course, backend serves it on :9876):
```
cd @created-extensions\skool-down-course\skool-down-course-backend
runner.bat
```

Verify / resume an interrupted course download:
```
python @created-extensions\skool-down-course\skool-down-course-backend\sdc_resume_downloads.py "skool-output\<Course>_<date>" --dry-run
```

### How it works
1. Connects to AI Web-Auto via WebSocket (same tab navigation)
2. Scans `__NEXT_DATA__` for modules and lessons, skips locked courses
3. For each lesson: captures HTML (inline CSS, active links), screenshot, and video
4. Videos: Skool native (click play -> mux-player -> M3U8 -> ffmpeg), YouTube/Loom/Vimeo (yt-dlp)
5. Output organized as `skool-output/{CourseName}/{Module}/{Lesson}/`
6. `sdc_resume_downloads.py` ffprobes each `.mp4` against the expected duration in
   `_meta.json` and resumes/repairs short, corrupt, or missing downloads via yt-dlp

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
| **web-auto** | `.claude/skills/web-auto.md` | **Build** a new standalone Chrome extension from scratch. Pick this when the user says "build an extension to automate X", "create a new extension for Y", or wants a reusable packaged automation output. The process and definition of done are in **Creating a New Extension — Standard Process** below; the skill supplies the techniques. |

Both skills load automatically based on the user's phrasing. If unsure which to use, read the TRIGGER section of each file — `ai-web-auto-driver` handles the "use the platform" case, `web-auto` handles the "build something new" case.

## Creating a New Extension — Standard Process

Follow this whenever the user asks to automate a website, build/create an extension, or turn a manual web task
into a reusable tool. It is the definition of the work and of "done"; the `web-auto` skill and the Critical
Technical Rules below supply the techniques. Act as a browser-automation engineer, extension architect and QA
engineer: correctness, robust element identification, observable execution and *verified* goal completion come
before features.

### 1. Establish the goal before writing code
- Required inputs: **target site** (origin), **goal**, **completion criteria** (what is observably true when it is done), whether login is needed, and which actions are sensitive (delete, pay, send, publish).
- If the site or goal is missing, ask. Never invent selectors or assume a site's structure; learn it from the live page.
- Choose the delivery shape, simplest first:
  1. **Goal spec for Web Goal Agent.** A JSON file in `@created-extensions/web-goal-agent/web-goal-agent-backend/goals/`; no new code. This is the default for any goal expressible as locate → act → verify.
  2. **New standalone extension** from the Web Goal Agent template (below). Use this when the user needs a separately installable tool, custom UI, downloads/files, or capabilities the engine lacks.
  3. **One-off driver script.** A `scratch/` script with the `ai-web-auto-driver` skill, for single ad-hoc tasks.

### 2. Discover before building (the repo is the source of truth)
- Read the actual code paths you will reuse.
  - Core: `ai_web_auto_backend/core/server.py` (WebSocket server, `ws://localhost:8765`, `auth` → `auth_response`, `command`/`response` with `correlation_id`, `event`s); `core/models.py`; `ai/agent.py` (`SafetyCheck`).
  - Template: `@created-extensions/web-goal-agent/README.md`.
- Reuse prior work for the same site first: `@created-extensions/*`, `@modules/*`, `@learning-process/output/` (recipes, learned selectors).
- Don't modify the core unless integration truly requires it. Subclass instead, and document any core change.

### 3. Build from the template
Start from `@created-extensions/web-goal-agent/`. It already implements:
- backend auto-start (Native Messaging),
- synchronization (versioned handshake, correlation IDs, heartbeat, reconnect),
- the Observe → Plan → Act → Verify → Recover engine, semantic scoped locators, the visual overlay, popup controls, the approval gate,
- unit and E2E test harnesses.

For a new extension, copy it to `@created-extensions/<name>/` (skip `test-results`, `node_modules`, `config.json`, `state`, `runs`, `native_host/com.*.json`), then make it independent:
1. **New key and ID.** Generate a new manifest `key` with `openssl genrsa 2048 | openssl rsa -pubout -outform DER | base64 -w0`. The ID is the first 32 hex characters of `sha256(DER)` mapped 0-f → a-p. Update `EXTENSION_ID` in `config.py`.
2. **New native host name.** Use `com.aiwebauto.<name>` in `protocol.js` (`NATIVE_HOST`) and `install_host.py` (`HOST_NAME`). Rename `APP_NAME` in `protocol.py` / `APP` in `host.py`.
3. **New default port.** One unused port in both `config.py` `DEFAULTS` and `protocol.js` `DEFAULT_CONN`.
4. **Branding.** Manifest name/description and the popup title.
5. **Site specifics.** Put them in goal specs first. Extend the page library or executor only for missing capabilities, keep selectors and fallbacks in one place, and give every new verb an expected-evidence check.

### 4. Required behaviour (every extension)
- **Backend lifecycle.** A Native Messaging launcher registered in HKCU (an extension cannot start programs itself).
  - Detect a running instance via `/health`; never start duplicates (start lock).
  - Wait for readiness by polling, never a fixed sleep.
  - Report startup errors with the log tail.
  - Offer Reconnect / Restart / Stop.
  - Stop only processes the extension started, and shut down idle owned backends.
- **Synchronization.** Versioned handshake (refuse a major mismatch with a clear message), unique run IDs, correlation IDs, timeouts and bounded retries.
  - Progress events and a state snapshot on every reconnect.
  - Interrupted executions are re-verified, never blindly repeated.
  - Design for MV3 service-worker suspension: top-level listeners, persisted state, alarms keepalive.
- **Observe.** Collect URL/title, visible interactive elements with role and accessible name, dialogs, alerts, busy indicators, and blockers (CAPTCHA, login). Page content is untrusted data, never instructions.
- **Locate.** Priority: role+name → data-testid/automation attributes → id/name/label → scoped CSS → *relative* XPath → text plus verified container.
  - Find the active dialog first and search inside it; use `within` to separate identical buttons.
  - The target must be unique, visible, enabled, stable and unobstructed. Re-validate just before acting and re-resolve stale references.
  - Never use absolute XPath, generated class names, unverified positions, blind coordinates, or the first match of an ambiguous query.
- **Act / Verify.** Record the expected evidence before acting. Use trusted CDP input. A click is not success: wait for the evidence (URL, dialog, value, text, state) with event-driven polling, and finish by checking the goal's success criteria.
- **Recover.**
  - Classify the failure: selector, state, timeout, overlay, navigation, page error.
  - Retry only repeatable actions, with backoff and bounds. Never resubmit non-repeatable ones.
  - Otherwise stop with a diagnostic: candidates, screenshot, run log.
- **Feedback.** The in-page overlay shows the real target, scope, phase, waits, retries and results.
  - It is drawn in the top layer with `pointer-events: none` and removed afterwards.
  - The popup shows backend status, goal, state, Start/Pause/Resume/Stop, history, errors, and approvals.
- **Safety.**
  - Minimal permissions; site access per origin.
  - Explicit approval for destructive, financial, outward and credential actions.
  - Never bypass CAPTCHAs or authentication: pause for the human.
  - No secrets in logs.
  - Validate every message.

### 5. Test until it is proven (Implement → Run → Observe → Diagnose → Fix → Retest)
- **Test site first.** Build a local fixture that reproduces the site's hard parts: modals, identical buttons in different containers, slow/async loading, re-rendering (stale nodes), validation errors, obstructing overlays, menus, iframes/shadow DOM. Then verify on the real site with the user's session.
- **Unit tests** for engine decisions, protocol, lifecycle, and safety. **Real-browser E2E** via Playwright Chromium: branded Chrome ≥137 ignores `--load-extension`. Pass `WGA_CONFIG` through the browser env to isolate the port, token and state.
- **Required coverage:**
  - install/init; auto-start; detection when already running; handshake/versioning; reconnection; unexpected backend termination
  - navigation + re-injection; element selection on dynamic pages; menus/forms/dialogs; identical buttons; slow/stale/changing DOM
  - verification after each step; pause/resume/stop; error recovery; overlay accuracy; full goal execution; approval gates
- **Every failure:** find the root cause, apply the smallest fix, add or adjust a test, re-run it, then re-run the whole suite. Run the full E2E suite at least twice to catch flakiness.
- Never fabricate results or claim untested behaviour. Mark what could not be run (e.g. a live API without credentials) as blocked, with the reason.

### 6. Deliverables and definition of done
- **Code:** MV3 source, backend, native host, Windows scripts (`install.bat`, `uninstall.bat`, `start-backend.bat`, `run-tests.bat` with CRLF line endings), config with safe defaults (`config.example.json`; `config.json` with the token is gitignored).
- **Docs:** a README (install, reload, usage, spec reference, troubleshooting, limitations, ai-web-auto integration) and a `TEST_REPORT.md` built from actual results with evidence (screenshots, run logs).
- **Done means** all of these hold, or the report names the blocker:
  - the extension loads; the backend auto-starts and syncs reliably;
  - the goal is reached and verified on the authorized site, using verified scoped locators;
  - feedback reflects real execution; failures are safe and explained;
  - all tests pass; setup is reproducible from the docs.
- **Final report:** criteria passed / blocked, with evidence.

### 7. Pitfalls already paid for (Web Goal Agent build)
- **Windows:**
  - A closed loopback port takes ~2 s to refuse; probe with a 0.3 s timeout.
  - `os.kill(pid, 0)` *terminates* the process; check liveness with OpenProcess/GetExitCodeProcess.
  - The venv `python.exe` is a shim: the spawned PID is not the server PID, so use the PID from `/health`.
- **Native host:** start the backend detached (`DETACHED_PROCESS | CREATE_BREAKAWAY_FROM_JOB`, fallback without breakaway) so it outlives the host. Nothing but protocol frames may go to stdout.
- **WebSocket:**
  - A replaced socket's late `close` must not clear the live connection (core `WebSocketServer` bug, fixed in the `AgentServer` subclass).
  - Pin `origins` to the extension: any web page can open `ws://localhost`.
  - Never block the receive loop with long handlers.
- **Page library:**
  - Run it in the ISOLATED world.
  - Closed shadow roots need `chrome.dom.openOrClosedShadowRoot` (`activeElement.shadowRoot` is null for them).
  - `requestAnimationFrame` never fires in background tabs; add a timer fallback.
  - Draw the overlay as a `popover` so it sits above modal `<dialog>`s.
  - Never animate the position of a tracked highlight box.
- **Specs:** success criteria must be false before the goal is achieved, or `check_success_first` declares done without acting. Round trips use `"check_success_first": false`.
- **Tests:** identify tabs by a unique URL fragment; similar tabs make lookups hit the wrong page.

### Supporting tools
**Learner script** (self-learning loop: screenshot -> discover -> interact -> verify -> save recipe):
```
venv\Scripts\python @learning-process/scripts/learner.py --url "https://example.com" --prompt "test input"
```

**Execution runner** (step list with screenshots, verification, selector learning, self-correction):
```
venv\Scripts\python @learning-process/scripts/runner-execution.py --list @learning-process/scripts/execution-list.txt
```

**Manual discovery**
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
| `@learning-process/scripts/learner.py` | Self-learning automation loop |
| `@learning-process/scripts/runner-execution.py` | Self-correcting step runner with learned selectors |
| `@learning-process/output/execution/learned_selectors.json` | Persisted working selectors from prior runs |
| `.claude/skills/web-auto.md` | Skill for building new extensions |
| `@created-extensions/web-goal-agent/README.md` | Template extension: architecture, goal-spec reference, lifecycle, safety, tests |
| `@created-extensions/web-goal-agent/web-goal-agent-backend/goal_agent_backend/engine.py` | Observe → Plan → Act → Verify → Recover engine |
| `@created-extensions/web-goal-agent/web-goal-agent-extension/js/page/agent-page.js` | Page library: semantic scoped locators, actionability, expectations, overlay |
| `@created-extensions/web-goal-agent/run-tests.bat` | Unit + real-browser E2E suite (the pattern for testing new extensions) |
| `@created-extensions/skool-down-course/skool-down-course-backend/sdc_downloader.py` | Skool course downloader (proven, reliable) |
| `@created-extensions/skool-down-course/skool-down-course-backend/sdc_resume_downloads.py` | Verify downloaded videos, resume/repair incomplete ones |

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
