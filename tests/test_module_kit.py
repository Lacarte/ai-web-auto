"""module_kit: registry validation, preflight repairs, learning unknown actions across modules."""

import asyncio
import json
import re
import shutil
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "@modules"))
warnings.filterwarnings("ignore", category=DeprecationWarning)
import module_kit as mk  # noqa: E402

APP = "https://app.test"


def write_module(root: Path, name: str, *, pages=None, locators=None, actions=None, lessons="", py=None) -> Path:
    d = root / name
    (d / "knowledge").mkdir(parents=True)
    (d / "module.json").write_text(json.dumps({"name": name, "title": name, "app_url": f"{APP}/home",
                                               "origins": [APP]}), encoding="utf-8")
    (d / "knowledge" / "map.json").write_text(json.dumps({"pages": pages or {}}), encoding="utf-8")
    (d / "knowledge" / "locators.json").write_text(json.dumps({"locators": locators or {}}), encoding="utf-8")
    (d / "knowledge" / "actions.json").write_text(json.dumps({"actions": actions or {}}), encoding="utf-8")
    (d / "knowledge" / "rules.md").write_text("# rules\n", encoding="utf-8")
    (d / "knowledge" / "lessons.md").write_text(lessons or "# lessons\n", encoding="utf-8")
    for fname, src in (py or {}).items():
        (d / fname).write_text(src, encoding="utf-8")
    return d


HOME = {"home": {"url": f"{APP}/home", "ready_when_any": ["list"], "busy_when_any": ["spinner"],
                 "sections": {"settings": {"ready_when_any": ["settings_panel"], "open": [{"click": "settings_tab"}]}}}}
LOCS = {
    "list": {"page": "home", "css": "ul.items"},
    "spinner": {"page": "home", "css": ".spinner", "state": "transient"},
    "settings_tab": {"page": "home", "role": "tab", "name": "Settings", "css": "[role=tab][name=settings]"},
    "settings_panel": {"page": "home", "css": "#settings", "state": "transient"},
    "open_menu": {"page": "home", "role": "menu", "css": "[role=\"menu\"][data-state=\"open\"]", "state": "transient"},
}
ACTIONS = {
    "list_items": {"page": "home", "description": "Read the items", "evidence": "items parsed", "uses": ["list"]},
    "start_job": {"page": "home", "description": "Start a job", "evidence": "job row appears",
                  "requires_idle": True, "uses": ["list"]},
    "change_setting": {"page": "home", "section": "settings", "description": "Change a setting",
                       "evidence": "setting saved", "uses": ["settings_panel"]},
}


class FakeBrowser:
    """Simulates the platform: tabs, URL, which locator css are present, sign-in, busy."""

    def __init__(self, url=f"{APP}/home", present=("ul.items", "[role=tab][name=settings]"), tabs=None):
        self.url = url
        self.present = set(present)
        self.hidden = set()              # in the DOM but not showing (hidden spinner, closed panel)
        self.tabs = tabs if tabs is not None else [{"id": 1, "url": url, "active": True}]
        self.password = False
        self.busy_polls = 0              # .spinner disappears after this many probes
        self.ready_after_reload = None   # css that appears only after a navigate/reload
        self.redirect_to = None          # navigate lands here instead (e.g. a login domain)
        self.overlay = None              # visible css that covers every control except overlay_buttons
        self.overlay_buttons = set()
        self.on_click = {"[role=tab][name=settings]": lambda: self.present.add("#settings")}
        self.on_navigate = None
        self._pointed = None
        self.log = []
        self.sent = []                   # (action, tab_id) as the extension receives them
        self.session = SimpleNamespace(connected=True)
        self.capabilities = {"tab_id"}
        self.bound_tab = None

    # The server's binding (core WebSocketServer.bind) and the extension's tab pin, simulated.
    def bind(self, tab_id):
        self.bound_tab = tab_id

    def unbind(self):
        self.bound_tab = None

    def _resp(self, result=None, **data):
        return SimpleNamespace(status="success", error=None,
                               data=SimpleNamespace(extracted_data={"result": result}, **data))

    async def send_command(self, command, timeout=None):
        action = command.action.value if hasattr(command.action, "value") else command.action
        if self.bound_tab is not None:
            command.params.setdefault("tab_id", self.bound_tab)
        tab_id = command.params.get("tab_id")
        self.log.append(action)
        self.sent.append((action, tab_id))
        if tab_id is not None and not any(t["id"] == tab_id for t in self.tabs):
            return SimpleNamespace(status="failure", error=f"Bound tab {tab_id} is closed",
                                   data=SimpleNamespace(extracted_data={}))
        if action == "list_tabs":
            return self._resp(tabs=self.tabs)
        if action == "navigate":
            await self.navigate(command.params["url"], new_tab=command.params.get("new_tab", False))
            return self._resp(None, tabId=self.tabs[-1]["id"] if command.params.get("new_tab") else tab_id)
        if action == "activate_tab":
            for t in self.tabs:
                t["active"] = t["id"] == tab_id if tab_id is not None else command.params["query"] in t["url"]
            return self._resp(True)
        script = command.params.get("script", "")
        if "ready_state" in script:
            return self._resp(json.dumps({"url": self.url, "ready_state": "complete", "password_field": self.password}))
        if "UI technology hints" in script or "hints: [...new Set(hints)]" in script:
            return self._resp(json.dumps({"url": self.url, "title": "t", "dialogs": [], "headings": [],
                                          "hints": ["menu", "radix"],
                                          "elements": [{"role": "button", "name": "Download", "tag": "button",
                                                        "css": "button[aria-label=\"Download\"]"}]}))
        if "scopeOf" in script:
            payload = json.loads(script[script.rindex("})(") + 3: script.rindex(")")])
            if ".spinner" in json.dumps(payload) and self.busy_polls > 0:
                self.busy_polls -= 1
                if self.busy_polls == 0:
                    self.present.discard(".spinner")
            results = {k: {"count": int(v.get("css") in self.present | self.hidden),
                           "visible": int(v.get("css") in self.present),
                           "text_found": None, "fallback_counts": []} for k, v in payload.items()}
            return self._resp(json.dumps({"url": self.url, "title": "t", "results": results}))
        if "covered" in script:
            css = json.loads("[" + script[script.rindex("})(") + 3: script.rindex(")")] + "]")[0]
            if css not in self.present:
                return self._resp(json.dumps(None))
            self._pointed = css
            covered = self.overlay in self.present and css not in self.overlay_buttons
            return self._resp(json.dumps({"x": 5, "y": 6, "covered": covered, "by": self.overlay if covered else None}))
        if "location.href" in script:
            return self._resp(json.dumps({"url": self.url}))
        return self._resp(None)

    async def navigate(self, url, new_tab=False):
        self.log.append(f"navigate:{url}")
        self.url = self.redirect_to or url
        if new_tab or not self.tabs:
            for t in self.tabs:
                t["active"] = False
            self.tabs.append({"id": max((t["id"] for t in self.tabs), default=1) + 1, "url": self.url, "active": True})
        if self.ready_after_reload:
            self.present.add(self.ready_after_reload)
        if self.on_navigate:
            self.on_navigate()
        return SimpleNamespace(status="success", error=None)

    async def click_cdp(self, x, y):
        self.log.append("click")
        self.on_click.get(self._pointed, lambda: None)()
        return SimpleNamespace(status="success", error=None)


def run(coro):
    return asyncio.run(coro)


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="mk-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def module(self, name="app", **kw):
        return mk.load(write_module(self.root, name, pages=kw.pop("pages", HOME), locators=kw.pop("locators", LOCS),
                                    actions=kw.pop("actions", ACTIONS), **kw))

    def preflight(self, browser, action=None, **kw):
        m = kw.pop("module", None) or self.module()
        return m, run(mk.preflight(m, browser, action, ready_timeout=0.2, idle_timeout=0.3, step_timeout=0.2,
                                   poll=0.01, modules_dir=self.root, **kw))


class TestRegistry(TmpCase):
    def test_elevenlabs_knowledge_is_valid(self):
        errors, warnings_ = mk.check(mk.load(ROOT / "@modules" / "elevenLabs"))
        self.assertEqual(errors, [])

    def test_check_catches_broken_knowledge(self):
        m = self.module(
            locators={**LOCS, "orphan": {"within": "ghost", "css": "a"},
                      "abs": {"page": "home", "xpath": "/html/body/div[2]/button"}},
            actions={**ACTIONS, "bad": {"page": "home", "description": "x", "evidence": "y", "uses": ["nope"]},
                     "draft": {"status": "draft", "description": "d", "evidence": "TODO"}},
            py={"runner.py": "SCRIPT = 'document.querySelector(@@missing@@)'"})
        errors, warns = mk.check(m)
        text = "\n".join(errors + warns)
        for expected in ("unknown locator 'ghost'", "uses unknown locator 'nope'", "@@missing@@ - unknown locator",
                         "absolute XPath", "draft"):
            self.assertIn(expected, text)

    def test_implemented_by_is_a_module_script_or_a_project_path(self):
        def errors_for(impl):
            acts = {"a": {"page": "home", "description": "x", "evidence": "y", "uses": ["list"], "implemented_by": impl}}
            return [e for e in mk.check(self.module(actions=acts, py={"runner.py": "x = 1"}))[0] if "implemented_by" in e]
        shutil.rmtree(self.root / "app", ignore_errors=True)
        self.assertEqual(errors_for("runner.main"), [])
        shutil.rmtree(self.root / "app", ignore_errors=True)
        self.assertEqual(errors_for("@modules/module_kit.py"), [])         # outside the module, from the project root
        shutil.rmtree(self.root / "app", ignore_errors=True)
        self.assertEqual(len(errors_for("@created-extensions/nope/x.js")), 1)
        shutil.rmtree(self.root / "app", ignore_errors=True)
        self.assertEqual(len(errors_for("missing.main")), 1)

    def test_check_validates_blockers(self):
        pages = {"home": {**HOME["home"], "blockers": [{"when": "ghost_wall", "dismiss": [{"click": "list"}]},
                                                       {"when": "list"}, {"when": "list", "dismiss": [{"tap": "list"}]}]}}
        errors = "\n".join(mk.check(self.module(pages=pages))[0])
        self.assertIn("'ghost_wall' is not in locators.json", errors)
        self.assertIn("blocker #2 needs 'when'", errors)
        self.assertIn("blocker 'list' dismiss: steps are", errors)

    def test_folder_name_must_match(self):
        d = write_module(self.root, "folder")
        manifest = json.loads((d / "module.json").read_text())
        (d / "module.json").write_text(json.dumps({**manifest, "name": "other"}))
        self.assertTrue(any("must match the folder" in e for e in mk.check(mk.load(d))[0]))

    def test_placeholders_render_registered_values(self):
        m = mk.load(ROOT / "@modules" / "elevenLabs")
        out = m.js("document.querySelector(@@download_button@@).textContent === @@mp3_menu_item.text@@")
        self.assertEqual(out, 'document.querySelector("button[aria-label=\\"Download\\"]").textContent === "MP3"')
        with self.assertRaises(mk.KnowledgeError):
            m.js("@@no_such_key@@")

    def test_scaffold_produces_a_valid_module(self):
        d = mk.MODULES_DIR / "zzTestScaffold"
        try:
            path = mk.scaffold("zzTestScaffold", "https://example.org/start")
            m = mk.load(path)
            self.assertEqual(mk.check(m)[0], [])
            self.assertEqual(m.page_for_url("https://example.org/start?x=1"), "home")
            self.assertIn("zzTestScaffold", (path / "knowledge" / "rules.md").read_text())
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestPreflight(TmpCase):
    def test_ready_app_passes(self):
        _, pf = self.preflight(FakeBrowser(), "list_items")
        self.assertTrue(pf.ok, pf.summary())
        self.assertEqual(pf.fixes, [])

    def test_closed_tab_is_reopened(self):
        b = FakeBrowser(url="about:blank", tabs=[])
        _, pf = self.preflight(b, "list_items")
        self.assertTrue(pf.ok, pf.summary())
        self.assertIn(f"navigate:{APP}/home", b.log)
        self.assertTrue(any("opened" in f for f in pf.fixes))

    def test_background_tab_is_brought_to_front(self):
        b = FakeBrowser(tabs=[{"id": 1, "url": f"{APP}/home", "active": False}])
        _, pf = self.preflight(b, "list_items")
        self.assertTrue(pf.ok)
        self.assertIn("activate_tab", b.log)

    def test_wrong_page_navigates(self):
        b = FakeBrowser(url=f"{APP}/elsewhere")
        _, pf = self.preflight(b, "list_items")
        self.assertTrue(pf.ok, pf.summary())
        self.assertTrue(any("navigated to home" in f for f in pf.fixes))

    def test_not_ready_page_is_reloaded_once(self):
        b = FakeBrowser(present=())
        b.ready_after_reload = "ul.items"
        _, pf = self.preflight(b, "list_items")
        self.assertTrue(pf.ok, pf.summary())
        self.assertTrue(any("reloaded" in f for f in pf.fixes))

    def test_sign_in_form_stops_for_the_human(self):
        b = FakeBrowser(present=())
        b.password = True
        _, pf = self.preflight(b, "list_items")
        self.assertFalse(pf.ok)
        self.assertIn("sign in", pf.problem)

    def test_redirect_off_the_app_stops_for_the_human(self):
        b = FakeBrowser(url=f"{APP}/elsewhere")
        b.redirect_to = "https://login.example.net/auth"
        _, pf = self.preflight(b, "list_items")
        self.assertFalse(pf.ok)
        self.assertIn("login", pf.problem)

    def test_unavailable_section_reports_with_a_snapshot(self):
        b = FakeBrowser(present=())
        _, pf = self.preflight(b, "list_items")
        self.assertFalse(pf.ok)
        self.assertIn("none of ['list'] appeared", pf.problem)
        self.assertTrue(Path(pf.snapshot).exists())

    def test_busy_app_is_waited_for_when_the_action_needs_idle(self):
        b = FakeBrowser(present=("ul.items", ".spinner"))
        b.busy_polls = 3
        _, pf = self.preflight(b, "start_job")
        self.assertTrue(pf.ok, pf.summary())
        self.assertTrue(any("waited" in f for f in pf.fixes))

    def test_app_that_stays_busy_is_not_acted_on(self):
        b = FakeBrowser(present=("ul.items", ".spinner"))
        _, pf = self.preflight(b, "start_job")
        self.assertFalse(pf.ok)
        self.assertIn("still busy", pf.problem)

    def test_busy_is_only_a_warning_when_idle_is_not_required(self):
        _, pf = self.preflight(FakeBrowser(present=("ul.items", ".spinner")), "list_items")
        self.assertTrue(pf.ok)
        self.assertTrue(any(c["check"] == "idle" and c["status"] == "warn" for c in pf.checks))

    def test_hidden_signals_do_not_count(self):
        b = FakeBrowser()
        b.hidden = {".spinner", "#settings"}   # spinner left in the DOM after loading; panel closed
        _, pf = self.preflight(b, "start_job")
        self.assertTrue(pf.ok, pf.summary())
        self.assertTrue(any(c["check"] == "idle" and c["status"] == "ok" for c in pf.checks))
        _, pf = self.preflight(b, "change_setting", module=mk.load(self.root / "app"))
        self.assertIn("click", b.log)          # a hidden panel is not "already open"
        self.assertTrue(any("settings: opened" in f for f in pf.fixes))

    def test_section_is_opened_with_its_registered_steps(self):
        b = FakeBrowser()
        _, pf = self.preflight(b, "change_setting")
        self.assertTrue(pf.ok, pf.summary())
        self.assertIn("click", b.log)
        self.assertTrue(any("settings: opened" in f for f in pf.fixes))

    def consent_module(self):
        pages = {"home": {**HOME["home"], "blockers": [{"when": "consent", "dismiss": [{"click": "consent_reject"}]}]}}
        return self.module(pages=pages, locators={**LOCS, "consent": {"page": "home", "css": "#consent", "state": "transient"},
                                                  "consent_reject": {"within": "consent", "css": "#consentReject"}})

    def consent_wall(self, closes=True):
        b = FakeBrowser(present=("ul.items", "[role=tab][name=settings]", "#consent", "#consentReject"))
        b.overlay, b.overlay_buttons = "#consent", {"#consentReject"}
        if closes:
            b.on_click["#consentReject"] = lambda: b.present.difference_update({"#consent", "#consentReject"})
        return b

    def test_registered_blocker_is_dismissed_before_the_section(self):
        _, pf = self.preflight(self.consent_wall(), "change_setting", module=self.consent_module())
        self.assertTrue(pf.ok, pf.summary())
        self.assertEqual(pf.fixes[:2], ["consent: dismissed", "settings: opened"])

    def test_blocker_that_stays_stops_the_order(self):
        _, pf = self.preflight(self.consent_wall(closes=False), "change_setting", module=self.consent_module())
        self.assertFalse(pf.ok)
        self.assertIn("consent: still showing", pf.problem)

    def test_unregistered_overlay_is_named_and_not_clicked_through(self):
        b = self.consent_wall()
        _, pf = self.preflight(b, "change_setting")
        self.assertFalse(pf.ok)
        self.assertIn("covered by #consent", pf.problem)
        self.assertNotIn("click", b.log)

    def test_order_is_bound_to_the_app_tab(self):
        b = FakeBrowser(tabs=[{"id": 7, "url": "https://mail.example/inbox", "active": True},
                              {"id": 3, "url": f"{APP}/home", "active": False}])
        _, pf = self.preflight(b, "list_items")
        self.assertTrue(pf.ok, pf.summary())
        self.assertEqual(pf.tab_id, 3)
        after_listing = b.sent[b.sent.index(("list_tabs", None)) + 1:]
        self.assertTrue(after_listing)
        self.assertEqual({tab for _, tab in after_listing}, {3})   # nothing reached the user's mail tab

    def test_reopening_opens_a_new_tab_instead_of_reusing_the_users_tab(self):
        b = FakeBrowser(url="https://mail.example/inbox", tabs=[{"id": 7, "url": "https://mail.example/inbox", "active": True}])
        _, pf = self.preflight(b, "list_items")
        self.assertTrue(pf.ok, pf.summary())
        self.assertEqual(pf.tab_id, 8)
        self.assertEqual(b.tabs[0]["url"], "https://mail.example/inbox")
        self.assertIn(("navigate", None), b.sent)                  # opened before binding, as a new tab

    def test_tab_already_on_the_target_page_is_preferred(self):
        b = FakeBrowser(tabs=[{"id": 4, "url": f"{APP}/other", "active": True},
                              {"id": 5, "url": f"{APP}/home", "active": False}])
        _, pf = self.preflight(b, "list_items")
        self.assertTrue(pf.ok, pf.summary())
        self.assertEqual(pf.tab_id, 5)
        self.assertTrue(b.tabs[1]["active"])

    def test_closed_bound_tab_stops_the_order(self):
        b = FakeBrowser(url=f"{APP}/elsewhere", tabs=[{"id": 1, "url": f"{APP}/elsewhere", "active": True}])
        b.on_navigate = b.tabs.clear                               # the user closes the tab mid-order
        _, pf = self.preflight(b, "list_items")
        self.assertFalse(pf.ok)
        self.assertEqual(pf.problem, "target: Bound tab 1 is closed")

    def test_page_url_with_a_query_is_recognised(self):
        m = self.module(pages={"home": {**HOME["home"], "url": f"{APP}/home?tab=music"}})
        b = FakeBrowser(url=f"{APP}/home?tab=music", tabs=[{"id": 1, "url": f"{APP}/home?tab=music", "active": True}])
        _, pf = self.preflight(b, "list_items", module=m)
        self.assertTrue(pf.ok, pf.summary())
        self.assertEqual(pf.fixes, [])                             # no needless re-navigation

    def test_section_whose_click_does_nothing_is_reported(self):
        b = FakeBrowser()
        b.on_click = {}                                            # the tab click doesn't open the panel
        _, pf = self.preflight(b, "change_setting")
        self.assertFalse(pf.ok)
        self.assertIn("did not show any of ['settings_panel']", pf.problem)

    def test_each_order_rebinds(self):
        b = FakeBrowser()
        _, first = self.preflight(b, "list_items")
        b.tabs[:] = [{"id": 2, "url": f"{APP}/home", "active": True}]   # tab 1 closed, the app reopened in 2
        _, second = self.preflight(b, "list_items", module=mk.load(self.root / "app"))
        self.assertEqual((first.tab_id, second.tab_id), (1, 2))
        self.assertTrue(second.ok, second.summary())

    def test_old_extension_is_flagged(self):
        b = FakeBrowser()
        b.capabilities = set()
        _, pf = self.preflight(b, "list_items")
        self.assertTrue(pf.ok)
        self.assertTrue(any(c["check"] == "binding" and c["status"] == "warn" for c in pf.checks))

    def test_disconnected_extension_fails_first(self):
        b = FakeBrowser()
        b.session.connected = False
        _, pf = self.preflight(b, "list_items")
        self.assertFalse(pf.ok)
        self.assertEqual(pf.checks[0]["check"], "connection")
        self.assertEqual(b.log, [])

    def test_draft_action_is_never_run(self):
        m = self.module(actions={**ACTIONS, "half": {"status": "draft", "page": "home", "description": "d",
                                                      "evidence": "TODO"}})
        _, pf = self.preflight(FakeBrowser(), "half", module=m)
        self.assertFalse(pf.ok)
        self.assertIn("draft", pf.problem)


class TestLearningAcrossModules(TmpCase):
    def setUp(self):
        super().setUp()
        # A module that already knows how to download through a Radix menu.
        write_module(self.root, "musicApp", pages={"lib": {"url": "https://music.test/lib", "ready_when_any": ["row"]}},
                     locators={"row": {"page": "lib", "css": "li.row"},
                               "download_button": {"within": "row", "role": "button", "name": "Download",
                                                   "css": "button[aria-label=\"Download\"]"},
                               "open_menu": {"page": "lib", "role": "menu",
                                             "css": "[role=\"menu\"][data-state=\"open\"]"}},
                     actions={"download_track": {"page": "lib", "description": "Download one track as MP3",
                                                 "evidence": "file verified", "steps": ["click Download", "pick MP3"],
                                                 "retry": "only if no file arrived",
                                                 "uses": ["download_button", "open_menu"]}},
                     lessons="# lessons\n\n- **Radix menus belong to their trigger:** match aria-labelledby.\n"
                             "- **Unrelated lesson** about billing.\n")

    def test_related_finds_similar_actions_lessons_and_locators(self):
        m = self.module()
        snap = {"hints": ["radix", "menu"], "elements": [{"role": "button", "name": "Download",
                                                         "css": "button[aria-label=\"Download\"]"}]}
        rel = mk.related_knowledge(m, "download_file", snap, modules_dir=self.root)
        self.assertEqual([a["action"] for a in rel["actions"]], ["download_track"])
        self.assertEqual(len(rel["lessons"]), 1)
        self.assertIn("Radix menus", rel["lessons"][0]["lesson"])
        keys = {c["locator"] for c in rel["locators"]}
        self.assertIn("download_button", keys)
        self.assertIn("open_menu", keys)

    def test_unknown_action_is_learned_as_a_draft_with_tested_transfer_candidates(self):
        b = FakeBrowser(present=("ul.items", "li.row", "button[aria-label=\"Download\"]"))
        m, pf = self.preflight(b, "download_file")
        self.assertFalse(pf.ok)
        self.assertEqual(pf.draft_registered, "download_file")
        saved = json.loads((m.knowledge_dir / "actions.json").read_text())["actions"]["download_file"]
        self.assertEqual(saved["status"], "draft")
        transfer = saved["transfer_candidates"]
        self.assertEqual(transfer["actions"][0]["module"], "musicApp")
        works = {c["locator"]: c["works_here"] for c in transfer["locators"]}
        self.assertTrue(works["download_button"])   # its selector matched on this page
        self.assertFalse(works["open_menu"])        # menu isn't open on this page: not proven here

    def test_transfer_candidate_hidden_in_the_dom_does_not_count_as_working(self):
        b = FakeBrowser(present=("ul.items", "li.row"))
        b.hidden = {"button[aria-label=\"Download\"]"}
        m, pf = self.preflight(b, "download_file")
        transfer = json.loads((m.knowledge_dir / "actions.json").read_text())["actions"]["download_file"]["transfer_candidates"]
        cand = next(c for c in transfer["locators"] if c["locator"] == "download_button")
        self.assertEqual((cand["count_here"], cand["visible_here"], cand["works_here"]), (1, 0, False))
        self.assertTrue(any(c["check"] == "reuse" for c in pf.checks))
        # The draft can't run until it is completed.
        _, again = self.preflight(b, "download_file", module=mk.load(m.dir))
        self.assertIn("draft", again.problem)

    def test_a_module_does_not_learn_from_itself(self):
        m = mk.load(self.root / "musicApp")
        rel = mk.related_knowledge(m, "download_track", modules_dir=self.root)
        self.assertEqual(rel["actions"], [])


class TestVerify(TmpCase):
    def test_write_records_verified_and_missing(self):
        m = self.module()
        report = run(mk.verify(m, FakeBrowser(present=("ul.items",)), write=True))
        self.assertIn("settings_tab", report["missing"])
        saved = json.loads((m.knowledge_dir / "locators.json").read_text())["locators"]
        self.assertRegex(saved["list"]["verified"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertIn("last_missing", saved["settings_tab"])
        self.assertNotIn("last_missing", saved["spinner"])  # transient: absent is fine


if __name__ == "__main__":
    unittest.main()
