# Web-app modules

A **module** commands one web app that doesn't need an extension of its own. The AI drives the app
through the ai-web-auto platform (backend on `ws://localhost:8765` + the AI Web-Auto extension) and
keeps everything it learns about the app in the module, so later orders run fast and the same way
every time. **Each web app has its own module.**

| Use | When |
|---|---|
| **Module** (`@modules/<app>/`) | Commanding an app from Claude or scripts, no separate install |
| Goal spec (`@created-extensions/web-goal-agent`) | A goal started from a popup, with on-page feedback |
| Standalone extension (`@created-extensions/<name>`) | A packaged tool someone installs |

## Layout

```
@modules/
  module_kit.py            shared toolkit (check, preflight, verify, discover, related, new)
  _template/               copied by `module_kit.py new`
  <app>/
    module.json            name (= folder), app URL, origins, settings, entry points
    knowledge/
      map.json             pages: URL, ready_when_any / busy_when_any signals, sections to open, blockers
      locators.json        every element used, registered once (css / relative xpath / text, role, name)
      actions.json         what the app can do: steps, evidence, sensitive, retry, requires_idle
      rules.md             the brain: decision rules and safety gates
      lessons.md           what was learned to work, and what didn't
      discovered/          page snapshots from discover (gitignored)
    <app>_*.py             executors; page scripts use @@key@@ / @@key.text@@ from locators.json
    @downloads/            outputs and reports (gitignored)
```

## Every order goes through preflight

Before an action runs, `module_kit.preflight()` checks and repairs, in order:

1. **Connection** - the AI Web-Auto extension is connected.
2. **Action** - known (in actions.json) and not a draft.
3. **Tab** - an app tab is open and in front (one already on the right page is preferred); a closed
   tab is reopened in a **new** tab, never by replacing the page in one of your tabs. The order is then
   **bound**: every later command goes to that tab on that browser, or is refused (tab closed, another
   browser's extension connected). Switching tabs while an order runs doesn't redirect it.
4. **Page** - the action's page is loaded; otherwise it navigates there.
5. **Sign-in** - a redirect off the app's origins or a password form stops the run for the human.
6. **Ready** - one of the page's `ready_when_any` signals appears; if not, reload once; if still not,
   stop with a page snapshot for diagnosis.
7. **Blockers** - the page's registered `blockers` that are showing (cookie wall, tour, promo) are
   dismissed with their steps; one that stays stops the run.
8. **Section** - the action's `section` is opened with its registered steps (click / navigate / wait_for).
   A click waits until its target is on screen and uncovered; an overlay that stays is named in the error.
9. **Idle** - if the action `requires_idle`, wait (bounded) for the page's `busy_when_any` signals to clear.

A blocker in `map.json`:

```json
"blockers": [{"when": "consent_dialog", "dismiss": [{"click": "consent_reject"}]}]
```

Register the least-consequential way out (Reject optional, Close, Skip) - never a step that accepts
terms, buys, or publishes.

Ready, busy and section signals count only **visible** elements (or rendered text): a spinner or a
closed panel left hidden in the DOM is neither busy nor open.

An **unknown action** is never improvised. Preflight scrapes the page (`discover`), checks what the
**other modules** already know (`related` + live test), and registers a **draft** in actions.json.
The draft holds the snapshot and the transfer candidates. It can't run until someone writes its steps
and evidence, verifies it on the live page, and removes `status: "draft"`.

## Learning across modules

When an action is learned in one module, the other modules are searched for:

- **similar actions**: shared words in name/description (e.g. *download*, *delete*). Their steps,
  evidence, retry and safety rules are a starting point.
- **lessons** that mention the action's words or the UI technology detected on the new page (`hints`:
  radix, react, editor, menu, dialog, switch, option, download, busy). Example: Radix menus belong to
  their trigger; a lost CDP click can fall back to the React handler.
- **locators** whose role+name or selector appear on the new page, plus the locators used by the most
  similar actions.

`try_transfer` then **runs those candidate selectors on the live page** and marks each `works_here`.
Only proven reuse goes into the new module, and only after it is verified there.

## Commands

```
python @modules/module_kit.py new <app> --url https://app.example.com/home   # scaffold from _template
python @modules/module_kit.py preflight <app> [--action NAME]                # check/repair readiness
python @modules/module_kit.py discover <app>                                 # snapshot the open page's controls
python @modules/module_kit.py related <app> --action NAME                    # knowledge from other modules
python @modules/module_kit.py verify <app> [--write]                         # count every locator on the open page
python @modules/module_kit.py check <app>                                    # validate the knowledge offline
```

`preflight`, `discover` and `verify` start the platform backend on `:8765` and wait for the extension.
Stop `runner.bat` first if it is running. They only read the page, except that preflight may reload,
navigate, bring a tab to the front, or click a section's registered open-steps.

## Growing a module (the loop)

1. `new` → open the app, sign in.
2. `discover` the pages you need; register elements in `locators.json` (stable selectors: aria-label,
   role+name, data-*; relative XPath only when needed) and pages in `map.json` with ready/busy signals.
3. Describe each action in `actions.json` with its **evidence**; implement it in an executor using
   `@@key@@` placeholders and call `preflight()` first.
4. `verify --write` on the live page; `check` offline; write tests with a fake server (see
   `tests/test_module_kit.py`, `tests/test_elevenlabs_*.py`).
5. Record what you learned in `lessons.md` and the decision rules in `rules.md`.
