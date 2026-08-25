"""Job data model, structured error codes, and error classification."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_job_id() -> str:
    # Timestamp-prefixed so directory listings sort chronologically.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


class JobStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"   # crashed or budget exhausted mid-run; resumable
    CANCELLED = "cancelled"


# Stable machine codes — free text lives in `error`, never in switch logic.
ERROR_CODES = {
    "NAV_FAILED": "navigate command failed",
    "ELEMENT_NOT_FOUND": "selector/xpath resolved to nothing",
    "TIMEOUT": "command exceeded its time budget",
    "WAIT_TIMEOUT": "wait condition never became true",
    "SCRIPT_ERROR": "execute_script threw in the page",
    "SCRIPT_TIMEOUT": "execute_script promise never settled",
    "NO_TARGET_TAB": "no browser tab bound for automation",
    "NO_CONNECTION": "extension disconnected",
    "DOMAIN_BLOCKED": "safety policy blocked the domain",
    "LLM_FAILED": "AI decision layer failed after retries",
    "MAX_STEPS": "step budget exhausted",
    "CANCELLED": "cancelled by caller",
    "INTERNAL": "unclassified failure",
}

_PATTERNS: list[tuple[str, str]] = [
    # Order matters: first match wins, most specific patterns first.
    (r"element not found", "ELEMENT_NOT_FOUND"),
    (r"xpath not found", "ELEMENT_NOT_FOUND"),
    (r"max steps \(", "MAX_STEPS"),
    (r"script timed out", "SCRIPT_TIMEOUT"),
    (r"wait .*timed out", "WAIT_TIMEOUT"),
    (r"timed? ?out|timeout", "TIMEOUT"),
    (r"no target tab", "NO_TARGET_TAB"),
    (r"not connected|disconnected|connectionerror", "NO_CONNECTION"),
    (r"domain blocked", "DOMAIN_BLOCKED"),
    (r"ai failed to decide", "LLM_FAILED"),
]


def classify_error(error: Optional[str]) -> str:
    """Map a free-text failure to one stable ERROR_CODES entry."""
    if not error:
        return "UNKNOWN"
    low = error.lower()
    for pattern, code in _PATTERNS:
        if re.search(pattern, low):
            return code
    return "UNKNOWN"


@dataclass
class JobSpec:
    """Everything needed to run, resume, and report a job. Persisted as job.json."""
    id: str = field(default_factory=new_job_id)
    objective: str = ""
    start_url: str = ""
    status: JobStatus = JobStatus.RUNNING
    max_steps: int = 40
    max_runtime_s: int = 1200
    explore: bool = False
    created_at: str = field(default_factory=now_iso)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    last_checkpoint_at: Optional[str] = None
    steps_done: int = 0            # checkpoint: last step fully journaled
    resume_count: int = 0
    success: Optional[bool] = None
    error_code: Optional[str] = None
    error: Optional[str] = None
    result_data: dict = field(default_factory=dict)
    duration_ms: int = 0

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "JobSpec":
        d = dict(d)
        d["status"] = JobStatus(d.get("status", JobStatus.INTERRUPTED.value))
        spec = cls()
        for k, v in d.items():
            if hasattr(spec, k):
                setattr(spec, k, v)
        return spec
