"""JobRunner — persistent, resumable, knowledge-acquiring self-play runs.

Wraps the one AI execution loop (AIAgent.execute_task) — it does NOT reimplement
it. The runner adds what the in-memory loop lacks:

  * persistence   job.json + append-only journal.jsonl survive crashes
  * resume        rebuilds action history from the journal and continues
  * knowledge     verified selectors persist per-domain and are injected into
                  future prompts, so run N+1 starts warm
  * reporting     HTML mission report regenerated on demand

EXPLORE mode is self-play without a hard goal: map the site, exercise safe
read-only flows, and record what was learned.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from loguru import logger

from ..automation_controller import BrowserAgent
from ..core.models import ResponseStatus
from ..core.state import Task
from .knowledge import SiteKnowledge, normalize_path
from .models import JobSpec, JobStatus, classify_error, now_iso
from .recipe import build_recipe, save_recipe
from .report import generate as generate_report
from .store import JobStore

EXPLORE_OBJECTIVE = (
    "Explore this website and build a working map of it. Systematically: "
    "1) discover the interactive elements on each page you reach; "
    "2) exercise the main read-only flows (navigation links, tabs, search, filters, pagination); "
    "3) do NOT perform destructive or transactional actions - no purchases, deletions, "
    "form submissions that send data, or account changes; "
    "4) when done, return action done with params.result describing: site purpose, "
    "main pages visited (URL + title), key UI flows observed, and any blocked or broken areas."
)

_CHECKPOINT_EVERY = 5


class _JobObserver:
    """Bridges AIAgent's per-step hook into the journal and knowledge store."""

    def __init__(self, agent: BrowserAgent, store: JobStore, knowledge: SiteKnowledge,
                 spec: JobSpec, base_steps: int):
        self.agent = agent
        self.store = store
        self.knowledge = knowledge
        self.spec = spec
        self.base_steps = base_steps  # steps already done before this execution

    async def on_action(self, step: int, action_dict: dict, resp) -> None:
        target = action_dict.get("target") or {}
        params = action_dict.get("params") or {}
        selector = (params.get("selector") or params.get("xpath")
                    or target.get("value") or "")
        ok = resp.status == ResponseStatus.SUCCESS
        url = self.agent.server.session.browser.url
        error_code = None if ok else classify_error(resp.error)
        absolute_step = self.base_steps + step + 1

        self.store.append(
            self.spec.id, "action_result",
            step=absolute_step,
            action=action_dict.get("action", ""),
            selector=(selector or "")[:500],
            ok=ok,
            error_code=error_code,
            error=(resp.error or "")[:300],
        )

        if selector:
            try:
                self.knowledge.record(url, action_dict.get("action", ""),
                                      selector, ok, error_code)
            except Exception as e:  # knowledge must never kill a run
                logger.warning(f"Knowledge record failed: {e}")

        if not ok:
            await self._capture(f"step{absolute_step}-fail")

        if absolute_step % _CHECKPOINT_EVERY == 0:
            self.store.checkpoint(self.spec, steps_done=absolute_step)

    async def _capture(self, label: str) -> None:
        try:
            r = await self.agent.server.screenshot()
            if r.status == ResponseStatus.SUCCESS and r.data.screenshot:
                rel = self.store.save_screenshot(self.spec.id, r.data.screenshot, label)
                if rel:
                    self.store.append(self.spec.id, "screenshot", label=label, path=rel)
        except Exception as e:
            logger.warning(f"Failure screenshot unavailable: {e}")


class JobRunner:
    def __init__(self, agent: BrowserAgent, jobs_root: str | Path = "jobs",
                 knowledge_root: str | Path = "knowledge/site"):
        self.agent = agent
        self.store = JobStore(jobs_root)
        self.knowledge = SiteKnowledge(Path(knowledge_root))

    # ── Public API ────────────────────────────────────────────────────────

    async def start(self, objective: str, start_url: str, max_steps: int = 40,
                    max_minutes: int = 20, explore: bool = False) -> JobSpec:
        if explore and not objective.strip():
            objective = EXPLORE_OBJECTIVE
        spec = self.store.create(objective, start_url, max_steps,
                                 max_runtime_s=max_minutes * 60, explore=explore)
        return await self._execute(spec)

    async def resume(self, job_id: str) -> JobSpec:
        spec = self.store.load(job_id)
        if spec.status not in (JobStatus.INTERRUPTED, JobStatus.FAILED):
            raise ValueError(
                f"Job {job_id} is {spec.status.value}; only interrupted/failed jobs can resume")
        spec.resume_count += 1
        spec.status = JobStatus.RUNNING
        spec.error_code = None
        spec.error = None
        spec.success = None
        self.store.save_spec(spec)
        return await self._execute(spec)

    def report(self, job_id: str) -> Path:
        return generate_report(job_id, self.store)

    def list_jobs(self) -> list[JobSpec]:
        return self.store.list_jobs()

    # ── Engine ────────────────────────────────────────────────────────────

    async def _execute(self, spec: JobSpec) -> JobSpec:
        started = time.time()
        self.store.append(spec.id, "job_started",
                          objective=spec.objective[:300],
                          start_url=spec.start_url[:300],
                          resume_count=spec.resume_count)

        history = self.store.action_history(spec.id)
        budget_left = max(1, spec.max_steps - len(history))
        task = Task(objective=spec.objective, start_url="", max_steps=budget_left)
        # Copy: the AI loop appends into task.action_history; keeping the
        # journal-derived list pristine is what makes len(history) below mean
        # "steps before this execution".
        task.action_history = list(history)

        observer = _JobObserver(self.agent, self.store, self.knowledge, spec,
                                base_steps=len(history))
        try:
            await self._navigate_if_needed(spec)

            self.agent.ai.context_provider = self._context
            try:
                async with asyncio.timeout(spec.max_runtime_s):
                    result = await self.agent.ai.execute_task(task, observer=observer)
            except TimeoutError:
                self._interrupt(spec, f"Runtime budget ({spec.max_runtime_s}s) exhausted", "TIMEOUT")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("Job crashed")
                self._interrupt(spec, f"{type(e).__name__}: {e}", classify_error(str(e)))
            else:
                spec.duration_ms = int((time.time() - started) * 1000)
                spec.steps_done = len(history) + task.steps_taken
                spec.success = bool(result.success)
                spec.result_data = result.data or {}
                if result.success:
                    spec.status = JobStatus.COMPLETED
                else:
                    spec.status = JobStatus.FAILED
                    spec.error_code = classify_error(result.error)
                    spec.error = result.error
        except asyncio.CancelledError:
            self._interrupt(spec, "Cancelled", "CANCELLED")
        except Exception as e:
            # Navigation failure, connection loss, anything unexpected.
            logger.exception("Job aborted")
            self._interrupt(spec, f"{type(e).__name__}: {e}", classify_error(str(e)))
        finally:
            self.agent.ai.context_provider = None
            if spec.status == JobStatus.RUNNING:  # escaped without a verdict
                self._interrupt(spec, "Runner exited without a verdict", "INTERNAL")
            self.store.checkpoint(spec, steps_done=spec.steps_done)

        self._finish(spec, started)
        return spec

    async def _navigate_if_needed(self, spec: JobSpec) -> None:
        current = self.agent.server.session.browser.url
        if current and normalize_path(current) == normalize_path(spec.start_url):
            self.store.append(spec.id, "navigate_skipped", already_at=current[:300])
            return
        if not spec.start_url:
            return
        r = await self.agent.navigate(spec.start_url)
        ok = r.status == ResponseStatus.SUCCESS
        self.store.append(spec.id, "navigated", url=spec.start_url[:300], ok=ok)
        if not ok:
            raise RuntimeError(f"Navigate failed: {r.error or 'unknown error'}")

    def _context(self) -> str:
        """Known-good selectors injected into the next decision prompt."""
        try:
            url = self.agent.server.session.browser.url or ""
            return self.knowledge.summary_for_prompt(url)
        except Exception:
            return ""

    def _interrupt(self, spec: JobSpec, message: str, code: str) -> None:
        spec.status = JobStatus.INTERRUPTED
        spec.success = False
        spec.error_code = code
        spec.error = message
        self.store.append(spec.id, "interrupted", error_code=code, error=message[:300])
        logger.warning(f"Job {spec.id} interrupted: [{code}] {message}")

    def _finish(self, spec: JobSpec, started: float) -> None:
        spec.completed_at = now_iso()
        spec.duration_ms = spec.duration_ms or int((time.time() - started) * 1000)
        self.store.save_spec(spec)

        final_shot = ""
        try:
            b64 = self.agent.server.session.browser.last_screenshot
            if b64:
                rel = self.store.save_screenshot(spec.id, b64, "final")
                final_shot = rel or ""
        except Exception:
            pass
        self.store.append(spec.id, "job_finished", status=spec.status.value,
                          success=spec.success, error_code=spec.error_code,
                          screenshot=final_shot)

        # Distil the journal into an ordered, replayable recipe.json. Written for
        # every job (even failed ones — a partial recipe is still a starting
        # point), and never allowed to break the run.
        try:
            recipe = build_recipe(spec.id, self.store,
                                  objective=spec.objective, start_url=spec.start_url)
            save_recipe(recipe, self.store)
            self.store.append(spec.id, "recipe_saved", steps=len(recipe.steps))
            logger.info(f"Recipe: jobs/{spec.id}/recipe.json ({len(recipe.steps)} steps)")
        except Exception as e:
            logger.warning(f"Recipe distillation failed: {e}")

        try:
            report_path = self.report(spec.id)
            logger.info(f"Report: {report_path}")
        except Exception as e:
            logger.warning(f"Report generation failed: {e}")
