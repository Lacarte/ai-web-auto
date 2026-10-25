"""Run engine: Observe -> Plan -> Act -> Verify -> Recover.

One run at a time, bound to one tab. The engine never treats a dispatched
action as success: a step is `verified` only when its expectations hold on the
page afterwards, and the goal succeeds only when its `success` criteria hold.

Recovery rules:
- Locating has no side effects, so it is retried freely until the step's
  locate timeout: waiting out spinners, obstructions, disabled controls and
  late-rendered content, handling known interrupts (cookie banners...), and
  scrolling lazy lists. Ambiguity is never "recovered" by picking one.
- Actions are re-dispatched only when the step is repeatable (typing, select,
  navigate...) or the spec says safe_to_repeat. Clicks and key presses may
  submit, so after one dispatch the engine only waits longer for evidence.
- When the outcome is unknown (timeout, extension disconnected mid-action),
  the engine re-verifies the page before deciding; for non-repeatable steps it
  asks the human rather than risk a duplicate submission.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlsplit

from loguru import logger

from .browser import BrowserError, Disconnected, ExtensionBrowser
from .config import Config
from .goals import (Expectation, GoalError, GoalSpec, Locator, Step, describe_locator, load_goals,
                    origin_of, render)
from .planner import PlannerError, PlannerNeedsHuman
from .protocol import ConfirmArgs, RunRef, StartRunArgs
from .runlog import MASK, Redactor, RunLog
from .safety import Safety

LOCATE_SLICE_MS = 1500
VERIFY_SLICE_MS = 2000
MAX_LOCATE_REDISPATCH = 3     # re-locates after a revalidation miss (element went stale/obstructed)
MAX_UNKNOWN_OUTCOMES = 3      # timeouts/disconnects tolerated within one step
MAX_AI_FAILURES = 3


class RunState(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_HUMAN = "waiting_human"
    INTERRUPTED = "interrupted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STOPPED = "stopped"


TERMINAL = {RunState.SUCCEEDED, RunState.FAILED, RunState.STOPPED}


class StopRun(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class StepFailure(Exception):
    def __init__(self, code: str, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Run:
    def __init__(self, *, mode: str, title: str, tab_id: int, allowed_origins: list[str],
                 spec: Optional[GoalSpec] = None, goal_text: str = "", max_duration_s: int = 600):
        self.id = uuid.uuid4().hex[:12]
        self.mode = mode
        self.title = title
        self.goal_text = goal_text
        self.spec = spec
        self.tab_id = tab_id
        self.allowed_origins = allowed_origins
        self.max_duration_s = max_duration_s
        self.state = RunState.STARTING
        self.phase = ""
        self.message = ""
        self.steps: list[dict[str, Any]] = []
        self.step_index = -1
        self.history: deque[dict[str, Any]] = deque(maxlen=200)
        self.outputs: dict[str, Any] = {}
        self.evidence: list[dict[str, Any]] = []
        self.error: Optional[dict[str, Any]] = None
        self.pending_confirmation: Optional[dict[str, Any]] = None
        self.human: Optional[dict[str, Any]] = None
        self.started_at = _now()
        self.ended_at: Optional[str] = None
        self.actions_dispatched = 0
        self._resume = asyncio.Event()
        self._resume.set()
        self._stop_reason: Optional[str] = None
        self._confirm: Optional[asyncio.Future] = None
        self._started_mono = time.monotonic()
        self._held_since: Optional[float] = None
        self._held_total = 0.0
        self.task: Optional[asyncio.Task] = None

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL

    @property
    def active_seconds(self) -> float:
        held = self._held_total + (time.monotonic() - self._held_since if self._held_since else 0.0)
        return time.monotonic() - self._started_mono - held

    def hold(self):
        if self._held_since is None:
            self._held_since = time.monotonic()

    def release_hold(self):
        if self._held_since is not None:
            self._held_total += time.monotonic() - self._held_since
            self._held_since = None

    async def checkpoint(self):
        """Honour stop/pause between phases; enforce the active-time budget."""
        if self._stop_reason:
            raise StopRun(self._stop_reason)
        if not self._resume.is_set():
            await self._resume.wait()
            if self._stop_reason:
                raise StopRun(self._stop_reason)
        if self.active_seconds > self.max_duration_s:
            raise StepFailure("goal_timeout", f"the goal exceeded its {self.max_duration_s}s time budget")

    def snapshot(self, history: int = 40) -> dict[str, Any]:
        return {
            "run_id": self.id, "mode": self.mode, "title": self.title, "goal_text": self.goal_text,
            "tab_id": self.tab_id, "allowed_origins": self.allowed_origins,
            "state": self.state.value, "phase": self.phase, "message": self.message,
            "step_index": self.step_index, "steps": self.steps, "total_steps": len(self.steps),
            "outputs": self.outputs, "error": self.error, "evidence": self.evidence[-10:],
            "pending_confirmation": self.pending_confirmation, "human": self.human,
            "started_at": self.started_at, "ended_at": self.ended_at,
            "actions_dispatched": self.actions_dispatched,
            "history": list(self.history)[-history:],
        }


Emitter = Callable[[str, dict[str, Any]], Awaitable[bool]]


class Engine:
    def __init__(self, cfg: Config, browser: ExtensionBrowser, push: Emitter,
                 planner_factory: Optional[Callable[[], Any]] = None,
                 is_connected: Optional[Callable[[], bool]] = None):
        self.cfg = cfg
        self.browser = browser
        self.push = push
        self.safety = Safety(cfg["safety"])
        self.planner_factory = planner_factory
        self._is_connected = is_connected or (lambda: browser.connected)
        self._connected = asyncio.Event()
        if self._is_connected():
            self._connected.set()
        self.run: Optional[Run] = None
        self.last_run: Optional[Run] = None
        self.goals: dict[str, GoalSpec] = {}
        self.goal_errors: list[str] = []
        self._log: Optional[RunLog] = None
        self._redactor = Redactor()
        self._js_dialog: Optional[dict[str, Any]] = None
        self._interrupt_counts: dict[str, int] = {}
        self._in_interrupt = False
        self.reload_goals()

    # ── Goals ────────────────────────────────────────────────────────────

    def reload_goals(self):
        self.goals, self.goal_errors = {}, []
        for directory in self.cfg.goals_dirs:
            goals, errors = load_goals(directory)
            for gid in set(goals) & set(self.goals):
                errors.append(f"duplicate goal id '{gid}' in {directory}")
                goals.pop(gid)
            self.goals.update(goals)
            self.goal_errors.extend(errors)
        for err in self.goal_errors:
            logger.warning(f"Goal spec rejected: {err}")

    def list_goals(self) -> dict[str, Any]:
        self.reload_goals()
        return {"goals": [g.summary() for g in self.goals.values()], "errors": self.goal_errors,
                "ai_planner": self.ai_available()}

    def ai_available(self) -> dict[str, Any]:
        if not self.planner_factory:
            return {"available": False, "reason": "planner not configured"}
        try:
            self.planner_factory()
            return {"available": True}
        except Exception as e:
            return {"available": False, "reason": str(e)}

    # ── Extension events ─────────────────────────────────────────────────

    def on_client_change(self, connected: bool):
        if connected:
            self._connected.set()
            asyncio.get_event_loop().create_task(self.push("state_snapshot", self.state_snapshot()))
        else:
            self._connected.clear()

    async def on_tab_closed(self, msg: dict[str, Any]):
        run = self.run
        tab_id = (msg.get("data") or {}).get("tab_id")
        if run and not run.terminal and tab_id == run.tab_id:
            run._stop_reason = "the target tab was closed"
            run._resume.set()
            if run._confirm and not run._confirm.done():
                run._confirm.set_result(False)

    async def on_debugger_detached(self, msg: dict[str, Any]):
        """The user clicked "Cancel" on Chrome's debugging bar: respect it, don't re-attach."""
        data = msg.get("data") or {}
        run = self.run
        if run and not run.terminal and data.get("tab_id") == run.tab_id and data.get("reason") == "canceled_by_user":
            run._stop_reason = "debugging was cancelled in the browser"
            run._resume.set()
            if run._confirm and not run._confirm.done():
                run._confirm.set_result(False)

    async def on_js_dialog(self, msg: dict[str, Any]):
        data = msg.get("data") or {}
        run = self.run
        if not run or data.get("tab_id") != run.tab_id:
            return
        self._js_dialog = data if data.get("open") else None
        if not data.get("open") and run.human and run.human.get("kind") == "js_dialog":
            run.human = None
            run.release_hold()
            await self._set_state(run, RunState.RUNNING, "The page dialog was answered — continuing")
            run._resume.set()

    # ── UI operations ────────────────────────────────────────────────────

    def state_snapshot(self) -> dict[str, Any]:
        current = self.run or self.last_run
        return {"run": current.snapshot() if current else None,
                "goals_available": len(self.goals), "ai_planner": self.ai_available()}

    async def start_run(self, args: StartRunArgs) -> dict[str, Any]:
        if self.run and not self.run.terminal:
            raise RuntimeError(f"run {self.run.id} is still {self.run.state.value}; stop it first")
        if bool(args.goal_id) == bool(args.goal_text):
            raise ValueError("provide exactly one of goal_id or goal_text")
        granted = {o.rstrip("/").lower() for o in args.granted_origins}
        if args.goal_id:
            self.reload_goals()
            spec = self.goals.get(args.goal_id)
            if not spec:
                raise ValueError(f"unknown goal '{args.goal_id}'")
            try:
                spec, inputs, secrets = render(spec, args.inputs)
            except GoalError as e:
                raise ValueError(str(e)) from e
            missing = [o for o in spec.allowed_origins if o not in granted]
            if missing:
                raise PermissionError(f"site access not granted for {', '.join(missing)}")
            run = Run(mode="spec", title=spec.title, tab_id=args.tab_id, spec=spec,
                      allowed_origins=spec.allowed_origins, max_duration_s=spec.max_duration_s)
            run.steps = [{"id": s.id, "label": s.label(), "action": s.action, "status": "pending", "attempts": 0}
                         for s in spec.steps]
        else:
            avail = self.ai_available()
            if not avail["available"]:
                raise RuntimeError(f"AI planner unavailable: {avail.get('reason')}")
            if not granted:
                raise PermissionError("grant access to the current site before starting a free-text goal")
            run = Run(mode="ai", title=args.goal_text[:120], goal_text=args.goal_text, tab_id=args.tab_id,
                      allowed_origins=sorted(granted), max_duration_s=self.cfg["ai"].get("max_duration_s", 900))
            secrets = set()
        self._redactor = Redactor(secrets)
        self._log = RunLog(self.cfg.runs_dir, run.id, self._redactor)
        self._js_dialog = None
        self.run = run
        self._log.write("run_created", mode=run.mode, title=run.title, tab_id=run.tab_id,
                        allowed_origins=run.allowed_origins, goal_id=args.goal_id)
        run.task = asyncio.create_task(self._drive(run))
        return run.snapshot()

    def _current(self, ref: RunRef | ConfirmArgs) -> Run:
        run = self.run
        if not run or (ref.run_id and ref.run_id != run.id):
            raise ValueError("no such active run")
        return run

    async def pause(self, ref: RunRef) -> dict[str, Any]:
        run = self._current(ref)
        if run.state != RunState.RUNNING:
            raise RuntimeError(f"cannot pause a run that is {run.state.value}")
        run._resume.clear()
        run.hold()
        await self._set_state(run, RunState.PAUSED, "Paused by user — finishing the current check, then holding")
        return run.snapshot()

    async def resume(self, ref: RunRef) -> dict[str, Any]:
        run = self._current(ref)
        if run.state not in (RunState.PAUSED, RunState.WAITING_HUMAN):
            raise RuntimeError(f"cannot resume a run that is {run.state.value}")
        if run.pending_confirmation:
            raise RuntimeError("answer the pending confirmation (approve or reject) instead of resuming")
        run.human = None
        run.release_hold()
        await self._set_state(run, RunState.RUNNING, "Resumed")
        run._resume.set()
        return run.snapshot()

    async def stop(self, ref: RunRef) -> dict[str, Any]:
        run = self._current(ref)
        if run.terminal:
            return run.snapshot()
        run._stop_reason = "stopped by user"
        run._resume.set()
        if run._confirm and not run._confirm.done():
            run._confirm.set_result(False)
        await self._emit(run, "log", "Stop requested — finishing the current command", level="warn")
        return run.snapshot()

    async def confirm(self, args: ConfirmArgs) -> dict[str, Any]:
        run = self._current(args)
        pending = run.pending_confirmation
        if not pending or pending["id"] != args.confirmation_id or not run._confirm or run._confirm.done():
            raise ValueError("no matching confirmation is pending")
        run._confirm.set_result(args.approved)
        return run.snapshot()

    # ── Emitting ─────────────────────────────────────────────────────────

    async def _emit(self, run: Run, kind: str, text: str = "", level: str = "info", **data: Any):
        text = self._redactor.text(text)
        data = self._redactor.obj(data)
        entry = {"ts": _now(), "kind": kind, "text": text, "level": level, **data}
        if kind in ("log", "step", "result", "confirm", "human", "recover"):
            run.history.append(entry)
        if self._log:
            self._log.write(kind, text=text, level=level, **data)
        await self.push("run_event", {"run_id": run.id, "kind": kind, "text": text, "level": level,
                                      "state": run.state.value, "phase": run.phase,
                                      "step_index": run.step_index, "total_steps": len(run.steps),
                                      "title": run.title, **data})

    async def _set_state(self, run: Run, state: RunState, message: str = ""):
        run.state = state
        run.message = self._redactor.text(message)
        await self._emit(run, "state", message, level="info")

    async def _phase(self, run: Run, phase: str, message: str):
        run.phase = phase
        run.message = self._redactor.text(message)
        await self._emit(run, "phase", message)

    # ── Driving a run ────────────────────────────────────────────────────

    async def _drive(self, run: Run):
        outcome, message = RunState.FAILED, ""
        try:
            await self._set_state(run, RunState.RUNNING, f"Starting: {run.title}")
            await self._bind(run)
            if run.mode == "spec":
                await self._drive_spec(run)
            else:
                await self._drive_ai(run)
            outcome, message = RunState.SUCCEEDED, run.message
        except StopRun as e:
            outcome, message = RunState.STOPPED, f"Stopped: {e.reason}"
        except StepFailure as e:
            outcome, message = RunState.FAILED, e.message
            run.error = {"code": e.code, "message": self._redactor.text(e.message),
                         "details": self._redactor.obj(_trim(e.details)), "step_index": run.step_index}
            await self._capture_failure(run)
        except BrowserError as e:
            outcome, message = RunState.FAILED, f"Browser error: {e.message}"
            run.error = {"code": e.code, "message": self._redactor.text(e.message), "step_index": run.step_index}
        except Exception as e:  # internal bug: report, don't hide
            logger.exception("Run crashed")
            outcome, message = RunState.FAILED, f"Internal error: {e}"
            run.error = {"code": "internal_error", "message": str(e)[:500], "step_index": run.step_index}
        finally:
            run.pending_confirmation = None
            run.release_hold()
            run.ended_at = _now()
            run.state = outcome
            run.message = self._redactor.text(message)
            run.phase = "done"
            level = {"succeeded": "ok", "stopped": "warn"}.get(outcome.value, "error")
            await self._emit(run, "result", message, level=level, outcome=outcome.value,
                             outputs=run.outputs, error=run.error)
            if self._connected.is_set():
                try:
                    await self.browser.release_tab(outcome.value, run.message)
                except BrowserError:
                    pass
            self.last_run = run
            self.run = None
            logger.info(f"Run {run.id} {outcome.value}: {message}")

    async def _bind(self, run: Run):
        await self._phase(run, "observe", "Connecting to the target tab")
        info = await self.browser.bind_tab(run.tab_id, run.allowed_origins, run.id)
        run.outputs["_start_url"] = info.get("url", "")
        if self.safety.blacklisted(info.get("url", "")):
            raise StepFailure("blacklisted", f"{origin_of(info.get('url', ''))} is blacklisted in the backend config")

    async def _capture_failure(self, run: Run):
        if not self._connected.is_set() or not self._log:
            return
        try:
            shot = await self.browser.screenshot()
            path = self._log.save_screenshot(f"failure-step{run.step_index + 1}", shot)
            if path and run.error is not None:
                run.error["screenshot"] = str(path)
        except BrowserError:
            pass

    # ── Spec goals ───────────────────────────────────────────────────────

    async def _drive_spec(self, run: Run):
        spec = run.spec
        assert spec is not None
        if spec.start_url:
            current = run.outputs.get("_start_url", "")
            if not _same_page(current, spec.start_url):
                await self._navigate(run, spec.start_url)
        self._interrupt_counts = {i.id: 0 for i in spec.interrupts}

        if spec.check_success_first:
            await self._phase(run, "observe", "Checking whether the goal is already met")
            res = await self._check(run, spec.success)
            if res.get("ok"):
                run.evidence.append({"kind": "success", "results": res.get("results")})
                for s in run.steps:
                    s["status"] = "skipped"
                run.message = "Goal already satisfied — nothing to do"
                await self._emit(run, "log", run.message, level="ok")
                return

        for index, step in enumerate(spec.steps):
            run.step_index = index
            await run.checkpoint()
            try:
                await self._execute_step(run, step, index)
            except StepFailure as e:
                if not step.optional:
                    run.steps[index]["status"] = "failed"
                    run.steps[index]["note"] = self._redactor.text(e.message)
                    await self._emit(run, "step", f"✗ {step.label()}: {e.message}", level="error",
                                     step_id=step.id, status="failed", code=e.code)
                    raise
                run.steps[index]["status"] = "skipped"
                await self._emit(run, "step", f"Optional step skipped: {e.message}", level="warn",
                                 step_id=step.id, status="skipped")

        await self._phase(run, "verify", "Verifying the goal's success criteria")
        res = await self._wait_expect(run, [e.wire() for e in spec.success],
                                      self.cfg.timeout("verify_s"), "Verifying that the goal was reached...")
        if not res.get("ok"):
            raise StepFailure("goal_not_reached", "all steps ran, but the success criteria are not met: "
                              + _unmet(res), {"results": res.get("results")})
        run.evidence.append({"kind": "success", "results": res.get("results")})
        run.message = f"Goal reached and verified: {run.title}"

    async def _execute_step(self, run: Run, step: Step, index: int, *, interrupt: bool = False):
        rec = run.steps[index] if not interrupt else {"status": "pending", "attempts": 0}
        rec["status"] = "running"
        label = step.label()
        if not interrupt:
            await self._emit(run, "step", f"Step {index + 1}/{len(run.steps)}: {label}", step_id=step.id,
                             status="running")

        if step.skip_if:
            res = await self._check(run, step.skip_if, step.target)
            if res.get("ok"):
                rec["status"], rec["note"] = "skipped", "skip_if conditions already hold"
                await self._emit(run, "step", f"Skipped (already done): {label}", level="ok",
                                 step_id=step.id, status="skipped")
                return

        expects = self._expectations(step)
        if expects and step.action not in ("wait", "assert"):
            res = await self._check(run, expects)
            if res.get("ok"):
                rec["status"], rec["note"] = "verified", "already in the expected state"
                run.evidence.append({"step": step.id, "results": res.get("results"), "pre_satisfied": True})
                await self._emit(run, "step", f"✓ Already in the expected state: {label}", level="ok",
                                 step_id=step.id, status="verified")
                return

        if not interrupt:
            await self._handle_interrupts(run)

        if step.action == "navigate":
            await self._navigate(run, step.url or "")
            res = await self._verify(run, step, expects) if expects else {"ok": True}
        elif step.action in ("wait", "assert"):
            await self._phase(run, "verify", f"{'Waiting until' if step.action == 'wait' else 'Checking that'} "
                              f"{'; '.join(e.describe() for e in step.expect)}")
            res = await self._wait_expect(run, expects, step.timeout_s or self.cfg.timeout("verify_s"),
                                          f"Waiting: {label}")
            if not res.get("ok"):
                raise StepFailure("not_verified", f"{label}: {_unmet(res)}", {"results": res.get("results")})
        elif step.action == "extract":
            res = await self._extract(run, step)
        elif step.action in ("press", "scroll") and step.target is None:
            await run.checkpoint()
            await self.safety.rate_limit(run.checkpoint)
            await self._phase(run, "act", f"Pressing {'+'.join([*step.modifiers, step.key or ''])}"
                              if step.action == "press" else f"Scrolling {step.direction}")
            await self.browser.act(step.action, "", {}, label, **self._act_params(step))
            run.actions_dispatched += 1
            res = await self._verify(run, step, expects)
        else:
            res = await self._perform(run, step, expects, rec)

        rec["status"] = "verified"
        run.evidence.append({"step": step.id, "results": res.get("results")})
        if not interrupt:
            await self._emit(run, "step", f"✓ Verified: {label}", level="ok", step_id=step.id, status="verified",
                             evidence=_trim(res.get("results")))

    def _expectations(self, step: Step) -> list[dict[str, Any]]:
        """Step expectations plus the implicit evidence every value-setting action owes."""
        out = [e.wire(step.target) for e in step.expect]
        t = step.target.wire() if step.target else None
        if step.action == "type" and t is not None:
            mode = "equals" if step.clear else "contains"
            out.insert(0, {"kind": "value", "args": {"target": t, mode: step.value or ""}})
        elif step.action == "select" and t is not None:
            out.insert(0, {"kind": "value", "args": {"target": t, "equals": step.value or ""}})
        elif step.action == "check" and t is not None:
            out.insert(0, {"kind": "checked", "args": {"target": t, "value": bool(step.checked)}})
        return out

    async def _perform(self, run: Run, step: Step, expects: list[dict], rec: dict) -> dict:
        """Locate -> (confirm) -> act -> settle -> verify, with bounded recovery."""
        label = step.label()
        locator = step.target.wire() if step.target else {}
        desc = describe_locator(step.target)
        dispatches_allowed = step.retry.attempts if step.repeatable else 1
        dispatches = 0
        redispatch_misses = 0
        unknown_outcomes = 0
        stage = "locate"
        while True:
            try:
                await run.checkpoint()
                stage = "locate"
                found = await self._locate(run, step, locator, desc)
                if await self._confirm_if_sensitive(run, step, found):
                    found = await self._locate(run, step, locator, desc)  # page may have changed while waiting
                await self.safety.rate_limit(run.checkpoint)
                await run.checkpoint()
                stage = "act"
                await self._phase(run, "act", _act_phrase(step, desc))
                result = await self.browser.act(step.action, found["handle"], locator, label,
                                                **self._act_params(step))
                if not result.get("dispatched"):
                    if result.get("terminal"):
                        raise StepFailure(result.get("code", "act_failed"), f"{label}: {result.get('message')}", result)
                    redispatch_misses += 1
                    if redispatch_misses > MAX_LOCATE_REDISPATCH:
                        raise StepFailure(result.get("code", "act_failed"),
                                          f"{label}: {result.get('message', 'the target kept changing')}", result)
                    await self._emit(run, "recover", f"Target changed before the action ({result.get('code')}); "
                                     "locating it again", level="warn", step_id=step.id)
                    continue
                dispatches += 1
                rec["attempts"] = dispatches
                run.actions_dispatched += 1
                stage = "verify"
                res = await self._verify(run, step, expects, alerts_before=found.get("alerts", []))
                if res.get("ok"):
                    return res
                if dispatches < dispatches_allowed:
                    await self._emit(run, "recover", f"Not verified ({_unmet(res)}); retrying "
                                     f"({dispatches + 1}/{dispatches_allowed})", level="warn", step_id=step.id)
                    await asyncio.sleep(min(0.5 * 2 ** (dispatches - 1), 4.0))
                    continue
                if not step.repeatable:
                    # One dispatch only: give slow pages one more verification window.
                    await self._phase(run, "verify", f"Still waiting for evidence that {label!r} worked")
                    res = await self._wait_expect(run, expects, self.cfg.timeout("verify_s"),
                                                  f"Still verifying: {label}")
                    if res.get("ok"):
                        return res
                    raise StepFailure("not_verified", f"{label}: dispatched once, but {_unmet(res)}. "
                                      "Not repeating it: the action may not be safe to repeat.",
                                      {"results": res.get("results")})
                raise StepFailure("not_verified", f"{label}: {_unmet(res)} after {dispatches} attempts",
                                  {"results": res.get("results")})
            except BrowserError as e:
                if not e.outcome_unknown:
                    raise StepFailure(e.code, f"{label}: {e.message}", e.details) from e
                unknown_outcomes += 1
                if unknown_outcomes > MAX_UNKNOWN_OUTCOMES:
                    raise StepFailure(e.code, f"{label}: {e.message} (gave up after {MAX_UNKNOWN_OUTCOMES} "
                                      "interrupted attempts)") from e
                await self._recover_unknown(run, e)
                if stage in ("act", "verify"):
                    res = await self._check(run, expects)
                    if res.get("ok"):
                        await self._emit(run, "recover", f"After the interruption the page shows {label!r} took "
                                         "effect", level="ok", step_id=step.id)
                        return res
                    if not step.repeatable:
                        await self._wait_for_human(run, "interrupted",
                                                   f"The connection dropped while performing {label!r}, and the page "
                                                   "doesn't show it took effect. It won't be repeated automatically. "
                                                   "Check the tab, finish it by hand if needed, then Resume.")
                        res = await self._check(run, expects)
                        if res.get("ok"):
                            return res
                        raise StepFailure("unverified_after_interruption",
                                          f"{label}: still not verified after the interruption", res)
                await self._emit(run, "recover", f"Resuming {label!r} after the interruption", level="warn",
                                 step_id=step.id)

    def _act_params(self, step: Step) -> dict[str, Any]:
        p: dict[str, Any] = {}
        if step.action == "type":
            p.update(value=step.value or "", clear=step.clear, secret=self._redactor.text(step.value or "") == MASK)
        elif step.action == "select":
            p["value"] = step.value or ""
        elif step.action == "check":
            p["checked"] = bool(step.checked)
        elif step.action == "press":
            p.update(key=step.key, modifiers=step.modifiers)
        elif step.action == "scroll":
            p["direction"] = step.direction
        return p

    async def _locate(self, run: Run, step: Step, locator: dict, desc: str) -> dict:
        """Find a unique, actionable target, recovering from transient problems."""
        timeout_s = step.timeout_s or self.cfg.timeout("locate_s")
        deadline = time.monotonic() + timeout_s
        purpose = step.action
        scrolled = False
        last: dict[str, Any] = {}
        scope_phrase = desc
        await self._phase(run, "observe", f"Searching for {scope_phrase}...")
        while True:
            await run.checkpoint()
            if self._js_dialog:
                await self._wait_for_human(run, "js_dialog",
                                           f"The page opened a {self._js_dialog.get('dialog_type', 'JavaScript')} "
                                           f"dialog: \"{self._js_dialog.get('message', '')[:200]}\". "
                                           "Respond to it in the tab, then Resume.")
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            r = await self.browser.locate(locator, purpose, f"Searching for {desc}...",
                                          max(300, min(LOCATE_SLICE_MS, remaining_ms)))
            if r.get("ok"):
                self._check_origin(run, r.get("url", ""))
                return r
            last = r
            code = r.get("code", "not_found")
            if code == "ambiguous":
                raise StepFailure("ambiguous", f"{r.get('count', 'several')} elements match {desc}; refusing to "
                                  "guess. Narrow the locator with 'within' (a dialog, row, or region).", r)
            if code == "invalid_locator":
                raise StepFailure(code, f"{desc}: {r.get('message')}", r)
            if code == "js_dialog_open":
                self._js_dialog = self._js_dialog or {"dialog_type": "JavaScript", "message": r.get("message", "")}
                continue
            if code == "obstructed":
                if await self._handle_interrupts(run):
                    continue
                await self._phase(run, "recover", f"{desc} is covered by {r.get('obstructed_by', 'another element')}"
                                  " — waiting for it to clear")
            elif code == "not_found" and not scrolled and time.monotonic() > deadline - timeout_s + min(3.0, timeout_s / 3):
                scrolled = True
                await self._phase(run, "recover", f"{desc} not visible yet — scrolling to look for lazy content")
                s = await self.browser.scroll_search(locator, f"Scrolling to find {desc}...")
                if s.get("ok"):
                    return s
            elif code in ("hidden", "disabled", "unstable", "not_editable", "scope_not_found", "no_active_dialog"):
                await self._phase(run, "observe", f"Waiting: {r.get('message') or code}")
            if time.monotonic() >= deadline:
                raise StepFailure(code, f"Could not find a usable target for {desc} within {timeout_s:g}s: "
                                  f"{r.get('message') or code}", last)

    async def _verify(self, run: Run, step: Step, expects: list[dict], alerts_before: list | None = None) -> dict:
        await self._phase(run, "verify", "Waiting for the page to settle...")
        try:
            await self.browser.settle(int(self.cfg.timeout("settle_s") * 1000), "Waiting for the page to update...")
        except BrowserError as e:
            if e.outcome_unknown:
                raise
        if not expects:
            return {"ok": True, "results": []}
        what = "; ".join(_describe_wire(e) for e in expects[:3])
        await self._phase(run, "verify", f"Verifying: {what}")
        res = await self._wait_expect(run, expects, step.timeout_s or self.cfg.timeout("verify_s"),
                                      f"Verifying: {what}...")
        self._check_origin(run, res.get("url", ""))
        if not res.get("ok"):
            new_alerts = [a for a in res.get("alerts", []) if a not in (alerts_before or [])]
            if new_alerts:
                raise StepFailure("page_reported_error", f"{step.label()}: the page reported "
                                  f"\"{new_alerts[0][:200]}\"", {"alerts": new_alerts, "results": res.get("results")})
        return res

    async def _wait_expect(self, run: Run, expects: list[dict], timeout_s: float, label: str) -> dict:
        deadline = time.monotonic() + timeout_s
        res: dict[str, Any] = {"ok": False, "results": []}
        while True:
            await run.checkpoint()
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            res = await self.browser.wait_for(expects, max(200, min(VERIFY_SLICE_MS, remaining_ms)), label)
            if res.get("ok") or time.monotonic() >= deadline:
                return res

    async def _check(self, run: Run, expectations: list, step_target: Optional[Locator] = None) -> dict:
        wired = [e.wire(step_target) if isinstance(e, Expectation) else e for e in expectations]
        return await self.browser.check(wired)

    async def _navigate(self, run: Run, url: str):
        if origin_of(url) not in run.allowed_origins:
            raise StepFailure("origin_not_allowed", f"{url} is outside the authorized origins {run.allowed_origins}")
        await run.checkpoint()
        await self._phase(run, "act", f"Opening {url}")
        r = await self.browser.navigate(url)
        run.actions_dispatched += 1
        self._check_origin(run, r.get("url", url))

    def _check_origin(self, run: Run, url: str):
        if url and not self.safety.origin_allowed(url, run.allowed_origins):
            raise StepFailure("left_authorized_site", f"the page moved to {origin_of(url) or url}, outside the "
                              f"authorized origins {run.allowed_origins}; stopping rather than act there")

    async def _extract(self, run: Run, step: Step) -> dict:
        await self._phase(run, "observe", f"Extracting {', '.join(step.fields)}")
        data = await self.browser.extract_fields({k: v.wire() for k, v in step.fields.items()})
        values = data.get("values", {})
        missing = [k for k, v in values.items() if v is None]
        if missing:
            raise StepFailure("extract_missing", f"could not extract {missing}", data)
        run.outputs[step.id] = self._redactor.obj(values)
        return {"ok": True, "results": [{"kind": "extract", "ok": True, "actual": run.outputs[step.id]}]}

    async def _handle_interrupts(self, run: Run) -> bool:
        spec = run.spec
        if not spec or not spec.interrupts or self._in_interrupt:
            return False
        live = [i for i in spec.interrupts if self._interrupt_counts.get(i.id, 0) < i.max_times]
        if not live:
            return False
        handled = False
        for intr in live:
            res = await self._check(run, intr.when)
            if not res.get("ok"):
                continue
            self._interrupt_counts[intr.id] += 1
            await self._emit(run, "recover", f"Handling interruption '{intr.id}': {intr.do.label()}", level="warn")
            self._in_interrupt = True
            try:
                await self._execute_step(run, intr.do, run.step_index, interrupt=True)
                handled = True
            except StepFailure as e:
                await self._emit(run, "recover", f"Interruption handler '{intr.id}' failed: {e.message}",
                                 level="warn")
            finally:
                self._in_interrupt = False
        return handled

    async def _confirm_if_sensitive(self, run: Run, step: Step, found: dict) -> bool:
        target = found.get("target") or {}
        sens = self.safety.needs_confirmation(step.action, target, step.sensitive)
        if not sens:
            return False
        conf = {
            "id": uuid.uuid4().hex[:10], "step_id": step.id, "action": step.action, "category": sens.category,
            "reason": sens.reason,
            "description": f"{step.action.capitalize()} {target.get('role', 'element')} "
                           f"\"{target.get('name') or target.get('text') or '?'}\""
                           + (f" in dialog \"{target['dialog_name']}\"" if target.get("dialog_name") else ""),
        }
        loop = asyncio.get_event_loop()
        run._confirm = loop.create_future()
        run.pending_confirmation = conf
        run.hold()
        await self._set_state(run, RunState.WAITING_HUMAN, f"Waiting for your approval: {conf['description']}")
        await self._emit(run, "confirm", f"Approval needed: {conf['description']} ({sens.reason})", level="human",
                         confirmation=conf)
        await self.browser.overlay("human", handle=found.get("handle"), text=f"Approve in the extension popup: "
                                   f"{conf['description']}")
        try:
            approved = await asyncio.wait_for(asyncio.shield(run._confirm), self.cfg.timeout("confirmation_s"))
        except asyncio.TimeoutError:
            approved = None
        finally:
            run.pending_confirmation = None
            run.release_hold()
        if run._stop_reason:
            raise StopRun(run._stop_reason)
        if approved is None:
            raise StopRun("no answer to the confirmation request in time")
        if not approved:
            await self._emit(run, "human", f"Rejected: {conf['description']}", level="warn")
            raise StopRun(f"you rejected: {conf['description']}")
        await self._emit(run, "human", f"Approved: {conf['description']}", level="ok")
        await self._set_state(run, RunState.RUNNING, "Approved")
        return True

    async def _wait_for_human(self, run: Run, kind: str, message: str):
        run.human = {"kind": kind, "message": message}
        run._resume.clear()
        run.hold()
        await self._set_state(run, RunState.WAITING_HUMAN, message)
        await self._emit(run, "human", message, level="human", reason=kind)
        await self.browser.overlay("human", text=message)
        await run._resume.wait()
        if run._stop_reason:
            raise StopRun(run._stop_reason)
        if kind == "js_dialog":
            self._js_dialog = None

    async def _recover_unknown(self, run: Run, err: BrowserError):
        """The last command's outcome is unknown: wait out a disconnect, then re-bind the tab."""
        if isinstance(err, Disconnected) or not self._connected.is_set():
            run.hold()
            await self._set_state(run, RunState.INTERRUPTED, "Lost the extension connection — waiting for it "
                                  "to come back")
            try:
                await asyncio.wait_for(self._connected.wait(), self.cfg.timeout("reconnect_grace_s"))
            except asyncio.TimeoutError:
                run.release_hold()
                raise StepFailure("disconnected", "the extension did not reconnect within "
                                  f"{self.cfg.timeout('reconnect_grace_s'):g}s")
            run.release_hold()
            await self._set_state(run, RunState.RUNNING, "Reconnected — re-checking the page before continuing")
        else:
            await self._emit(run, "recover", f"Command outcome unknown ({err.message}); re-checking the page",
                             level="warn")
        await self.browser.bind_tab(run.tab_id, run.allowed_origins, run.id)

    # ── Free-text goals (AI planner) ─────────────────────────────────────

    async def _drive_ai(self, run: Run):
        planner = self.planner_factory()
        max_steps = int(self.cfg["ai"].get("max_steps", 30))
        feedback: list[dict[str, Any]] = []
        failures = 0
        for i in range(max_steps):
            await run.checkpoint()
            run.step_index = i
            await self._phase(run, "observe", "Observing the page")
            observation = await self.browser.observe()
            self._check_origin(run, observation.get("url", ""))
            await self._phase(run, "plan", "Planning the next action")
            try:
                decision = await planner.decide(run.goal_text, observation, feedback, run.allowed_origins)
            except PlannerNeedsHuman as e:
                await self._wait_for_human(run, "planner", f"The planner needs you: {e}. Handle it in the tab, "
                                           "then Resume.")
                feedback.append({"step": i, "outcome": "user_intervened", "detail": str(e)[:200]})
                continue
            except PlannerError as e:
                raise StepFailure("planner", str(e)) from e
            await self._emit(run, "log", f"Plan: {decision.reasoning}", step_index=i)
            if decision.done:
                res = await self._wait_expect(run, [e.wire() for e in decision.success],
                                              self.cfg.timeout("verify_s"), "Verifying that the goal was reached...")
                if res.get("ok"):
                    run.evidence.append({"kind": "success", "results": res.get("results")})
                    run.message = f"Goal reached and verified: {run.title}"
                    return
                feedback.append({"step": i, "outcome": "claimed_done_but_unverified", "detail": _unmet(res)})
                await self._emit(run, "recover", f"Planner said done, but {_unmet(res)}", level="warn")
                continue
            step = decision.step
            run.steps.append({"id": step.id, "label": step.label(), "action": step.action,
                              "status": "pending", "attempts": 0})
            try:
                await self._execute_step(run, step, len(run.steps) - 1)
                feedback.append({"step": i, "action": step.label(), "outcome": "verified"})
                failures = 0
            except StepFailure as e:
                run.steps[-1]["status"] = "failed"
                failures += 1
                feedback.append({"step": i, "action": step.label(), "outcome": "failed", "code": e.code,
                                 "detail": e.message[:400]})
                await self._emit(run, "recover", f"Step failed ({e.code}); re-planning", level="warn")
                if failures >= MAX_AI_FAILURES or e.code in ("left_authorized_site", "origin_not_allowed"):
                    raise
        raise StepFailure("max_steps", f"stopped after {max_steps} planned steps without verified success")


# ── Helpers ─────────────────────────────────────────────────────────────────

def _same_page(current: str, target: str) -> bool:
    """Same document (fragment ignored): the query is part of the page's state."""
    a, b = urlsplit(current or ""), urlsplit(target)
    return ((a.scheme, a.netloc.lower(), a.path.rstrip("/"), a.query)
            == (b.scheme, b.netloc.lower(), b.path.rstrip("/"), b.query))


def _unmet(res: dict) -> str:
    bad = [r for r in res.get("results", []) if not r.get("ok")]
    if not bad:
        return "the expected state was not observed"
    parts = []
    for r in bad[:3]:
        actual = r.get("actual")
        parts.append(f"{r.get('message') or r.get('kind')}" + (f" (actual: {str(actual)[:80]!r})" if actual is not None else ""))
    return "; ".join(parts)


def _describe_wire(e: dict) -> str:
    kind, args = e.get("kind", ""), e.get("args") or {}
    if kind in ("dialog_open", "dialog_closed"):
        name = (args.get("name") or {})
        n = name.get("equals") or name.get("contains") or name.get("regex") if isinstance(name, dict) else name
        return f"dialog {n!r} {'opens' if kind == 'dialog_open' else 'closes'}" if n else f"dialog {kind[7:]}"
    if kind in ("text_present", "text_absent"):
        t = args.get("text") or {}
        n = t.get("equals") or t.get("contains") or t.get("regex")
        return f"text {n!r} {'appears' if kind == 'text_present' else 'disappears'}"
    if kind == "value":
        return "the field holds the typed value"
    return kind.replace("_", " ")


def _act_phrase(step: Step, desc: str) -> str:
    return {
        "click": f"Clicking {desc}",
        "type": f"Typing into {desc}",
        "select": f"Selecting {step.value!r} in {desc}",
        "check": f"{'Checking' if step.checked else 'Unchecking'} {desc}",
        "press": f"Pressing {step.key} on {desc}",
        "hover": f"Hovering over {desc}",
        "scroll": f"Scrolling {desc}",
    }.get(step.action, f"{step.action} {desc}")


def _trim(o: Any, depth: int = 0) -> Any:
    """Keep diagnostics small enough for the popup and logs."""
    if depth > 4:
        return "…"
    if isinstance(o, str):
        return o if len(o) <= 300 else o[:300] + "…"
    if isinstance(o, list):
        return [_trim(x, depth + 1) for x in o[:12]]
    if isinstance(o, dict):
        return {k: _trim(v, depth + 1) for k, v in list(o.items())[:30] if k != "screenshot"}
    return o
