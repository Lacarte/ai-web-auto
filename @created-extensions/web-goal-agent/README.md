# Web Goal Agent

Goal-driven browser automation for any website, built on ai-web-auto. You state a goal; the extension
observes the page, plans, acts with trusted CDP input, **verifies every step on the page**, recovers from
the usual failures, and reports success only when the goal's success criteria are observed.

- **Goal specs** (JSON) run deterministically: semantic locators, expected evidence per step, success criteria.
- **Free-text goals** use a Claude planner (optional, needs Anthropic credentials) through the same verified path.
- The **backend starts itself** through Chrome Native Messaging; no console to keep open.
- **Visible execution**: the page shows what is being inspected, clicked and verified; the popup shows state,
  history, errors, and asks before destructive, financial or outward actions.

It is also the reference template for new extensions in this repo (see "Building a new extension" in
[CLAUDE.md](../../CLAUDE.md)).

## Architecture

```
 Popup (control panel)                     Page (any authorized site)
   │  chrome.runtime port                    ▲  agent-page.js, ISOLATED world:
   ▼                                         │  observe · locate · actionability · expectations · overlay
 Service worker ── executor ── CDP (trusted mouse/keys, screenshots, network idle, JS dialogs)
   │  BackendLink: native host + WebSocket (auth, protocol version, heartbeat, reconnect)
   │
   │ chrome.runtime.sendNativeMessage           ws://127.0.0.1:8766  (ai-web-auto command/response protocol)
   ▼                                            ▼
 native_host/host.bat ── start/detect/stop ──▶ Python backend (python -m goal_agent_backend)
                                                 AgentServer ⊂ ai-web-auto core WebSocketServer
                                                 Engine: Observe → Plan → Act → Verify → Recover
                                                 Planner: goal spec | Claude (optional)
                                                 Safety, run logs (JSONL, redacted)
```

The backend is the brain (it survives MV3 service-worker suspension); the extension executes commands on
one explicitly bound tab and draws feedback.

## Install

Prerequisites: the ai-web-auto root `install.bat` has been run (it creates `venv`), and Chrome, Edge,
Brave or Chromium is installed.

1. Run **`install.bat`** in this folder. It registers the native host for the current Windows user (HKCU, no
   admin) and creates `web-goal-agent-backend/config.json` with a random token.
2. Open `chrome://extensions`, enable **Developer mode**, click **Load unpacked**, and pick
   `web-goal-agent-extension/`. The ID must be `lfejjmliceaahhcnbdlmafhklbehnpab`. The `key` in the manifest pins
   it, and the native host only answers that ID.
3. Click the extension icon. The popup shows **Backend v0.1.0** once the backend is started and connected.

**Reloading after code changes:**
- After extension (JS/HTML) changes, click the reload icon on the card in `chrome://extensions`. The backend keeps running, and the reloaded extension reconnects to it.
- After backend (Python) changes, restart the backend. One way is **Settings → Stop backend** in the popup; closing and reopening the popup then starts the new code. A backend started with `start-backend.bat` is restarted from its own console. There's no need to re-run `install.bat`.

**Uninstall:** `uninstall.bat` removes the registry entries; remove the extension in `chrome://extensions`.

## Use

1. Open the site you want to automate in a tab, then open the popup from that tab.
2. Grant access for the site (**Authorize this site**). Access is per origin and requested only when needed.
3. Choose a **Goal spec** (and fill its inputs), or **Describe a goal** (AI planner).
4. **Start**. Watch the overlay; use **Pause / Resume / Stop** at any time. When a step needs approval, the
   badge shows `!` and the popup shows **Approve / Reject**.

Try it on the bundled demo site: run `serve-demo-site.bat`, open `http://127.0.0.1:8777/crm.html`, and run
*Demo CRM: rename a customer*. The four `demo-*` goals cover dialogs, menus, top-layer `<dialog>`, native
selects, a closed shadow root, a same-origin iframe, a lazy-loaded list, and an approval gate.

## Goal specs

Goal specs are JSON files in `web-goal-agent-backend/goals/` (reloaded on every Start). Validation runs
when a file is loaded; the popup lists rejected files with the reason.

```json
{
  "id": "crm-rename-customer",
  "title": "Rename a customer",
  "start_url": "https://crm.example.com/customers",
  "allowed_origins": ["https://crm.example.com"],
  "inputs": { "customer": { "default": "Ada Lovelace" }, "new_name": {}, "password": { "secret": true } },
  "interrupts": [{ "id": "cookies", "when": [{ "dialog_open": { "name": "Cookie preferences" } }],
                   "do": { "id": "reject", "action": "click",
                           "target": { "role": "button", "name": "Reject optional", "within": "active-dialog" },
                           "expect": [{ "dialog_closed": { "name": "Cookie preferences" } }] } }],
  "steps": [
    { "id": "open-edit", "action": "click",
      "target": { "role": "button", "name": "Edit", "within": { "role": "row", "name": { "contains": "{{inputs.customer}}" } } },
      "expect": [{ "dialog_open": { "name": "Edit customer" } }], "retry": { "safe_to_repeat": true } },
    { "id": "name", "action": "type", "target": { "label": "Name", "within": "active-dialog" }, "value": "{{inputs.new_name}}" },
    { "id": "save", "action": "click", "target": { "role": "button", "name": "Save", "within": { "role": "dialog", "name": "Edit customer" } },
      "expect": [{ "dialog_closed": { "name": "Edit customer" } }] }
  ],
  "success": [{ "visible": { "role": "row", "name": { "contains": "{{inputs.new_name}}" } } }]
}
```

### Locators

A locator describes an element the way a person would. Strategies in one locator are combined (AND); `any`
lists alternatives in priority order (first that matches wins). Prefer them in this order:

| Key | Matches | Notes |
|---|---|---|
| `role` + `name` | ARIA role (explicit or implicit) and accessible name | Best default. `name` needs `role`. |
| `testid` | `data-testid`, `data-test-id`, `data-test`, `data-qa`, `data-cy` | When the site has them |
| `id`, `label`, `placeholder` | Element id / associated label text / placeholder | `label` for form fields |
| `css` | CSS selector within the scope | Avoid generated class names |
| `xpath` | XPath **relative to the scope** (`.//…`) | Absolute `/html/…` paths are rejected |
| `text` | Visible text; resolves to the innermost element, lifted to its button/link | Fallback |
| `within` | `"active-dialog"`, `"page"`, or a container locator (row, dialog, region…) | Disambiguates identical controls |
| `nth` | Index among matches | Only when order is truly meaningful |

Text matches (`name`, `label`, `text`, …) are a string (exact, whitespace-normalised, case-insensitive) or
`{"contains": …}`, `{"regex": …}`, plus optional `"case_sensitive": true`.

**Scope rules.**
- Action targets with no `within` search the topmost open **modal** dialog first, as a person would. Without a modal they search the whole page.
- Expectations without `within` look at the whole page.
- If several elements match and no `nth` is given, the step fails as **ambiguous** and lists the candidates. It never picks the first.

Search covers open **and closed** shadow roots and same-origin iframes. Cross-origin iframes are reported in
observations but are not reachable.

### Steps

| `action` | Needs | Implicit evidence | Repeated on failure? |
|---|---|---|---|
| `click` | `target`, `expect` | — | No, unless `retry.safe_to_repeat: true` |
| `type` | `target`, `value` (`clear`: true) | field value equals the text | Yes |
| `select` | `target` (native `<select>`), `value` (option value or text) | value | Yes |
| `check` | `target`, `checked` | checked state | Yes |
| `press` | `key`, optional `target`, `modifiers` | — (add `expect`) | No |
| `hover`, `scroll` | `target` (scroll may omit it), `direction` | — | Yes |
| `navigate` | `url` (inside `allowed_origins`) | — | Yes |
| `wait` / `assert` | `expect` | — | — |
| `extract` | `fields: {name: locator}` | values found | Yes |

Other step keys:
- `intent`: the label shown in the overlay and history.
- `skip_if`: expectations that mean the step is already done.
- `timeout_s`, `retry.attempts`, `optional`.
- `sensitive: true`: always ask the user first.

### Expectations (evidence)

`url` (`equals|contains|regex|changed`), `title`, `visible`, `hidden`, `enabled`, `disabled`, `focused`,
`dialog_open` / `dialog_closed` (`name`, `role`), `text_present` / `text_absent` (`text`, `within`),
`value` (`target`, `equals|contains|regex`), `checked` (`target`, `value`), `attribute`
(`target`, `name`, `equals|contains|regex|present`), `count` (`target`, `eq|gte|lte|gt|lt`). `"$target"` refers
to the step's own target.

**Success criteria must be false before the goal is achieved.** The engine checks them first
(`check_success_first`) and does nothing if they already hold. That makes re-runs safe, but a criterion like
"the dialog is closed" is true on page load. Set `"check_success_first": false` for round-trip goals that end
where they start.

### Interrupts and inputs

`interrupts` handle popups that can appear at any time (cookie walls, promos). They are checked before each step
and whenever a target is covered. `inputs` are filled in the popup; `{{inputs.x}}` substitutes anywhere.
Inputs marked `secret` are never written to logs, history, or the overlay.

## How a run works

1. **Observe**: bind the tab, navigate to `start_url` if needed, and check whether the goal is already met.
2. Per step:
   - **Plan**: skip the step if its `skip_if` or `expect` already hold; otherwise handle interrupts.
   - **Locate**: find one target that is visible, enabled, stable and unobstructed. The overlay outlines the scope and the target.
   - **Confirm**: if the action is sensitive, wait for the user's approval.
   - **Act**: re-validate the target immediately before dispatching trusted input. A stale reference is re-resolved from the locator.
   - **Verify**: wait for the page to settle (DOM quiet, no busy indicators, network idle), then wait for the evidence. Waits are event-driven with bounded time slices; there are no fixed sleeps.
3. **Verify the goal**: the success criteria must hold.

| Problem | Recovery |
|---|---|
| Target not rendered yet, disabled, animating, covered | Wait (bounded); run interrupt handlers; describe what covers it |
| Target in a lazy list | Scroll the containing scroller step by step and re-search |
| Element replaced by a re-render | Re-resolve from the locator just before input |
| Several matches | Fail as `ambiguous` with candidates; never guess |
| Page shows a new error (`role=alert`, `aria-invalid`) | Fail with the page's message; never resubmit |
| Evidence missing after a non-repeatable action | One extra verification window, then fail; never re-click |
| Connection lost mid-action | Wait for reconnection, re-bind, re-check the page. Continue if the effect is there; ask the human for non-repeatable steps |
| Native `alert/confirm`, CAPTCHA, login wall, AI planner unsure | Pause for the human; resume afterwards |
| Page leaves the authorized origins | Stop |

## Backend lifecycle

The native host (`native_host/host.py`, launched by Chrome via `host.bat`) answers `ensure`, `status`,
`stop`, `restart`:

- **Detects** a running backend via `GET /health` (and refuses ports owned by other programs).
- **Prevents duplicates** with a start lock: concurrent `ensure` calls start exactly one process.
- **Starts** the backend detached, so it outlives the short-lived host. It **waits for `/health`**, not a fixed delay, and reports a crash at startup with the backend's log tail.
- **Stops** only a backend it started. A backend you started with `start-backend.bat` is never stopped by the extension.
- A backend started by the host **exits after 30 idle minutes** with no extension connected and no run
  (`idle_shutdown_minutes`). The next popup open starts it again.

The extension reconnects with backoff (1 → 15 s). After repeated failures it asks the host to `ensure` again,
which restarts a backend that died. A 15 s heartbeat detects a half-open connection. Manual controls: popup
**Reconnect / Start backend / Restart backend**, and **Stop backend** under Settings.

Without the native host (e.g. a locked-down machine), run `start-backend.bat` and enter the port and the token
from `config.json` under the popup's **Settings → Manual connection**.

## Protocol and synchronization

- **Transport.** The ai-web-auto protocol: `auth` → `auth_response`, `command`/`response` matched by
  `correlation_id` with timeouts, and `event`s.
- **Handshake.** The auth message carries the extension version and protocol number. A major mismatch is refused with `protocol_mismatch`, and the popup explains it.
- **Requests.** Popup and service-worker requests (`start_run`, `pause_run`, `confirm`, …) travel as `ui_request` events. Replies come back as `ui_reply` with the same `request_id`. Payloads are validated with strict schemas; unknown operations are rejected.
- **Progress.** The backend pushes `run_event`s; the backend's `state_snapshot` (sent on every reconnect) is authoritative. The extension caches state in `storage.session`, so a service-worker restart doesn't blank the popup.
- **Run IDs.** Each run has a unique `run_id`. Events, logs (`runs/<date>/<run_id>.jsonl`) and failure screenshots are keyed by it.

## Safety

- **Site access is opt-in.** Optional host permissions are requested per origin from the popup. Only `127.0.0.1`/`localhost` are granted at install, for the demo site and the backend.
- **Exact origins.** Every run is limited to its `allowed_origins`, checked in both the engine and the executor. A redirect elsewhere stops the run.
- **Approval gate.** The user must approve actions that look destructive (delete, remove…), financial (pay, buy, subscribe…), outward (send, post, publish…), typing into password or payment fields, and confirm buttons in alert dialogs. Buttons that only open a dialog (`aria-haspopup`) are exempt; the dialog's commit button is gated instead. Specs cannot opt out unless `allow_spec_to_skip_confirmation` is enabled.
- **No bypassing access controls.** CAPTCHAs, logins and JS dialogs are never solved or bypassed; the run pauses for you.
- **Backend reachable only by the extension.** It listens on 127.0.0.1 only and pins the WebSocket `Origin` to the extension, because any web page can open `ws://localhost`. The per-install token travels only over the native-messaging channel, and the token is never logged.
- **Page content is data.**
  - The page library runs in the isolated world, so page scripts can't tamper with it.
  - For the AI planner, page text is framed as untrusted, and the model's output is schema-validated.
  - Every planned action still goes through origin checks, the approval gate, and verification.
- **Logs are redacted.** Secret inputs and credential-like fields are masked in logs.

## Configuration

`web-goal-agent-backend/config.json` (created by `install.bat`; defaults in
[config.example.json](web-goal-agent-backend/config.example.json)). `WGA_CONFIG` overrides the path. Chrome
passes its environment to the native host, which is how the E2E suite isolates itself.

## Integration with ai-web-auto

The core is reused rather than modified; no file in `ai_web_auto_backend/` or `ai-web-auto-extension/` was changed.

| Reused from the core | How |
|---|---|
| `WebSocketServer` | Subclassed as `AgentServer`: command/response correlation, timeouts, event routing, `SessionState`, convenience methods |
| `Command` / `Response` models | `AgentCommand` widens `action` to `str` for the new verbs; responses use `extracted_data` |
| `SafetyCheck` | Rate limiting and the domain blacklist |
| Proven CDP techniques from `ai-web-auto-extension` | Trusted press/release clicks, `Input.insertText`, focus before select-all, React native-setter fallback |

`AgentServer` overrides only `start()` and `_handle_connection()`:
- localhost binding, Origin pinning, `/health` + `/shutdown`, protocol negotiation;
- "newest connection wins" without the core's race where a stale socket closing clears the live one;
- per-message error isolation.

The extension implements the core verbs too (`navigate`, `click`, `type`, `execute_script`, `extract`, `screenshot`,
`key`, `scroll`, `wait`, `find`, `list_tabs`). Existing scripts can therefore drive it with the **unmodified**
core server on this port; E2E test 21 proves it.

It does not reuse `AIAgent`: that loop has no post-action verification and receives no element list. The Claude
planner here produces steps that run through the verified engine path instead.

## Testing

```
run-tests.bat
```

- **Unit tests** (`tests/unit`, 46): the engine (verification, recovery, retries, pause/stop, approvals, interrupted execution, secrets), the server (Origin pinning, auth, versioning, reconnect races, non-blocking requests), the native host with real processes (start, detect, single instance, ownership, port conflicts, crash reporting), specs, the safety classifier, and planner conversion.
- **End-to-end tests** (`tests/e2e/run-e2e.mjs`, 21): real Chromium with the extension loaded via Playwright, a native-host-started backend, and the fixture site in `tests/fixtures/site`. Evidence goes to `test-results/`: screenshots, `e2e-results.json`, and run logs.

`HEADED=1` shows the browser; `ONLY=<text>` runs matching tests. See [TEST_REPORT.md](TEST_REPORT.md) for the
latest verified results.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Popup: "Native host not installed" | Run `install.bat`; check `web-goal-agent-backend/native_host/install_host.py --check` |
| Extension ID differs from `lfejjmliceaahhcnbdlmafhklbehnpab` | The manifest `key` was removed or changed; restore it |
| "port … is in use by another program" | Change `port` in `config.json`, or stop the other program |
| "Version mismatch" | Reload the extension and restart the backend so both are the same version |
| Backend won't start | Popup shows the log tail; full logs: `web-goal-agent-backend/state/backend.log`, `backend-stdout.log`, `native-host.log` |
| "site access is not granted" | **Authorize this site** in the popup (per origin) |
| "Another debugger is attached" | Close DevTools on that tab |
| Run paused with "needs you" | Approve/Reject in the popup, or handle the page dialog/CAPTCHA/login, then **Resume** |
| `ambiguous` failure | Add `within` (dialog, row, region) to the locator; the error lists the candidates |
| Free-text goal disabled | Set `ANTHROPIC_API_KEY` (or `ant auth login`) for the user that runs Chrome, then restart the backend |

## Known limitations

- Cross-origin iframes and canvas-only UIs are not automatable by locators.
- Custom dropdowns (non-`<select>`) need explicit click steps.
- Native JS dialogs are paused for, not answered.
- Branded Google Chrome ignores `--load-extension` since v137, so the E2E suite runs on Playwright's Chromium build. Loading into Chrome is the manual step above.
- The Claude planner's live behaviour was not exercised here (no API credentials on the test machine). Its decision conversion, prompt framing and loop logic are unit-tested with a scripted planner.
- One run at a time, bound to one tab. Chrome shows its "started debugging this browser" bar while a run is attached (that's how trusted input works).

## Files

```
install.bat · uninstall.bat · start-backend.bat · serve-demo-site.bat · run-tests.bat
web-goal-agent-extension/   manifest.json, html/css/js popup, js/service-worker.js,
                            js/lib/{backend-link,executor,cdp,page,store,protocol}.js, js/page/agent-page.js
web-goal-agent-backend/     goal_agent_backend/{server,engine,goals,planner,safety,browser,runlog,config,protocol}.py
                            native_host/{host.py,host.bat,install_host.py}, goals/*.json, config.example.json
tests/                      unit/ (Python), e2e/ (Playwright), fixtures/site/ (demo CRM + edge cases)
```
