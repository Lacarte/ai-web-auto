# Web Goal Agent — Test Report

**Result: 46/46 unit tests and 21/21 end-to-end tests pass.** The full E2E suite passed on three consecutive
runs (2026-10-25). It has not been run on branded Google Chrome or against a real third-party site; see
[Not verified](#not-verified).

| | |
|---|---|
| Date | 2026-10-25 |
| OS | Windows 11 Pro 10.0.26100 |
| Browser | Chromium 156 (Playwright 1.64.1 build, new headless), extension loaded unpacked |
| Backend | Python 3.10.0, websockets 16.0, pydantic 2.12.5 (ai-web-auto `venv`) |
| Reproduce | `run-tests.bat` (evidence written to `test-results/`) |

## Definition of done

| Criterion | Status | Evidence |
|---|---|---|
| Extension loads in Chrome | **Pass (Chromium)** | E2E 1: MV3 service worker, fixed ID `lfejjmliceaahhcnbdlmafhklbehnpab`, popup renders. Branded Chrome: manual "Load unpacked" only, not run here |
| Backend starts automatically through a supported mechanism | **Pass** | E2E 2: native host started it (`started_by=native-host`); cold start ~1.3 s to `/health` |
| Extension and backend communicate reliably | **Pass** | E2E 4–7, 20; unit `test_server` |
| Inspects, navigates and interacts with the authorized site | **Pass (fixture site)** | E2E 8–17 on the local test site. No real target site was specified |
| Dialogs and elements selected with verified, robust locators | **Pass** | E2E 8, 11, 12, 13 |
| Visual feedback reflects actual execution | **Pass** | E2E 8: every dispatched action had a matching overlay highlight, scope boxes logged; E2E 11: overlay in the top layer above a modal `<dialog>`; screenshots |
| Failures handled safely and meaningfully | **Pass** | E2E 13–16, 19, 20; unit `test_engine` recovery cases |
| Goal reached and final state verified | **Pass (fixture site)** | Every succeeded run ends with its success criteria re-checked on the page |
| Automated and real-browser tests pass | **Pass** | 46 unit + 21 E2E; E2E ×3 runs |
| Setup reproducible from the docs | **Pass** | `install.bat` → load unpacked → popup; `run-tests.bat` itself exercised end-to-end |

## End-to-end tests (real browser, final run)

| # | Test | Evidence |
|---|---|---|
| 1 | Extension installs and initializes | ID `lfejjmliceaahhcnbdlmafhklbehnpab`, v0.1.0; `e2e/01-popup.png` |
| 2 | Backend auto-start via native host | connected to v0.1.0, `started_by=native-host` |
| 3 | Running backend detected, no duplicates | 3 concurrent `ensure` → `already_running`, same PID, exactly 1 listener (`netstat`) |
| 4 | Handshake, versioning, correlation | protocol 1; parallel requests matched to replies; protocol 2 → `protocol_mismatch`; wrong token → `bad_token`; extension link unaffected |
| 5 | Reconnect after a dropped connection | auto-reconnect 1055 ms; manual Reconnect 196 ms; same backend PID |
| 6 | Unexpected backend termination | `taskkill /F` → loss detected → native host restarted it (new PID) → reconnected in 5.2 s |
| 7 | Manual restart | PID changed; reconnected |
| 8 | Goal: rename customer | Cookie wall handled by interrupt (`consent=necessary`); Save clicked in **Edit customer**, not the open **Quick note** panel (`saves=1, noteSaves=0`); all 5 steps verified; overlay highlight per action; overlay removed afterwards; `e2e/08-rename-in-progress.png` |
| 9 | Approval — approve | Run paused at `Click button "Delete" in dialog "Delete Alan Turing?"`; nothing deleted before approval; deleted once after; `e2e/09-approval-*.png` |
| 10 | Approval — reject | Run stopped; nothing deleted; row still present |
| 11 | Menu → top-layer `<dialog>` → native select | "Exported 4 customers as Excel"; overlay `:popover-open` while the dialog was `:modal`; `e2e/11-overlay-above-modal-dialog.png` |
| 12 | Edge cases | Shipping saved / Billing untouched; closed-shadow-root field typed; iframe coupon applied; lazy feed scrolled (5 pages) to item 42; late-enabled button clicked once; approval requested for "Subscribe" |
| 13 | Ambiguous identical buttons | Failed `ambiguous` with 2 candidates (Shipping, Billing); nothing clicked; failure screenshot saved |
| 14 | Page-reported validation error | Failed with "Email: Enter a valid email address"; Save attempted exactly once |
| 15 | Obstruction recovery | Diagnosed "covered by div 'Welcome to Acme CRM!…'", waited it out, then clicked |
| 16 | Stale elements | Table re-rendered during the flow; customer saved once |
| 17 | Navigation + extraction | Full-page navigation and back; page library re-injected; extracted `revenue=$1,234,500`, `active=3` |
| 18 | Pause / resume | Paused while waiting; button became clickable at 5 s but no click while paused; exactly one click after Resume |
| 19 | Stop | Stopped 638 ms after Stop; no click afterwards; overlay removed |
| 20 | Interrupted execution | Connection dropped mid-run → `interrupted` → reconnected → page re-checked → goal verified; one click total |
| 21 | ai-web-auto core compatibility | The **unmodified** core `WebSocketServer` drove the extension: `navigate`, `wait_for`, `execute_script`, `click`, `type_text`, `scroll`, `screenshot`, `extract` |

Screenshots are in `test-results/e2e/`. Run logs (JSONL, one per run) and failure screenshots are in `test-results/runs/<date>/`.
`test-results/` is regenerated by every run and is not committed.

## Unit tests (46)

| Area | Tests |
|---|---|
| Engine (`test_engine.py`, 19) | evidence required per step and goal; already-satisfied goal/step does nothing; ambiguity refused without acting; non-repeatable click dispatched once; repeatable steps retried within bounds; transient disabled/obstructed waited out; page error stops without resubmitting; interrupt handler clears an obstruction; leaving the authorized site fails; disconnect mid-action with effect present → recovered without repeating; without effect → human asked, action never re-sent; pause holds actions; stop; approval approve/reject; start validation; secret inputs absent from events and logs |
| Server (`test_server.py`, 7) | `/health` on the WS port (no token); web-page Origin rejected with 403; auth + version negotiation; `/shutdown` requires the token; newest connection wins and a stale close doesn't clobber it; pending commands fail fast on disconnect; slow UI handlers don't block pings; unknown ops rejected |
| Native host (`test_native_host.py`, 6, real processes) | full lifecycle; 4 concurrent `ensure` → exactly one backend; manual backend detected, not stopped/restarted without force; port owned by another program reported; backend crash at startup reported from its exit code with the log tail; unlisted caller refused |
| Specs, safety, redaction (`test_goals_safety.py`, 7) | brittle or unverifiable specs rejected (absolute XPath, click without `expect`, off-origin URLs, no success criteria); input rendering and secrets; `$target` wiring; confirmation classifier (destructive / financial / outward / credential, confirm-in-alertdialog, popup openers exempt); log redaction |
| Planner (`test_planner.py`, 7) | model decision → validated step; nested scopes; click without evidence rejected; "done" needs checkable success; "none" hands over to the human; page content framed as untrusted; claimed-done verified before success (scripted planner) |

## Defects found by testing and fixed

| Found by | Root cause | Fix | Regression coverage |
|---|---|---|---|
| E2E 5–7 | `reconnect()` closed the socket but left status `connected`, so `start()` short-circuited and the link stayed dead | Status follows the socket when it is closed | E2E 5 (manual reconnect), 6, 7 |
| Code review | A replaced socket closing late could stop the new heartbeat and fail its requests (extension), or clear the live connection (core server) | Ignore stale closes in both | unit `test_newest_connection_wins…`; E2E 5, 20 |
| Native host timing | Windows takes ~2 s to refuse a closed loopback port; two probes made a cold start take ~6 s | 0.3 s TCP probe, then HTTP `/health` | cold start now ~2 s; unit lifecycle tests |
| Logs | Port probes made `websockets` log tracebacks | HTTP probes; library logger quietened | clean `backend-stdout.log` |
| E2E 12 | Closed shadow roots hide `activeElement.shadowRoot`, so focus checks failed | Deep active element via `chrome.dom.openOrClosedShadowRoot` and frames | E2E 12 |
| Screenshot review | Highlight boxes animated position (lagged ~8 px); page-level HUD updates erased the step counter | Animate color only; keep the step count from run events | `e2e/08`, `e2e/11` screenshots |
| E2E harness | Tabs with identical URLs → lookup found an older tab | Unique `#t<n>` fragment per tab | all E2E |
| E2E 14, 17 | Test goals had success criteria already true on page load, so the engine correctly did nothing | Fixed the specs; the rule is documented in README and CLAUDE.md | E2E 14, 17 |
| E2E 12 | "Subscribe" correctly triggered the financial approval gate (could be a paid plan) | Kept the safety behaviour; the test approves like a user | E2E 12 asserts the request |

## Not verified

- **Branded Google Chrome.** Chrome 137+ ignores `--load-extension`, so automated runs use Playwright's Chromium 156 build (same engine). Loading into Chrome is the manual step in the README. The native host is registered for Chrome, Chromium, Chrome for Testing, Edge and Brave.
- **A real third-party website.** No specific site was named, so all goals ran on the bundled fixture site, which reproduces the hard cases. A live-site goal needs that site's spec and your logged-in session.
- **Claude planner with live API calls.** No Anthropic credentials on this machine. Decision conversion, prompt framing and the verified loop are unit-tested with a scripted planner; the popup reports the planner as unavailable instead of faking it.
- **Native `alert/confirm/beforeunload` dialogs.** Detected via CDP (the run pauses, then resumes automatically when the dialog closes), but not E2E-tested: Playwright auto-dismisses dialogs.
- **Idle self-shutdown after 30 minutes and genuine MV3 worker suspension.** Implemented. Suspension recovery is exercised by forced connection drops (E2E 5, 20), not by waiting for Chrome to evict the worker.
- **Headed mode.** The final runs were headless; `HEADED=1` runs the same suite visibly.
