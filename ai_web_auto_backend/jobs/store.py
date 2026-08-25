"""Filesystem persistence for jobs: job.json, journal.jsonl, screenshots.

Layout (all paths relative — jobs move, absolute paths don't):

    jobs/<id>/
      job.json          JobSpec (rewritten atomically on every update)
      journal.jsonl     append-only event log; the source of truth for resume
      screenshots/      failure + baseline captures, referenced from the journal
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterator, Optional

from loguru import logger

from .models import JobSpec, JobStatus, now_iso


class JobStore:
    def __init__(self, root: str | Path = "jobs"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def dir(self, job_id: str) -> Path:
        return self.root / job_id

    # ── Spec ──────────────────────────────────────────────────────────────

    def create(self, objective: str, start_url: str, max_steps: int,
               max_runtime_s: int, explore: bool = False) -> JobSpec:
        spec = JobSpec(
            objective=objective,
            start_url=start_url,
            max_steps=max_steps,
            max_runtime_s=max_runtime_s,
            explore=explore,
        )
        d = self.dir(spec.id)
        d.mkdir(parents=True, exist_ok=False)
        (d / "screenshots").mkdir(exist_ok=True)
        self.save_spec(spec)
        return spec

    def save_spec(self, spec: JobSpec) -> None:
        path = self.dir(spec.id) / "job.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(spec.to_dict(), indent=2), encoding="utf-8")
        os.replace(tmp, path)  # atomic on Windows and POSIX

    def load(self, job_id: str) -> JobSpec:
        path = self.dir(job_id) / "job.json"
        return JobSpec.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_jobs(self) -> list[JobSpec]:
        specs = []
        for d in sorted(self.root.iterdir()):
            f = d / "job.json"
            if not f.is_file():
                continue
            try:
                specs.append(JobSpec.from_dict(json.loads(f.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Skipping unreadable job dir {d.name}: {e}")
        return specs

    def find_resumable(self) -> list[JobSpec]:
        return [s for s in self.list_jobs()
                if s.status in (JobStatus.INTERRUPTED, JobStatus.FAILED)]

    # ── Journal ───────────────────────────────────────────────────────────

    def journal_path(self, job_id: str) -> Path:
        return self.dir(job_id) / "journal.jsonl"

    def append(self, job_id: str, event_type: str, **fields) -> None:
        record = {"ts": now_iso(), "type": event_type, **fields}
        with open(self.journal_path(job_id), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def read_journal(self, job_id: str) -> Iterator[dict]:
        path = self.journal_path(job_id)
        if not path.exists():
            return
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # A torn final write (crash mid-append) must not kill resume.
                    logger.warning("Skipping corrupt journal line")
                    continue

    def action_history(self, job_id: str) -> list[dict]:
        """Rebuild the AI agent's action history from result events."""
        history = []
        for ev in self.read_journal(job_id):
            if ev.get("type") == "action_result":
                history.append({
                    "action": ev.get("action", ""),
                    "target": {"method": "css", "value": ev.get("selector") or ""},
                    "status": "success" if ev.get("ok") else "failure",
                })
        return history

    # ── Screenshots ───────────────────────────────────────────────────────

    def save_screenshot(self, job_id: str, b64_png: str, label: str,
                        max_per_job: int = 20) -> Optional[str]:
        """Persist base64 PNG under the job dir; returns the relative path."""
        import base64
        shots_dir = self.dir(job_id) / "screenshots"
        existing = len(list(shots_dir.glob("*.png")))
        if existing >= max_per_job:
            return None
        rel = f"screenshots/{label}.png"
        try:
            (shots_dir / f"{label}.png").write_bytes(base64.b64decode(b64_png))
            return rel
        except Exception as e:
            logger.warning(f"Screenshot save failed: {e}")
            return None

    # ── Checkpoint ────────────────────────────────────────────────────────

    def checkpoint(self, spec: JobSpec, steps_done: int) -> None:
        spec.steps_done = steps_done
        spec.last_checkpoint_at = now_iso()
        self.save_spec(spec)
