# AI Web-Auto

A CDP-first browser automation platform that learns how to interact with a website, then produces a standalone Chrome extension that replays what it learned.

Most automation breaks because it fakes events. This drives Chrome through the **Chrome DevTools Protocol**, so clicks and keystrokes are trusted input — React, Vue, and Radix UI can't tell the difference between it and a person.

```
   Python backend  ──WebSocket (:8765)──▶  AI Web-Auto extension
   ai_web_auto_backend/                    ai-web-auto-extension/
        │                                          │
        │  navigate / click / type / extract       │  chrome.debugger
        │  observe / diff / task                   ▼
        │                                    Chrome DevTools Protocol
        └──────────── learned recipe ───────▶ created-extensions/<name>/
                                              (standalone, no backend needed)
```

## Why CDP

`element.click()` and `input.value = "text"` are invisible to modern frameworks — React reads its own synthetic event stream and internal state, so the DOM changes but the app never notices. CDP dispatches at the browser level, above the page, which is why it works where scripted events don't.

The trade-offs this codebase settled on, the hard way:

| Task | Use | Not |
|---|---|---|
| Typing | `Input.insertText` via CDP | `el.value = …`, `execCommand` |
| Clicking | CDP trusted mouse events at resolved coordinates | `el.click()` |
| Reading the DOM | `executeScript` in the `MAIN` world | content-script eval (CSP blocks it) |
| React inputs | the native `value` setter + a bubbled `input` event | assigning `.value` |
| Verifying an action | DOM diff via `observe` / `diff` | a screenshot alone |

[CLAUDE.md](CLAUDE.md) carries the full rule set, including framework click escalation and the hover-only-button workarounds.

## Quick start

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Load the extension: open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**, and select [ai-web-auto-extension/](ai-web-auto-extension/). It auto-connects to `ws://localhost:8765` with the token `local-dev-token`.

Start the server:

```bash
runner.bat
```

You get a `>` prompt:

| Command | What it does |
|---|---|
| `navigate <url>` | Open a URL in the connected tab |
| `screenshot` | Capture the viewport to `learning-process/output/` |
| `discover` | List and highlight every interactive element |
| `click <selector>` | Click by CSS selector or XPath (CDP) |
| `type <text>` | Type into the focused input (CDP) |
| `extract` | Pull the visible page text |
| `observe [selector]` | Snapshot a subtree (default `body`) |
| `diff` | Structured DOM changes since the snapshot, with inferred reasons |
| `unobserve` | Stop observing |
| `task <objective>` | Hand the objective to the AI agent |
| `quit` | Exit |

Only `task` needs an API key — set `ANTHROPIC_API_KEY` and the [Anthropic SDK](https://docs.claude.com/en/api/overview) picks it up. Everything else is pure CDP and runs offline.

## Self-play jobs

For runs that should survive crashes, learn across sessions, and produce a report, use the jobs layer instead of one-shot `task`:

```bash
venv\Scripts\python -m ai_web_auto_backend.jobs start --url "https://example.com" --objective "Find the pricing page and extract plan names"
venv\Scripts\python -m ai_web_auto_backend.jobs explore --url "https://example.com"   # self-play: map the site, no hard goal
venv\Scripts\python -m ai_web_auto_backend.jobs resume --latest                       # continue after a crash/budget stop
venv\Scripts\python -m ai_web_auto_backend.jobs list
venv\Scripts\python -m ai_web_auto_backend.jobs report <job-id> --open
venv\Scripts\python -m ai_web_auto_backend.jobs knowledge                             # what selectors the platform has proven
```

Each job gets `jobs/<id>/` with an append-only `journal.jsonl`, failure screenshots, and a self-contained `report.html`. Verified selectors accumulate in `knowledge/site/<domain>.json` with success/failure counters and are injected into future prompts on the same domain — run N+1 starts warm.

## DOM observation

Screenshots tell you a page *looks* right. They miss state that never renders. `observe` snapshots a subtree, `diff` reports what actually changed and infers why:

```python
await server.observe_start("#my-form")
# ... perform actions ...
resp = await server.observe_diff()
resp.data.extracted_data["changes"]   # [{type, tag, selector, reason, ...}]
resp.data.extracted_data["summary"]   # human-readable, AI-friendly
await server.observe_stop()
```

A diff reading `Loading state applied` means wait; `Error state applied` means change strategy. The AI agent consumes these automatically during `task`.

## What's been built with it

| Extension | Target | What it does |
|---|---|---|
| [skool-down-course](created-extensions/skool-down-course/) | Skool.com | Downloads whole classroom courses — video, HTML, screenshots, resources |
| [chatgpt-ui-to-api](created-extensions/chatgpt-ui-to-api/) | ChatGPT | Turns a logged-in browser session into a local REST API |
| [flow-extension](created-extensions/flow-extension/) | Google Labs Flow | Image/video generation with configurable settings and auto-download |
| [meta-ai-extension](created-extensions/meta-ai-extension/) | Meta AI | Image generation with ratio selection and CDN-URL download |

Each ships as a normal MV3 extension. Once built, they don't need the backend — the learned selectors are baked in. [_projects/](_projects/) documents what each one had to solve.

## Building a new automation

**Ask Claude.** Say "automate [site] to do [task]". Two skills in [.claude/skills/](.claude/skills/) cover the two cases:

- **[web-auto](.claude/skills/web-auto.md)** — build a new standalone extension from scratch
- **[ai-web-auto-driver](.claude/skills/ai-web-auto-driver.md)** — drive the existing platform for a one-off scrape or interaction

Either way the loop is the same: reuse before rediscovering, prove the smallest happy path, verify every step, and only then generate the extension.

**Or run the learner directly:**

```bash
venv\Scripts\python learning-process/scripts/learner.py --url "https://example.com" --prompt "test input"
```

Screenshot → discover → interact → verify → save recipe. Working selectors persist to `learning-process/output/execution/learned_selectors.json`, so the next run starts from what already worked.

**Or drive it from Python:**

```python
from ai_web_auto_backend.core.server import WebSocketServer

s = WebSocketServer()
await s.start()
await s.wait_for_connection(timeout=60)

await s.navigate("https://example.com")
await s.screenshot()
await s.execute_script("return JSON.stringify({...})")   # MAIN world
await s.type_cdp("hello", xpath="/html/.../input")
await s.click_cdp(x, y)
```

## Layout

```
ai-web-auto-extension/          # The learning extension - CDP engine + WS client
  js/service-worker.js          #   command router, CDP calls, MAIN-world scripts
  js/modules/dom-observer.js    #   snapshot diffing and reason inference
ai_web_auto_backend/            # WebSocket server + AI agent
  core/server.py                #   protocol, CDP command surface
  ai/agent.py                   #   Claude-driven task loop
  automation_controller.py      #   BrowserAgent API + the `>` CLI
created-extensions/             # Standalone extensions this platform produced
learning-process/               # Learner/runner scripts and their output
_projects/                      # Write-ups of each finished automation
.claude/skills/                 # Skills that teach Claude to use and extend this
```

## Requirements

- Python 3.10+ and Chrome
- `ffmpeg` / `ffprobe` and `yt-dlp` on `PATH` — only for the Skool downloader's video handling
- `ANTHROPIC_API_KEY` — only for AI-driven `task` runs

The AI agent's model is a constructor argument on `BrowserAgent`, currently defaulting to `claude-sonnet-4-20250514`. Pass `model=` to use a newer one, e.g. `claude-opus-5`.

## Troubleshooting

| Problem | Fix |
|---|---|
| Extension won't connect | Reload it in `chrome://extensions`; the keepalive alarm fires every 15s |
| `ERR_CONNECTION_REFUSED` | Start the Python server first (`runner.bat`) |
| `Auth denied` | Token mismatch — both sides default to `local-dev-token` |
| `execute_script` returns empty | CSP is blocking it — use the service worker's MAIN world |
| Ctrl+A selects the whole page | CDP-click the input to focus it before clearing |
| Radix UI tabs don't switch | Use a CDP click; `.click()` isn't a trusted event |
| Port 8765 in use | `netstat -aon \| findstr 8765`, then `taskkill /PID <pid> /F` |

More in [CLAUDE.md](CLAUDE.md#troubleshooting).

## A note on scope

This drives *your* browser session against sites you already have access to. It doesn't bypass authentication, paywalls, or access controls — the Skool downloader, for example, explicitly skips locked courses. Check the terms of any site you point it at.
