"""Web-app module toolkit: load, validate, verify and grow a module's knowledge.

A module (@modules/<app>/) commands one web app through the ai-web-auto platform
(backend on ws://localhost:8765 + the AI Web-Auto extension). Its knowledge lives
in files the AI reads before acting and updates as it learns:

    module.json              identity, URLs, origins, settings, entry points
    knowledge/map.json       pages and how to recognise them
    knowledge/locators.json  every element the module uses, registered once
    knowledge/actions.json   what the app can do: steps, evidence, safety
    knowledge/rules.md       the decision rules ("brain") and safety gates
    knowledge/lessons.md     what was learned to work, and what didn't

Executors embed locators in their page scripts as @@key@@ (the CSS selector)
or @@key.text@@ (the registered text); Module.js() fills them in, so a UI
change is fixed in locators.json, not in every script.

Before any action runs, preflight() makes sure the app is usable and repairs
what it can: browser connected, app tab open and in front, the action's page
loaded and ready (reload once if not), sign-in detected, the needed section
opened, the app idle when the action requires it, and the action known - an
unknown action gets its page scraped and a draft registered for completion.

    python @modules/module_kit.py check     <module>                 validate offline (no browser)
    python @modules/module_kit.py preflight <module> [--action A]    check/repair readiness for an action
    python @modules/module_kit.py verify    <module> [--write]       count each locator on the open page (read-only)
    python @modules/module_kit.py discover  <module>                 snapshot the open page's controls -> knowledge/discovered/
    python @modules/module_kit.py new       <name> --url URL         scaffold a module from _template
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

MODULES_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MODULES_DIR.parent
TEMPLATE_DIR = MODULES_DIR / "_template"
PLACEHOLDER = re.compile(r"@@([a-z][a-z0-9_]*)(\.text)?@@")
LOCATOR_FIELDS = {"page", "within", "css", "xpath", "text", "role", "name", "description", "state",
                  "fallbacks", "verified", "last_missing", "last_count", "notes"}
ACTION_FIELDS = {"page", "section", "description", "inputs", "preconditions", "steps", "evidence", "uses", "sensitive",
                 "retry", "requires_idle", "implemented_by", "cli", "notes", "status", "requested_at", "discovered",
                 "transfer_candidates"}
PAGE_FIELDS = {"url", "url_pattern", "purpose", "ready_when", "ready_when_any", "busy_when_any", "sections", "blockers",
               "features", "notes"}
STEP_KINDS = ("click", "navigate", "wait_for")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class KnowledgeError(ValueError):
    pass


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise KnowledgeError(f"{path.name}: invalid JSON ({e})") from e


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


@dataclass
class Module:
    dir: Path
    manifest: dict[str, Any]
    map: dict[str, Any]
    locators: dict[str, dict[str, Any]]
    actions: dict[str, dict[str, Any]]
    _locators_doc: dict[str, Any] = field(default_factory=dict, repr=False)
    _actions_doc: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def name(self) -> str:
        return self.manifest.get("name", self.dir.name)

    @property
    def settings(self) -> dict[str, Any]:
        return self.manifest.get("settings", {})

    @property
    def knowledge_dir(self) -> Path:
        return self.dir / "knowledge"

    def css(self, key: str) -> str:
        loc = self._locator(key)
        if not loc.get("css"):
            raise KnowledgeError(f"locator '{key}' has no css")
        return loc["css"]

    def text(self, key: str) -> str:
        loc = self._locator(key)
        if not loc.get("text"):
            raise KnowledgeError(f"locator '{key}' has no text")
        return loc["text"]

    def js(self, script: str) -> str:
        """Fill @@key@@ / @@key.text@@ with JS string literals from the registry."""
        def fill(m: re.Match) -> str:
            value = self.text(m.group(1)) if m.group(2) else self.css(m.group(1))
            return json.dumps(value)
        return PLACEHOLDER.sub(fill, script)

    def _locator(self, key: str) -> dict[str, Any]:
        try:
            return self.locators[key]
        except KeyError:
            raise KnowledgeError(f"unknown locator '{key}' (not in {self.name}/knowledge/locators.json)") from None

    def page_for_url(self, url: str) -> str | None:
        def bare(u: str) -> str:
            return u.split("#")[0].split("?")[0].rstrip("/")

        for name, page in self.map.get("pages", {}).items():
            if page.get("url") and bare(url) == bare(page["url"]):
                return name
            pattern = page.get("url_pattern")
            if pattern:
                regex = "^" + re.sub(r"\\\{[a-z_]+\\\}", r"[^/?#]+", re.escape(pattern)) + r"(?:[?#].*)?$"
                if re.match(regex, url):
                    return name
        return None

    def locators_for_page(self, page: str) -> dict[str, dict[str, Any]]:
        return {k: v for k, v in self.locators.items() if self.page_of(k) == page}

    def page_of(self, key: str, _depth: int = 0) -> str | None:
        loc = self.locators.get(key, {})
        if loc.get("page") or _depth > 10:
            return loc.get("page")
        return self.page_of(loc["within"], _depth + 1) if loc.get("within") else None

    def save_locators(self) -> None:
        doc = dict(self._locators_doc) or {"version": 1}
        doc["locators"] = self.locators
        _write_json(self.knowledge_dir / "locators.json", doc)

    def save_actions(self) -> None:
        doc = dict(self._actions_doc) or {"version": 1}
        doc["actions"] = self.actions
        _write_json(self.knowledge_dir / "actions.json", doc)

    @property
    def default_page(self) -> str | None:
        return self.page_for_url(self.manifest.get("app_url", "")) or next(iter(self.map.get("pages", {})), None)

    def page(self, name: str) -> dict[str, Any]:
        return self.map.get("pages", {}).get(name, {})


def resolve(module: str | Path) -> Path:
    path = Path(module)
    if not path.is_absolute() and not path.exists():
        path = MODULES_DIR / module
    if not (path / "module.json").exists():
        raise KnowledgeError(f"{path} is not a module (no module.json)")
    return path.resolve()


def load(module: str | Path) -> Module:
    d = resolve(module)
    k = d / "knowledge"
    loc_doc = _read_json(k / "locators.json", {"locators": {}})
    act_doc = _read_json(k / "actions.json", {"actions": {}})
    return Module(
        dir=d,
        manifest=_read_json(d / "module.json", {}),
        map=_read_json(k / "map.json", {"pages": {}}),
        locators=loc_doc.get("locators", {}),
        actions=act_doc.get("actions", {}),
        _locators_doc=loc_doc,
        _actions_doc=act_doc,
    )


# ── check: offline validation ────────────────────────────────────────────────

def check(module: Module) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    m = module.manifest
    for key in ("name", "title", "app_url", "origins"):
        if not m.get(key):
            errors.append(f"module.json: '{key}' is required")
    if m.get("name") and m["name"] != module.dir.name:
        errors.append(f"module.json: name '{m['name']}' must match the folder name '{module.dir.name}' "
                      "(other modules find each other by folder)")
    for name, rel in (m.get("entry_points") or {}).items():
        if not (module.dir / rel).exists():
            errors.append(f"module.json: entry point '{name}' -> {rel} does not exist")
    for f in ("rules.md", "lessons.md"):
        if not (module.knowledge_dir / f).exists():
            warnings.append(f"knowledge/{f} is missing")

    pages = module.map.get("pages", {})
    for name, page in pages.items():
        if not page.get("url") and not page.get("url_pattern"):
            errors.append(f"map.json: page '{name}' needs 'url' or 'url_pattern'")
        unknown = set(page) - PAGE_FIELDS
        if unknown:
            warnings.append(f"map.json: page '{name}': unknown fields {sorted(unknown)}")
        signals = [*page.get("ready_when_any", []), *page.get("busy_when_any", [])]

        def steps_ok(where: str, steps: list) -> None:
            for step in steps:
                if not isinstance(step, dict) or len(step) != 1 or next(iter(step)) not in STEP_KINDS:
                    errors.append(f"map.json: {where}: steps are {{click|navigate|wait_for: ...}}")
                elif next(iter(step)) in ("click", "wait_for"):
                    signals.append(next(iter(step.values())))

        for sec_name, sec in (page.get("sections") or {}).items():
            signals += sec.get("ready_when_any", [])
            steps_ok(f"section '{name}.{sec_name}' open", sec.get("open", []))
        for i, blocker in enumerate(page.get("blockers") or []):
            if not isinstance(blocker, dict) or not blocker.get("when") or not blocker.get("dismiss"):
                errors.append(f"map.json: page '{name}' blocker #{i + 1} needs 'when' (a locator) and 'dismiss' (steps)")
                continue
            signals.append(blocker["when"])
            steps_ok(f"page '{name}' blocker '{blocker['when']}' dismiss", blocker["dismiss"])
        for key in signals:
            if key not in module.locators:
                errors.append(f"map.json: page '{name}' signal '{key}' is not in locators.json")
        if page.get("url") and not page.get("ready_when_any"):
            warnings.append(f"map.json: page '{name}' has no ready_when_any - preflight can't tell when it has loaded")

    for key, loc in module.locators.items():
        where = f"locators.json: '{key}'"
        unknown = set(loc) - LOCATOR_FIELDS
        if unknown:
            warnings.append(f"{where}: unknown fields {sorted(unknown)}")
        if not any(loc.get(f) for f in ("css", "xpath", "text")):
            errors.append(f"{where}: needs css, xpath or text")
        if loc.get("within") and loc["within"] not in module.locators:
            errors.append(f"{where}: 'within' refers to unknown locator '{loc['within']}'")
        if loc.get("within") == key:
            errors.append(f"{where}: 'within' refers to itself")
        if loc.get("page") and loc["page"] not in pages:
            errors.append(f"{where}: page '{loc['page']}' is not in map.json")
        if not module.page_of(key):
            warnings.append(f"{where}: no page (set 'page' or 'within')")
        xp = loc.get("xpath") or ""
        if xp.startswith("/html") or xp.startswith("/body"):
            warnings.append(f"{where}: absolute XPath breaks on any layout change; use css/role or a relative './/' XPath")
        for d in ("verified", "last_missing"):
            if loc.get(d) and not DATE.match(str(loc[d])):
                errors.append(f"{where}: '{d}' must be YYYY-MM-DD")
        if loc.get("state") not in (None, "always", "transient"):
            errors.append(f"{where}: state must be 'always' or 'transient'")

    for key, action in module.actions.items():
        where = f"actions.json: '{key}'"
        unknown = set(action) - ACTION_FIELDS
        if unknown:
            warnings.append(f"{where}: unknown fields {sorted(unknown)}")
        if not action.get("description"):
            errors.append(f"{where}: 'description' is required")
        if not action.get("evidence"):
            errors.append(f"{where}: 'evidence' is required (how to know it worked)")
        for used in action.get("uses", []):
            if used not in module.locators:
                errors.append(f"{where}: uses unknown locator '{used}'")
        if action.get("page") and action["page"] not in pages:
            errors.append(f"{where}: page '{action['page']}' is not in map.json")
        if action.get("section") and action["section"] not in (pages.get(action.get("page"), {}).get("sections") or {}):
            errors.append(f"{where}: section '{action['section']}' is not defined on page '{action.get('page')}'")
        if action.get("status") == "draft":
            warnings.append(f"{where}: draft - complete steps/uses/evidence, verify, then remove 'status' before running it")
        impl = action.get("implemented_by", "")
        # "file.function" -> a script in the module; a path with "/" -> any file from the project root
        # (e.g. an extension built from the module's knowledge).
        target = PROJECT_ROOT / impl if "/" in impl else module.dir / (impl.split(".")[0] + ".py")
        if impl and not target.exists():
            errors.append(f"{where}: implemented_by '{impl}' - no such file")

    # Every @@placeholder@@ in the module's scripts must resolve.
    for py in sorted(module.dir.glob("*.py")):
        for m2 in PLACEHOLDER.finditer(py.read_text(encoding="utf-8")):
            key, is_text = m2.group(1), bool(m2.group(2))
            loc = module.locators.get(key)
            if loc is None:
                errors.append(f"{py.name}: @@{key}{m2.group(2) or ''}@@ - unknown locator")
            elif is_text and not loc.get("text"):
                errors.append(f"{py.name}: @@{key}.text@@ - locator has no text")
            elif not is_text and not loc.get("css"):
                errors.append(f"{py.name}: @@{key}@@ - locator has no css")
    return errors, warnings


# ── verify: count every locator on the live page (read-only) ─────────────────

VERIFY_JS = r"""
((locs) => {
  const scopeOf = (key, seen = 0) => {
    const l = locs[key];
    if (!l || !l.within || seen > 10) return [document];
    const parents = [];
    for (const s of scopeOf(l.within, seen + 1)) parents.push(...matches(locs[l.within], s));
    return parents;
  };
  const matches = (l, root) => {
    if (!l) return [];
    if (l.css) { try { return [...root.querySelectorAll(l.css)]; } catch (e) { return []; } }
    if (l.xpath) {
      const r = document.evaluate(l.xpath, root, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
      return Array.from({length: r.snapshotLength}, (_, i) => r.snapshotItem(i));
    }
    return [];
  };
  const visible = el => { if (el.nodeType !== 1) return false; const r = el.getBoundingClientRect();
    const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const out = {};
  for (const key of Object.keys(locs)) {
    const l = locs[key];
    const scopes = scopeOf(key);
    const found = new Set();
    for (const s of scopes) for (const el of matches(l, s)) found.add(el);
    // css/xpath + text: only the matched elements that contain the text count (the word elsewhere on
    // the page proves nothing). Text alone: the rendered text of the scope.
    const bySelector = !!(l.css || l.xpath);
    const hits = bySelector && l.text ? [...found].filter(el => (el.innerText || el.textContent || '').includes(l.text)) : [...found];
    const textFound = l.text && !bySelector ? scopes.some(s => ((s === document ? document.body : s).innerText || '').includes(l.text)) : null;
    const fallbacks = (l.fallbacks || []).map(css => { try { return document.querySelectorAll(css).length; } catch (e) { return -1; } });
    out[key] = {count: hits.length, visible: hits.filter(visible).length, text_found: textFound, fallback_counts: fallbacks};
  }
  return JSON.stringify({url: location.href, title: document.title, results: out});
})(__LOCS__)
"""


async def _run_script(server, script: str) -> Any:
    from ai_web_auto_backend.core.models import Command
    resp = await server.send_command(Command(action="execute_script", params={"script": script}))
    if resp.status != "success":  # ResponseStatus is a str enum
        raise RuntimeError(resp.error or "page script failed")
    result = resp.data.extracted_data.get("result")
    if isinstance(result, dict) and "_error" in result:
        raise RuntimeError(result["_error"])
    return json.loads(result) if isinstance(result, str) else result


async def verify(module: Module, server, page: str | None = None, write: bool = False) -> dict[str, Any]:
    """Count each registered locator on the open page. Never clicks or types."""
    state = await _run_script(server, "JSON.stringify({url: location.href})")
    url = state["url"]
    page = page or module.page_for_url(url)
    if not page:
        raise RuntimeError(f"the open page ({url}) matches no page in {module.name}/knowledge/map.json; "
                           "open the app page first or pass --page")
    locs = module.locators_for_page(page)
    payload = {k: {f: v[f] for f in ("css", "xpath", "text", "within", "fallbacks") if v.get(f)} for k, v in locs.items()}
    # 'within' parents may live on the page too; include them so scopes resolve.
    for v in list(payload.values()):
        parent = v.get("within")
        while parent and parent not in payload:
            p = module.locators[parent]
            payload[parent] = {f: p[f] for f in ("css", "xpath", "text", "within") if p.get(f)}
            parent = p.get("within")
    data = await _run_script(server, VERIFY_JS.replace("__LOCS__", json.dumps(payload)))
    today = date.today().isoformat()
    rows = []
    for key in locs:
        r = data["results"].get(key, {})
        found = r.get("count", 0) > 0 or r.get("text_found") is True
        transient = module.locators[key].get("state") == "transient"
        status = "ok" if found else ("not_visible_now" if transient else "missing")
        rows.append({"key": key, "status": status, "count": r.get("count", 0), "visible": r.get("visible", 0),
                     "text_found": r.get("text_found"), "fallback_counts": r.get("fallback_counts", [])})
        if write:
            loc = module.locators[key]
            if status == "ok":
                loc["verified"] = today
                loc["last_count"] = r.get("count", 0)
                loc.pop("last_missing", None)
            elif status == "missing":
                loc["last_missing"] = today
    if write:
        module.save_locators()
    return {"module": module.name, "page": page, "url": url, "checked_at": datetime.now().isoformat(timespec="seconds"),
            "ok": sum(r["status"] == "ok" for r in rows), "missing": [r["key"] for r in rows if r["status"] == "missing"],
            "rows": rows}


# ── discover: snapshot the open page's controls for the AI to register ──────

DISCOVER_JS = r"""
(() => {
  const visible = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const clean = s => (s || '').replace(/\s+/g, ' ').trim().slice(0, 80);
  const q = s => s.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  const name = el => clean(el.getAttribute('aria-label') ||
    (el.labels && el.labels[0] && el.labels[0].innerText) || el.getAttribute('title') ||
    el.getAttribute('placeholder') || el.innerText || el.value);
  const roleOf = el => el.getAttribute('role') || ({A: 'link', BUTTON: 'button', SELECT: 'combobox', TEXTAREA: 'textbox',
    SUMMARY: 'button'})[el.tagName] || (el.tagName === 'INPUT' ? ({checkbox: 'checkbox', radio: 'radio', button: 'button',
    submit: 'button'})[el.type] || 'textbox' : '') || (el.isContentEditable ? 'textbox' : '');
  const dataAttr = el => [...el.attributes].find(a => /^data-(testid|test-id|test|qa|cy|agent-id)$/.test(a.name));
  const suggest = el => {
    const tag = el.tagName.toLowerCase();
    const d = dataAttr(el);
    if (d) return `[${d.name}="${q(d.value)}"]`;
    if (el.id && !/\d{3,}|:r[0-9a-z]+:|radix|^[a-f0-9-]{16,}$/i.test(el.id)) return `#${CSS.escape(el.id)}`;
    if (el.getAttribute('aria-label')) return `${tag}[aria-label="${q(el.getAttribute('aria-label'))}"]`;
    if (el.getAttribute('name')) return `${tag}[name="${q(el.getAttribute('name'))}"]`;
    if (el.getAttribute('role')) return `${tag}[role="${el.getAttribute('role')}"]`;
    return null;
  };
  const sel = 'a[href],button,input,select,textarea,summary,[role],[contenteditable="true"],[tabindex]:not([tabindex="-1"])';
  const elements = [];
  for (const el of document.querySelectorAll(sel)) {
    if (elements.length >= 250) break;
    if (!visible(el)) continue;
    const role = roleOf(el);
    if (!role || ['presentation', 'none', 'generic'].includes(role)) continue;
    const d = dataAttr(el);
    elements.push({role, name: name(el), tag: el.tagName.toLowerCase(), css: suggest(el),
      id: el.id || undefined, data: d ? `${d.name}=${d.value}` : undefined,
      href: el.tagName === 'A' ? el.getAttribute('href') : undefined,
      disabled: el.disabled || el.getAttribute('aria-disabled') === 'true' || undefined,
      in_dialog: clean(el.closest('[role="dialog"],[role="alertdialog"],dialog')?.getAttribute('aria-label') ||
        el.closest('[role="dialog"],[role="alertdialog"],dialog')?.querySelector('h1,h2,h3')?.innerText) || undefined});
  }
  const dialogs = [...document.querySelectorAll('[role="dialog"],[role="alertdialog"],dialog[open]')].filter(visible)
    .map(el => clean(el.getAttribute('aria-label') || el.querySelector('h1,h2,h3')?.innerText));
  const headings = [...document.querySelectorAll('h1,h2,h3')].filter(visible).map(h => clean(h.innerText)).slice(0, 15);
  // UI technology hints: they let lessons learned on other apps be matched to this one.
  const has = s => { try { return !!document.querySelector(s); } catch (e) { return false; } };
  const hints = [];
  if (has('[data-radix-collection-item],[data-radix-popper-content-wrapper]') || has('[aria-haspopup][data-state]')) hints.push('radix');
  if ([...document.querySelectorAll('button,a,div')].slice(0, 400).some(el => Object.keys(el).some(k => k.startsWith('__reactProps$')))) hints.push('react');
  if (has('.ProseMirror,.tiptap')) hints.push('tiptap', 'editor');
  if (has('[contenteditable="true"]')) hints.push('editor');
  if (has('[role="menu"],[aria-haspopup="menu"]')) hints.push('menu');
  if (has('[role="dialog"],[role="alertdialog"],dialog')) hints.push('dialog');
  if (has('[role="switch"]')) hints.push('switch');
  if (has('[role="option"],[role="listbox"]')) hints.push('option');
  if (has('a[download],[aria-label*="ownload" i]')) hints.push('download');
  if (has('[aria-busy="true"],[data-loading="true"]')) hints.push('busy');
  return JSON.stringify({url: location.href, title: document.title, dialogs, headings, hints: [...new Set(hints)], elements});
})()
"""


async def discover(module: Module, server, page: str | None = None) -> tuple[Path, dict[str, Any]]:
    """Record the open page's interactive elements; nothing on the page is changed."""
    snap = await _run_script(server, DISCOVER_JS)
    page = page or module.page_for_url(snap["url"]) or "unmapped"
    registered = {loc.get("css") for loc in module.locators.values()} | {
        fb for loc in module.locators.values() for fb in loc.get("fallbacks", [])}
    for el in snap["elements"]:
        el["registered"] = bool(el.get("css")) and el["css"] in registered
    snap.update(module=module.name, page=page, discovered_at=datetime.now().isoformat(timespec="seconds"),
                new_candidates=sum(1 for el in snap["elements"] if el["css"] and not el["registered"]))
    out_dir = module.knowledge_dir / "discovered"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{page}-{datetime.now():%Y%m%d-%H%M%S}.json"
    _write_json(path, snap)
    return path, snap


# ── preflight: check and repair readiness before any action ─────────────────

GENERIC_STATE_JS = r"""JSON.stringify({url: location.href, ready_state: document.readyState,
  password_field: [...document.querySelectorAll('input[type="password"]')].some(el => {
    const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; })})"""

POINT_JS = r"""
((css, withinCss) => {
  const roots = withinCss ? [...document.querySelectorAll(withinCss)] : [document];
  for (const root of roots) for (const el of root.querySelectorAll(css)) {
    el.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});
    const r = el.getBoundingClientRect();
    const x = Math.round(r.left + r.width / 2), y = Math.round(r.top + r.height / 2);
    if (r.width && r.height && x >= 0 && y >= 0 && x < innerWidth && y < innerHeight) {
      const hit = document.elementFromPoint(x, y);
      if (el.contains(hit)) return JSON.stringify({x, y, covered: false});
      const over = hit && (hit.closest('[role="dialog"],[role="alertdialog"],dialog') || hit);
      const cls = over && typeof over.className === 'string' ? over.className.trim().split(/\s+/)[0] : '';
      return JSON.stringify({x, y, covered: true,
        by: over ? (over.id ? '#' + over.id : over.tagName.toLowerCase() + (cls ? '.' + cls : '')) : null});
    }
  }
  return JSON.stringify(null);
})(__ARGS__)
"""


@dataclass
class Preflight:
    """Outcome of preflight(): ok means the action may run now."""
    module: str
    action: str | None
    page: str | None = None
    ok: bool = False
    checks: list[dict[str, str]] = field(default_factory=list)
    draft_registered: str | None = None
    snapshot: str | None = None
    tab_id: int | None = None  # the tab this order is bound to (every later command goes there)

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.checks.append({"check": name, "status": status, "detail": detail})

    @property
    def fixes(self) -> list[str]:
        return [c["detail"] for c in self.checks if c["status"] == "fixed"]

    @property
    def problem(self) -> str:
        failed = [c for c in self.checks if c["status"] == "fail"]
        return f"{failed[-1]['check']}: {failed[-1]['detail']}" if failed else ""

    def summary(self) -> str:
        icon = {"ok": "ok   ", "fixed": "FIXED", "warn": "warn ", "fail": "FAIL "}
        lines = [f"  {icon.get(c['status'], c['status'])} {c['check']:<15} {c['detail']}" for c in self.checks]
        verdict = "READY" if self.ok else f"NOT READY - {self.problem}"
        return "\n".join([f"Preflight {self.module}{' / ' + self.action if self.action else ''}: {verdict}", *lines])


def _probe_payload(module: Module, keys: list[str]) -> dict[str, dict[str, Any]]:
    payload: dict[str, dict[str, Any]] = {}
    for key in keys:
        k: str | None = key
        while k and k not in payload:
            loc = module._locator(k)
            payload[k] = {f: loc[f] for f in ("css", "xpath", "text", "within", "fallbacks") if loc.get(f)}
            k = loc.get("within")
    return payload


async def probe(module: Module, server, keys: list[str]) -> dict[str, bool]:
    """Which of these registered locators are showing on the open page right now.

    Signals must be visible: a hidden spinner or a closed panel left in the DOM is not busy or open.
    """
    if not keys:
        return {}
    data = await _run_script(server, VERIFY_JS.replace("__LOCS__", json.dumps(_probe_payload(module, keys))))
    out = {}
    for key in keys:
        r = data["results"].get(key, {})
        out[key] = r.get("visible", 0) > 0 or r.get("text_found") is True
    return out


async def _command(server, action: str, params: dict[str, Any] | None = None, timeout_ms: int = 30000):
    from ai_web_auto_backend.core.models import Command
    return await server.send_command(Command(action=action, params=params or {}, timeout=timeout_ms))


async def _app_tabs(module: Module, server) -> list[dict[str, Any]] | None:
    """Open tabs on the app's origins; None when the platform can't list tabs."""
    try:
        resp = await _command(server, "list_tabs")
    except Exception:
        return None
    tabs = getattr(resp.data, "tabs", None)
    if resp.status != "success" or tabs is None:
        return None
    return [t for t in tabs if _origin(t.get("url", "")) in module.manifest.get("origins", [])]


def _origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else ""


async def _poll(fn, timeout: float, interval: float = 0.5):
    """Call fn until it returns a truthy value or the timeout passes; return the last value."""
    deadline = asyncio.get_running_loop().time() + timeout
    value = await fn()
    while not value and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(interval)
        value = await fn()
    return value


def human_delay(settings: dict[str, Any], rng=None) -> float:
    """Seconds to wait before the next page load, paced like a person: uniform in settings.pause_seconds, and now
    and then (long_pause.chance) a longer read (long_pause.seconds). Polite pacing, not a way around a block:
    a robot check still stops the order for the human."""
    import random
    rng = rng or random
    lo, hi = settings.get("pause_seconds", [3.0, 8.0])
    long = settings.get("long_pause") or {}
    if long and rng.random() < long.get("chance", 0.0):
        return rng.uniform(*long.get("seconds", [12.0, 35.0]))
    return rng.uniform(lo, hi)


async def human_pause(settings: dict[str, Any]) -> float:
    secs = human_delay(settings)
    await asyncio.sleep(secs)
    return secs


SEARCH_BOX_JS = r"""JSON.stringify(((boxSel, btnSel) => {
  const box = document.querySelector(boxSel), btn = document.querySelector(btnSel);
  if (!box || !btn) return {err: 'search box or search button not on this page'};
  const r = btn.getBoundingClientRect(), x = Math.round(r.x + r.width / 2), y = Math.round(r.y + r.height / 2);
  const hit = document.elementFromPoint(x, y);
  return {value: box.value, x, y, size: r.width * r.height,
          covered: !(hit && (hit === btn || btn.contains(hit) || hit.contains(btn))),
          hit: hit ? hit.tagName + '.' + (hit.className || '') : null, page: document.visibilityState};
})(__ARGS__))"""

BOX_IN_VIEW_JS = r"""JSON.stringify((sel => { const b = document.querySelector(sel); if (!b) return null;
  const r = b.getBoundingClientRect();
  return {in_view: r.width > 0 && r.top >= 0 && r.bottom <= innerHeight, top: Math.round(r.top), w: innerWidth, h: innerHeight};
})(__ARGS__))"""

LINK_JS = r"""JSON.stringify(((sel, scroll) => {
  const a = [...document.querySelectorAll(sel)].find(e => e.href && e.getAttribute('aria-disabled') !== 'true');
  if (!a) return null;
  const q = a.getBoundingClientRect();
  if (scroll) { if (q.top < 0 || q.bottom > innerHeight) a.scrollIntoView({block: 'center', behavior: 'instant'}); return {scrolled: true}; }
  const x = Math.round(q.x + q.width / 2), y = Math.round(q.y + q.height / 2), hit = document.elementFromPoint(x, y);
  return {href: a.href, x, y, covered: !(hit && (hit === a || a.contains(hit))), hit: hit ? hit.tagName + '.' + (hit.className || '') : null};
})(__ARGS__))"""

IN_VIEW_JS = r"""((sel) => { const a = document.querySelector(sel);
  if (!a) return innerHeight + scrollY >= document.documentElement.scrollHeight - 5;
  const r = a.getBoundingClientRect(); return r.top > 0 && r.top < innerHeight * 0.85; })(__ARGS__)"""


async def think(lo: float = 0.4, hi: float = 1.3) -> None:
    """A short random beat before typing or clicking."""
    import random
    await asyncio.sleep(random.uniform(lo, hi))


async def bring_to_front(server) -> None:
    """Trusted input is dropped in a hidden tab: bring the bound tab to the front (activate_tab is pinned to it)."""
    try:
        await _command(server, "activate_tab", {"query": ""}, timeout_ms=8000)
        await asyncio.sleep(0.4)
    except Exception:
        pass


async def scroll_to(module: Module, server, key: str, max_bursts: int = 30, rng=None) -> dict[str, Any] | None:
    """Bring a registered element (e.g. the header search box) into view the way a person would: quick mouse-wheel
    flicks back up the page. Falls back to an instant scrollIntoView (no wheel command, or still out of view)."""
    import random
    rng = rng or random
    sel = json.dumps(module.css(key))
    st = None
    for _ in range(max_bursts):
        st = await _run_script(server, BOX_IN_VIEW_JS.replace("__ARGS__", sel))
        if not st:
            return st
        if st["in_view"]:
            # eBay's header is sticky: scrolled down it is position:fixed and slides in from above - wait until it
            # stops moving, or type_cdp clicks where the box was a moment ago (live 2026-10-28)
            await asyncio.sleep(0.6)
            again = await _run_script(server, BOX_IN_VIEW_JS.replace("__ARGS__", sel))
            if again and again["in_view"] and abs(again["top"] - st["top"]) <= 1:
                return again
            continue
        x, y = int(st["w"] * rng.uniform(0.35, 0.65)), int(st["h"] * rng.uniform(0.35, 0.7))
        up = st["top"] < 0
        resp = await _command(server, "wheel", {"x": x, "y": y, "notches": rng.randint(6, 12),
                                                "delta_y": (-1 if up else 1) * rng.randint(100, 125),
                                                "notch_delay_ms": rng.randint(25, 70)}, timeout_ms=15000)
        if resp.status != "success":
            break
        await asyncio.sleep(rng.uniform(0.15, 0.5))
    await _run_script(server, f"JSON.stringify((b => {{ if (b) b.scrollIntoView({{block: 'center', behavior: 'instant'}}); return 1; }})(document.querySelector({sel})))")
    await asyncio.sleep(0.3)
    return await _run_script(server, BOX_IN_VIEW_JS.replace("__ARGS__", sel))


async def type_and_submit(module: Module, server, query: str, box_key: str = "search_box",
                          submit_key: str = "search_submit", settings: dict[str, Any] | None = None) -> str:
    """Search like a person: click the search box, clear it, type the query (CDP insertText, varied speed), check the
    box holds exactly the query and the button is visible and uncovered, then click the button (trusted CDP click).
    Raises instead of clicking blind. Returns the URL before the click (to tell the results page apart)."""
    import random
    settings = settings or module.settings
    await bring_to_front(server)
    before = (await _run_script(server, "JSON.stringify(location.href)"))
    lo, hi = settings.get("type_ms_per_char", [45, 140])
    args = json.dumps(module.css(box_key)) + ", " + json.dumps(module.css(submit_key))
    for attempt in range(2):
        # the box sits in the page header: after reading down a results page it is out of view, and type_cdp's click
        # on it would land outside the window (live 2026-10-28: the box kept the previous query)
        await scroll_to(module, server, box_key)
        await think()
        r = await _command(server, "type_cdp", {"text": query, "xpath": module._locator(box_key)["xpath"], "clear": True,
                                                "delay_per_char": random.randint(lo, hi)}, timeout_ms=30000)
        if r.status != "success":
            raise RuntimeError(f"typing into the search box failed: {r.error}")
        pt = await _run_script(server, SEARCH_BOX_JS.replace("__ARGS__", args))
        if pt.get("err"):
            raise RuntimeError(pt["err"])
        if (pt.get("value") or "").strip() == query.strip():
            break
        if attempt == 0:
            await bring_to_front(server)  # once more, from the top
    else:
        raise RuntimeError(f"the search box holds {pt.get('value')!r}, not {query!r} (page {pt.get('page')}) - not submitting")
    if pt["covered"] or not pt["size"]:
        raise RuntimeError(f"the search button is covered ({pt.get('hit')}) or not visible - not clicking blind")
    await think(0.3, 1.0)
    await server.click_cdp(pt["x"], pt["y"])
    return before


async def click_link(module: Module, server, key: str) -> dict[str, Any] | None:
    """Trusted click on a registered link (e.g. Next page) after checking it is uncovered. Returns {href, clicked}
    (clicked False when covered - the caller may open href instead), or None when there is no such link."""
    args = json.dumps(module.css(key))
    if not await _run_script(server, LINK_JS.replace("__ARGS__", args + ", true")):
        return None
    await think(0.4, 1.0)
    pt = await _run_script(server, LINK_JS.replace("__ARGS__", args + ", false"))  # measured after the scroll settled
    if not pt:
        return None
    if pt["covered"]:
        return {"href": pt["href"], "clicked": False, "hit": pt.get("hit")}
    await server.click_cdp(pt["x"], pt["y"])
    return {"href": pt["href"], "clicked": True}


async def read_down(module: Module, server, until_key: str | None = None, bursts: tuple[int, int] = (8, 40)) -> dict[str, Any]:
    """Read down a page with mouse-wheel flicks until until_key (e.g. the Next link) is in view, the page ends, or
    a random number of flicks in `bursts` (people don't read every result)."""
    import random
    stop = IN_VIEW_JS.replace("__ARGS__", json.dumps(module.css(until_key))) if until_key else None
    return await human_scroll(server, stop, max_bursts=random.randint(*bursts))


def wheel_burst(rng=None) -> dict[str, Any]:
    """One flick of a mouse wheel as a person does it: a few notches of ~100 px, quick notches, then a pause to
    read; now and then a notch back up."""
    import random
    rng = rng or random
    if rng.random() < 0.12:
        return {"notches": 1, "delta_y": -rng.randint(90, 120), "notch_delay_ms": rng.randint(40, 110),
                "pause": rng.uniform(0.6, 1.8)}
    return {"notches": rng.randint(3, 8), "delta_y": rng.randint(90, 125), "notch_delay_ms": rng.randint(35, 110),
            "pause": rng.uniform(0.4, 1.9)}


async def human_scroll(server, stop_js: str | None = None, max_bursts: int = 40, rng=None) -> dict[str, Any]:
    """Scroll down with trusted mouse-wheel events (the tab must be in front) in person-like bursts, the pointer
    resting over the page's middle, until stop_js returns true or the page end. Falls back to window.scrollBy on an
    extension without the wheel command. Returns {bursts, via, end}."""
    import random
    rng = rng or random
    vp = await _run_script(server, "JSON.stringify({w: innerWidth, h: innerHeight})")
    via, bursts, last_y, still = "wheel", 0, None, 0
    for bursts in range(1, max_bursts + 1):
        b = wheel_burst(rng)
        x = int(vp["w"] * rng.uniform(0.35, 0.65))
        y = int(vp["h"] * rng.uniform(0.35, 0.7))
        resp = None
        if via == "wheel":
            resp = await _command(server, "wheel", {"x": x, "y": y, **{k: b[k] for k in ("notches", "delta_y", "notch_delay_ms")}},
                                  timeout_ms=15000)
            if resp.status != "success":
                via = "script"  # older extension: no wheel command
        if via == "script":
            await _run_script(server, f"(() => {{ scrollBy(0, {b['notches'] * b['delta_y']}); return 1; }})()")
        await asyncio.sleep(b["pause"])
        if stop_js and await _run_script(server, stop_js):
            return {"bursts": bursts, "via": via, "end": "target in view"}
        pos = await _run_script(server, "JSON.stringify(scrollY)")
        still = still + 1 if pos == last_y and b["delta_y"] > 0 else 0
        if still >= 2:
            return {"bursts": bursts, "via": via, "end": "page end"}
        last_y = pos
    return {"bursts": bursts, "via": via, "end": "max bursts"}


async def _any_present(module: Module, server, keys: list[str]) -> list[str]:
    present = await probe(module, server, keys)
    return [k for k, v in present.items() if v]


async def open_section(module: Module, server, page: str, section: str, timeout: float = 15.0,
                       poll: float = 0.5) -> tuple[bool, str]:
    """Run a section's registered open-steps unless it is already showing."""
    spec = (module.page(page).get("sections") or {}).get(section)
    if not spec:
        return False, f"section '{section}' is not defined on page '{page}'"
    ready_keys = spec.get("ready_when_any", [])
    if ready_keys and await _any_present(module, server, ready_keys):
        return True, "already open"
    ok, detail = await run_steps(module, server, spec.get("open", []), timeout, poll)
    if not ok:
        return False, f"{detail} (opening section '{section}')"
    if ready_keys and not await _poll(lambda: _any_present(module, server, ready_keys), timeout, poll):
        return False, f"section '{section}' did not show any of {ready_keys}"
    return True, "opened"


async def click_registered(module: Module, server, key: str, timeout: float = 15.0,
                           poll: float = 0.5) -> tuple[bool, str]:
    """CDP-click a registered locator once it is on screen and nothing covers it.

    A covering overlay that goes away by itself (a tour, a toast) is waited out; one that stays is
    reported by name so it can be registered as a page blocker.
    """
    loc = module._locator(key)
    parent = module.locators.get(loc.get("within", ""), {}).get("css")
    covered: dict[str, Any] = {}

    async def point():
        pos = await _run_script(server, POINT_JS.replace("__ARGS__", json.dumps(loc.get("css", "")) + ", " + json.dumps(parent)))
        covered.clear()
        if pos and pos.get("covered"):
            covered["by"] = pos.get("by") or "another element"
            return None
        return pos

    pos = await _poll(point, timeout, poll)
    if not pos:
        if covered:
            return False, (f"'{key}' is covered by {covered['by']} - close it, or register it as a blocker "
                           "of this page in map.json")
        return False, f"could not find '{key}' on screen"
    resp = await server.click_cdp(pos["x"], pos["y"])
    if resp.status != "success":
        return False, f"click on '{key}' failed: {resp.error}"
    return True, "clicked"


async def run_steps(module: Module, server, steps: list[dict[str, str]], timeout: float = 15.0,
                    poll: float = 0.5) -> tuple[bool, str]:
    """Run registered {click|navigate|wait_for} steps in order; stop at the first that fails."""
    for step in steps:
        kind, value = next(iter(step.items()))
        if kind == "navigate":
            await server.navigate(value)
        elif kind == "click":
            ok, detail = await click_registered(module, server, value, timeout, poll)
            if not ok:
                return False, detail
        elif kind == "wait_for":
            if not await _poll(lambda: _any_present(module, server, [value]), timeout, poll):
                return False, f"'{value}' did not appear"
    return True, "done"


async def clear_blockers(module: Module, server, page: str | None, timeout: float = 10.0,
                         poll: float = 0.5) -> list[tuple[str, bool, str]]:
    """Dismiss the page's registered blockers (cookie wall, tour, promo) that are showing now."""
    results = []
    for blocker in (module.page(page).get("blockers") or []) if page else []:
        when = blocker["when"]
        if not await _any_present(module, server, [when]):
            continue
        ok, detail = await run_steps(module, server, blocker["dismiss"], timeout, poll)
        if ok and not await _poll(lambda: _not(_any_present(module, server, [when])), timeout, poll):
            ok, detail = False, "still showing after its dismiss steps"
        results.append((when, ok, "dismissed" if ok else detail))
    return results


def register_draft_action(module: Module, name: str, page: str | None, snapshot: str | None,
                          transfer: dict[str, Any] | None = None) -> dict[str, Any]:
    """Record a requested-but-unknown action so it can be learned, without making it runnable."""
    draft = {
        "status": "draft",
        "page": page,
        "description": f"Requested but not yet learned: {name}",
        "steps": [],
        "uses": [],
        "evidence": "TODO - the observable page state that proves this action worked",
        "requested_at": date.today().isoformat(),
        "discovered": snapshot,
        "transfer_candidates": transfer or None,
        "notes": "Learn it: start from transfer_candidates (knowledge from other modules; locators marked "
                 "works_here were found on this page), find the remaining controls in the snapshot, register them in "
                 "locators.json, write steps + evidence here, implement it, verify on the live page, then remove 'status'.",
    }
    module.actions[name] = {k: v for k, v in draft.items() if v is not None}
    module.save_actions()
    return module.actions[name]


# ── cross-module learning: reuse what other apps' modules already know ──────

_STOP = {"the", "and", "for", "with", "from", "into", "this", "that", "then", "when", "than", "only", "not", "are",
         "was", "its", "one", "each", "per", "via", "page", "track", "button", "click", "todo", "requested", "learned",
         "yet", "but", "use", "used", "uses", "new", "all", "any", "out", "off", "app"}


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "").lower().replace("_", " "))
    out = set()
    for w in words:
        if len(w) < 3 or w in _STOP:
            continue
        for suffix in ("ing", "ed", "es", "s"):
            if w.endswith(suffix) and len(w) - len(suffix) >= 4:
                w = w[: -len(suffix)]
                break
        if w.endswith("e") and len(w) > 4:  # delete/deleted/deleting -> delet
            w = w[:-1]
        out.add(w)
    return out


def list_modules(modules_dir: Path = MODULES_DIR) -> list[Path]:
    return sorted(d for d in modules_dir.iterdir()
                  if d.is_dir() and not d.name.startswith(("_", ".")) and (d / "module.json").exists())


def _lessons(module: Module) -> list[str]:
    path = module.knowledge_dir / "lessons.md"
    if not path.exists():
        return []
    items, current = [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("- "):
            if current:
                items.append(" ".join(current))
            current = [line[2:].strip()]
        elif current and line.startswith("  "):
            current.append(line.strip())
        elif current:
            items.append(" ".join(current))
            current = []
    if current:
        items.append(" ".join(current))
    return items


def related_knowledge(module: Module, action: str, snapshot: dict[str, Any] | None = None,
                      modules_dir: Path = MODULES_DIR, limit: int = 6) -> dict[str, Any]:
    """What other modules know that may apply to learning `action` in this module.

    Similar actions (by words in name/description), lessons that mention the action's words or the
    page's UI technology (snapshot hints), and locators whose role+name or css match an element of
    the new page. These are candidates: try_transfer() checks which actually work on the live page.
    """
    words = _tokens(action)
    hints = set((snapshot or {}).get("hints", []))
    elements = (snapshot or {}).get("elements", [])
    by_role_name = {(e.get("role"), (e.get("name") or "").lower()) for e in elements if e.get("name")}
    css_here = {e.get("css") for e in elements if e.get("css")}
    actions, lessons, locators = [], [], []
    for other_dir in list_modules(modules_dir):
        if other_dir.resolve() == module.dir.resolve():
            continue
        other = load(other_dir)
        for name, spec in other.actions.items():
            if spec.get("status") == "draft":
                continue
            overlap = words & _tokens(f"{name} {spec.get('description', '')}")
            if overlap:
                actions.append({"module": other.name, "action": name, "matched": sorted(overlap),
                                "score": round(len(overlap) / max(len(words), 1), 2),
                                "description": spec.get("description"), "steps": spec.get("steps", []),
                                "evidence": spec.get("evidence"), "retry": spec.get("retry"),
                                "sensitive": spec.get("sensitive"), "uses": spec.get("uses", [])})
        for text in _lessons(other):
            lt = _tokens(text)
            overlap = (words | hints) & lt
            if overlap:
                lessons.append({"module": other.name, "matched": sorted(overlap), "lesson": text,
                                "score": len(overlap)})
        for key, loc in other.locators.items():
            same_name = loc.get("role") and loc.get("name") and (loc["role"], loc["name"].lower()) in by_role_name
            same_css = loc.get("css") in css_here
            if same_name or same_css:
                locators.append({"module": other.name, "locator": key, "css": loc.get("css"), "text": loc.get("text"),
                                 "role": loc.get("role"), "name": loc.get("name"),
                                 "why": "same role and name on this page" if same_name else "same selector on this page",
                                 "description": loc.get("description")})
    actions.sort(key=lambda a: -a["score"])
    lessons.sort(key=lambda lesson: -lesson["score"])
    # Locators used by the most similar actions are worth trying on the new page as well.
    seen = {(c["module"], c["locator"]) for c in locators}
    for a in actions[:2]:
        other = load(modules_dir / a["module"])
        for key in a["uses"]:
            loc = other.locators.get(key, {})
            if (a["module"], key) not in seen and (loc.get("css") or loc.get("text")):
                locators.append({"module": a["module"], "locator": key, "css": loc.get("css"), "text": loc.get("text"),
                                 "role": loc.get("role"), "name": loc.get("name"),
                                 "why": f"used by similar action {a['module']}.{a['action']}",
                                 "description": loc.get("description")})
                seen.add((a["module"], key))
    return {"for_action": action, "page_hints": sorted(hints), "actions": actions[:limit],
            "lessons": lessons[:limit], "locators": locators[: limit * 4]}


async def try_transfer(module: Module, server, related: dict[str, Any], modules_dir: Path = MODULES_DIR) -> dict[str, Any]:
    """Run candidate locators from other modules on the live page and record which work here."""
    payload: dict[str, dict[str, Any]] = {}
    for cand in related.get("locators", []):
        other = load(modules_dir / cand["module"])
        key = cand["locator"]
        chain = key
        while chain:  # include the 'within' parents so scoped selectors resolve
            loc = other.locators.get(chain, {})
            payload[f"{cand['module']}:{chain}"] = {
                **{f: loc[f] for f in ("css", "xpath", "text") if loc.get(f)},
                **({"within": f"{cand['module']}:{loc['within']}"} if loc.get("within") else {})}
            chain = loc.get("within")
    if payload:
        data = await _run_script(server, VERIFY_JS.replace("__LOCS__", json.dumps(payload)))
        for cand in related.get("locators", []):
            r = data["results"].get(f"{cand['module']}:{cand['locator']}", {})
            cand["count_here"] = r.get("count", 0)
            cand["visible_here"] = r.get("visible", 0)
            # Showing on this page now, not merely matching something hidden in the DOM.
            cand["works_here"] = r.get("visible", 0) > 0 or r.get("text_found") is True
    related["locators"].sort(key=lambda c: not c.get("works_here"))
    return related


async def preflight(module: Module, server, action: str | None = None, page: str | None = None, *,
                    wait_idle: bool | None = None, ready_timeout: float = 20.0, idle_timeout: float = 120.0,
                    step_timeout: float = 15.0, learn: bool = True, poll: float = 0.5,
                    modules_dir: Path = MODULES_DIR) -> Preflight:
    """Make sure the app is ready for `action`, repairing what can be repaired.

    Order: connection -> action known -> app tab (the order is bound to it) -> page -> sign-in ->
    ready (reload once) -> blockers -> section -> idle. Unknown actions are learned as drafts and never run.
    Once bound, every command the order sends goes to that tab on that browser, or is refused.
    """
    pf = Preflight(module=module.name, action=action)

    # 1. Connection to the browser.
    session = getattr(server, "session", None)
    if session is not None and not getattr(session, "connected", True):
        pf.add("connection", "fail", "the AI Web-Auto extension is not connected (load it and start the backend)")
        return pf
    pf.add("connection", "ok", "browser extension connected")
    if hasattr(server, "unbind"):
        server.unbind()
        if "tab_id" not in getattr(server, "capabilities", {"tab_id"}):
            pf.add("binding", "warn", "the connected AI Web-Auto extension is an older version that can't pin "
                   "commands to a tab - reload it in chrome://extensions; until then commands follow the active tab")
    try:
        await _prepare(pf, module, server, action, page, wait_idle=wait_idle, ready_timeout=ready_timeout,
                       idle_timeout=idle_timeout, step_timeout=step_timeout, learn=learn, poll=poll,
                       modules_dir=modules_dir)
    except (RuntimeError, ConnectionError) as e:  # a refused command: bound tab closed, browser switched
        pf.ok = False
        pf.add("target", "fail", str(e))
    return pf


def _bind(server, pf: Preflight, tab_id: int | None) -> None:
    pf.tab_id = tab_id
    if hasattr(server, "bind"):
        server.bind(tab_id)


def _pick_tab(module: Module, tabs: list[dict[str, Any]], target: str | None) -> dict[str, Any]:
    """The app tab to use: one already on the target page first, then the one in front."""
    return min(tabs, key=lambda t: (not (target and module.page_for_url(t.get("url", "")) == target),
                                    not t.get("active")))


async def _prepare(pf: Preflight, module: Module, server, action: str | None, page: str | None, *,
                   wait_idle: bool | None, ready_timeout: float, idle_timeout: float, step_timeout: float,
                   learn: bool, poll: float, modules_dir: Path) -> Preflight:
    # 2. Is the action known?
    spec = module.actions.get(action) if action else None
    if spec and spec.get("status") == "draft":
        pf.add("action", "fail", f"'{action}' is still a draft - complete and verify it in actions.json first")
        return pf
    if action and spec is None:
        pf.add("action", "warn", f"'{action}' is not a known action - opening the app to learn it")
    elif spec:
        pf.add("action", "ok", spec.get("description", "")[:90])
    target = page or (spec or {}).get("page") or module.default_page
    pf.page = target
    target_url = module.page(target).get("url") if target else None

    # 3. App tab open and in front (reopen it if it was closed); bind the order to it.
    tabs = await _app_tabs(module, server)
    if tabs is None:
        _bind(server, pf, None)
        pf.add("tab", "warn", "the platform can't list tabs; checking the current tab only")
    elif not tabs:
        if not target_url:
            pf.add("tab", "fail", f"no tab is open on {module.manifest.get('origins')} and page '{target}' has no URL to open")
            return pf
        # A new tab: reusing the last-targeted tab would replace whatever page the user has there.
        resp = await _command(server, "navigate", {"url": target_url, "new_tab": True}, timeout_ms=60000)
        if resp.status != "success":
            pf.add("tab", "fail", f"could not open {target_url}: {resp.error}")
            return pf
        _bind(server, pf, getattr(resp.data, "tabId", None))
        pf.add("tab", "fixed", f"no app tab was open - opened {target_url} in a new tab")
    else:
        tab = _pick_tab(module, tabs, target)
        _bind(server, pf, tab.get("id"))
        if not tab.get("active"):
            host = urlsplit(tab["url"]).netloc
            resp = await _command(server, "activate_tab", {"query": host})  # bound: exactly this tab
            pf.add("tab", "fixed" if resp.status == "success" else "warn", f"brought the {host} tab to the front")
        else:
            pf.add("tab", "ok", f"{len(tabs)} app tab(s) open; using tab {tab.get('id')}")

    # 4. Right page.
    state = await _run_script(server, GENERIC_STATE_JS)
    current = module.page_for_url(state["url"])
    if target and current != target:
        if not target_url:
            pf.add("page", "fail", f"on '{current or state['url']}', need '{target}' which has no fixed URL")
            return pf
        await server.navigate(target_url)
        state = await _run_script(server, GENERIC_STATE_JS)
        pf.add("page", "fixed", f"navigated to {target} ({target_url})")
    else:
        pf.add("page", "ok", f"{target} ({state['url']})")

    # 5. Signed in? A redirect off the app or a visible password form means a human must sign in.
    if _origin(state["url"]) not in module.manifest.get("origins", []):
        pf.add("login", "fail", f"the app redirected to {_origin(state['url'])} - sign in there, then retry")
        return pf

    # 6. Page ready (reload once if not).
    ready_keys = module.page(target).get("ready_when_any", []) if target else []
    if ready_keys:
        found = await _poll(lambda: _any_present(module, server, ready_keys), ready_timeout, poll)
        if not found:
            await server.navigate(state["url"])
            pf.add("reload", "fixed", "page was not ready - reloaded it")
            found = await _poll(lambda: _any_present(module, server, ready_keys), ready_timeout, poll)
        if not found:
            state = await _run_script(server, GENERIC_STATE_JS)
            if state.get("password_field"):
                pf.add("login", "fail", "a sign-in form is showing - sign in, then retry")
            else:
                path, _ = await discover(module, server, target)
                pf.snapshot = str(path)
                pf.add("ready", "fail", f"none of {ready_keys} appeared after a reload; page snapshot: {path}")
            return pf
        pf.add("ready", "ok", f"found {', '.join(found)}")
    else:
        pf.add("ready", "warn", f"page '{target}' declares no ready_when_any signals")

    # 6b. Registered blockers in the way (cookie wall, tour, promo): dismiss them.
    for when, cleared, detail in await clear_blockers(module, server, target, step_timeout, poll):
        pf.add("blocker", "fixed" if cleared else "fail", f"{when}: {detail}")
        if not cleared:
            return pf

    # 7. Unknown action: scrape the page, check what other modules know, register a draft to be learned.
    if action and spec is None:
        if learn:
            path, snap = await discover(module, server, target)
            pf.snapshot = str(path)
            transfer = await try_transfer(module, server, related_knowledge(module, action, snap, modules_dir), modules_dir)
            working = [f"{c['module']}.{c['locator']}" for c in transfer["locators"] if c.get("works_here")]
            pf.add("reuse", "ok" if transfer["actions"] or transfer["lessons"] or working else "warn",
                   f"{len(transfer['actions'])} similar action(s), {len(transfer['lessons'])} lesson(s) from other modules; "
                   f"{len(working)} of their locators work here" + (f" ({', '.join(working[:4])})" if working else ""))
            register_draft_action(module, action, target, str(path.relative_to(module.dir)), transfer)
            pf.draft_registered = action
            pf.add("learn", "fail", f"registered draft '{action}' in actions.json with a snapshot of {len(snap['elements'])} "
                   f"controls ({path.name}); complete and verify it before running")
        else:
            pf.add("learn", "fail", f"'{action}' is not a known action")
        return pf

    # 8. Section needed by the action.
    if spec and spec.get("section"):
        opened, detail = await open_section(module, server, target, spec["section"], step_timeout, poll)
        pf.add("section", "fixed" if opened and detail == "opened" else ("ok" if opened else "fail"),
               f"{spec['section']}: {detail}")
        if not opened:
            return pf

    # 9. Busy / idle.
    busy_keys = module.page(target).get("busy_when_any", []) if target else []
    need_idle = (spec or {}).get("requires_idle", False) if wait_idle is None else wait_idle
    if busy_keys:
        busy = await _any_present(module, server, busy_keys)
        if busy and need_idle:
            start = asyncio.get_running_loop().time()
            idle = await _poll(lambda: _not(_any_present(module, server, busy_keys)), idle_timeout, max(poll, 2.0 * poll / 0.5))
            if not idle:
                pf.add("idle", "fail", f"still busy after {idle_timeout:g}s ({', '.join(busy)}); not starting")
                return pf
            pf.add("idle", "fixed", f"waited {asyncio.get_running_loop().time() - start:.0f}s for the app to finish ({', '.join(busy)})")
        elif busy:
            pf.add("idle", "warn", f"app is busy ({', '.join(busy)}); this action doesn't need it idle")
        else:
            pf.add("idle", "ok", "app is idle")
    pf.ok = True
    return pf


async def _not(awaitable) -> bool:
    return not await awaitable


# ── new: scaffold a module ───────────────────────────────────────────────────

def scaffold(name: str, url: str) -> Path:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{1,40}", name):
        raise KnowledgeError("module name: letters, digits, '-' or '_' (2-41 chars, starting with a letter)")
    target = MODULES_DIR / name
    if target.exists():
        raise KnowledgeError(f"{target} already exists")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise KnowledgeError(f"not an http(s) URL: {url}")
    shutil.copytree(TEMPLATE_DIR, target, ignore=shutil.ignore_patterns("__pycache__"))
    manifest = _read_json(target / "module.json", {})
    manifest.update(name=name, title=name, app_url=url, origins=[f"{parts.scheme}://{parts.netloc}"],
                    updated=date.today().isoformat())
    _write_json(target / "module.json", manifest)
    site_map = _read_json(target / "knowledge" / "map.json", {"pages": {}})
    site_map["pages"] = {"home": {"url": url, "purpose": "Entry page - describe what it is for",
                                  "ready_when": "describe what proves the page has loaded"}}
    _write_json(target / "knowledge" / "map.json", site_map)
    for md in ("rules.md", "lessons.md"):
        p = target / "knowledge" / md
        p.write_text(p.read_text(encoding="utf-8").replace("<App>", name), encoding="utf-8")
    return target


# ── CLI ──────────────────────────────────────────────────────────────────────

async def _with_browser(fn):
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    import warnings
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    from ai_web_auto_backend.automation_controller import BrowserAgent
    agent = BrowserAgent()
    print("Waiting for the AI Web-Auto extension to connect on ws://localhost:8765 ...")
    await agent.connect(timeout=60)
    try:
        return await fn(agent.server)
    finally:
        await agent.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="module_kit", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check", help="validate a module's knowledge offline")
    p.add_argument("module")
    p = sub.add_parser("preflight", help="check and repair readiness (tab, page, sign-in, idle, known action)")
    p.add_argument("module")
    p.add_argument("--action", help="action name from actions.json; unknown names are learned as drafts")
    p.add_argument("--page", help="page name from map.json (default: the action's page)")
    p.add_argument("--no-learn", action="store_true", help="don't scrape/register unknown actions")
    p = sub.add_parser("related", help="what other modules know that may apply to an action (offline)")
    p.add_argument("module")
    p.add_argument("--action", required=True)
    p = sub.add_parser("verify", help="count every registered locator on the open page (read-only)")
    p.add_argument("module")
    p.add_argument("--page", help="page name from map.json (default: detected from the URL)")
    p.add_argument("--write", action="store_true", help="record verified/last_missing dates in locators.json")
    p = sub.add_parser("discover", help="snapshot the open page's controls into knowledge/discovered/")
    p.add_argument("module")
    p.add_argument("--page", help="page name to file the snapshot under")
    p = sub.add_parser("new", help="scaffold a module from _template")
    p.add_argument("name")
    p.add_argument("--url", required=True)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    try:
        if args.cmd == "new":
            path = scaffold(args.name, args.url)
            print(f"Created {path}. Next: open {args.url}, then run discover and register what you need.")
            return 0
        module = load(args.module)
        if args.cmd == "check":
            errors, warnings = check(module)
            for w in warnings:
                print(f"  warning: {w}")
            for e in errors:
                print(f"  ERROR:   {e}")
            print(f"{module.name}: {len(module.locators)} locators, {len(module.actions)} actions, "
                  f"{len(module.map.get('pages', {}))} pages - {len(errors)} errors, {len(warnings)} warnings")
            return 1 if errors else 0
        if args.cmd == "related":
            rel = related_knowledge(module, args.action)
            for a in rel["actions"]:
                print(f"  action  {a['module']}.{a['action']:<24} matched {a['matched']} - {a['description']}")
            for lesson in rel["lessons"]:
                print(f"  lesson  {lesson['module']}: {lesson['lesson'][:140]}")
            for c in rel["locators"]:
                print(f"  locator {c['module']}.{c['locator']:<24} {c['css'] or c['text']}  ({c['why']})")
            if not any(rel[k] for k in ("actions", "lessons", "locators")):
                print("  nothing related in other modules")
            return 0
        if args.cmd == "preflight":
            pf = asyncio.run(_with_browser(lambda s: preflight(module, s, args.action, args.page,
                                                               learn=not args.no_learn)))
            print(pf.summary())
            return 0 if pf.ok else 1
        if args.cmd == "verify":
            report = asyncio.run(_with_browser(lambda s: verify(module, s, args.page, args.write)))
            for r in report["rows"]:
                print(f"  {r['status']:<16} {r['key']:<28} count={r['count']} visible={r['visible']}"
                      + (f" text={r['text_found']}" if r["text_found"] is not None else ""))
            print(f"{report['page']}: {report['ok']}/{len(report['rows'])} ok; missing: {report['missing'] or 'none'}")
            return 1 if report["missing"] else 0
        if args.cmd == "discover":
            path, snap = asyncio.run(_with_browser(lambda s: discover(module, s, args.page)))
            print(f"{len(snap['elements'])} controls on {snap['url']} ({snap['new_candidates']} not yet registered)")
            print(f"Saved {path}")
            return 0
    except KnowledgeError as e:
        print(f"ERROR: {e}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
