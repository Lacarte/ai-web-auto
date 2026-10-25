"""Shared test helpers: import path, temp config, and a scripted fake browser."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import tempfile
import warnings
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]            # @created-extensions/web-goal-agent
BACKEND = ROOT / "web-goal-agent-backend"
PROJECT = ROOT.parents[1]
PYTHON = PROJECT / "venv" / "Scripts" / "python.exe"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
warnings.filterwarnings("ignore", category=DeprecationWarning)

import logging  # noqa: E402

from loguru import logger  # noqa: E402

from goal_agent_backend.browser import BrowserError, Disconnected  # noqa: E402
from goal_agent_backend.config import DEFAULTS, Config, _deep_merge  # noqa: E402

logger.remove()  # keep unittest output readable; behaviour is asserted, not logged
logger.add(sys.stderr, level="ERROR")
logging.getLogger("websockets").setLevel(logging.CRITICAL)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_config(tmp: Path, goals: list[dict] | None = None, **overrides: Any) -> Config:
    goals_dir = tmp / "goals"
    goals_dir.mkdir(parents=True, exist_ok=True)
    for g in goals or []:
        (goals_dir / f"{g['id']}.json").write_text(json.dumps(g), encoding="utf-8")
    data = _deep_merge(DEFAULTS, {
        "token": "unit-test-token", "goals_dir": str(goals_dir), "runs_dir": str(tmp / "runs"),
        "state_dir": str(tmp / "state"),
        "timeouts": {"locate_s": 1.0, "verify_s": 0.4, "settle_s": 0.1, "reconnect_grace_s": 1.0,
                     "confirmation_s": 3.0, "command_s": 2.0},
        **overrides,
    })
    path = tmp / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return Config(path=path, data=data)


def tempdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="wga-unit-"))


ORIGIN = "http://127.0.0.1:8777"


def goal(steps: list[dict], success: list[dict], **extra: Any) -> dict:
    return {"id": extra.pop("id", "g"), "title": "Unit goal", "allowed_origins": [ORIGIN],
            "steps": steps, "success": success, **extra}


def fact(name: str) -> dict:
    """An expectation the FakeBrowser treats as the named boolean fact."""
    return {"text_present": {"text": name}}


class FakeBrowser:
    """Scripted stand-in for ExtensionBrowser.

    Facts are named booleans; an expectation `text_present: <name>` is true when
    facts[name] is true. `on_act` mutates facts to model what an action did.
    `locate_script` (optional) returns locate results in order.
    """

    def __init__(self):
        self.facts: dict[str, bool] = {}
        self.calls: list[tuple] = []
        self.acts: list[tuple] = []
        self.url = ORIGIN + "/page.html"
        self.connected = True
        self.alerts: list[str] = []
        self.locate_script: list[dict] = []
        self.target_context: dict = {"role": "button", "name": "Go"}
        self.on_act: Callable[[str, str], Any] = lambda action, label: None
        self.act_errors: list[BrowserError] = []

    # ── expectation evaluation ───────────────────────────────────────────
    def _eval(self, expect: list[dict]) -> dict:
        results = []
        for e in expect:
            if e["kind"] in ("text_present", "text_absent"):
                name = e["args"]["text"]["equals"] if "equals" in e["args"]["text"] else e["args"]["text"]["contains"]
                v = self.facts.get(name, False)
                ok = v if e["kind"] == "text_present" else not v
            elif e["kind"] == "value":
                ok = self.facts.get("value:" + str(e["args"].get("equals", e["args"].get("contains"))), False)
            elif e["kind"] == "checked":
                ok = self.facts.get("checked", False) == e["args"]["value"]
            else:
                ok = self.facts.get(e["kind"], False)
            results.append({"kind": e["kind"], "ok": ok, "message": f"{e['kind']} not met"})
        return {"ok": all(r["ok"] for r in results), "results": results, "url": self.url, "alerts": list(self.alerts)}

    # ── browser API ──────────────────────────────────────────────────────
    async def bind_tab(self, tab_id, allowed_origins, run_id):
        self.calls.append(("bind", tab_id))
        return {"url": self.url, "ok": True}

    async def release_tab(self, outcome, message=""):
        self.calls.append(("release", outcome))
        return {"ok": True}

    async def navigate(self, url, timeout_ms=30000):
        self.calls.append(("navigate", url))
        self.url = url
        return {"url": url}

    async def observe(self, **kw):
        return {"url": self.url, "elements": []}

    async def locate(self, locator, purpose, label, timeout_ms):
        await asyncio.sleep(0.01)
        self.calls.append(("locate", label))
        if self.locate_script:
            r = self.locate_script.pop(0)
            if r.get("ok"):
                r = {"handle": "h1", "target": dict(self.target_context), "url": self.url, "alerts": list(self.alerts), **r}
            return r
        return {"ok": True, "handle": "h1", "point": {"x": 1, "y": 1}, "target": dict(self.target_context),
                "url": self.url, "alerts": list(self.alerts)}

    async def scroll_search(self, locator, label, max_scrolls=15):
        return {"ok": False, "code": "not_found"}

    async def act(self, action, handle, locator, label, **params):
        self.calls.append(("act", action, label))
        if self.act_errors:
            err = self.act_errors.pop(0)
            if err is not None:
                if getattr(err, "_apply_effect", False):
                    self.acts.append((action, label))
                    self.on_act(action, label)
                raise err
        self.acts.append((action, label))
        self.on_act(action, label)
        return {"ok": True, "dispatched": True, "url": self.url}

    async def wait_for(self, expect, timeout_ms, label):
        await asyncio.sleep(0.02)
        return self._eval(expect)

    async def check(self, expect):
        return self._eval(expect)

    async def settle(self, timeout_ms, label=""):
        return {"settled": True}

    async def extract_fields(self, fields):
        return {"values": {k: self.facts.get(f"extract:{k}") for k in fields}}

    async def screenshot(self):
        return ""

    async def overlay(self, op, **data):
        self.calls.append(("overlay", op))
