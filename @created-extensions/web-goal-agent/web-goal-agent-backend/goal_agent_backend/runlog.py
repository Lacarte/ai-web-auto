"""Structured per-run logs (JSONL) with secret redaction.

Every record passes through the Redactor: secret input values are replaced
wherever they appear, and values under credential-like keys are masked.
Cookies, storage and tokens are never requested by the engine in the first
place; the key masking is a second line of defence.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

MASK = "•••"
_SENSITIVE_KEY = re.compile(r"(pass(word)?|secret|token|cookie|authorization|api[_-]?key|session)", re.I)


class Redactor:
    def __init__(self, secrets: Iterable[str] = ()):
        self._secrets = sorted({s for s in secrets if s}, key=len, reverse=True)

    def add(self, secret: str):
        if secret and secret not in self._secrets:
            self._secrets = sorted({*self._secrets, secret}, key=len, reverse=True)

    def text(self, s: str) -> str:
        for secret in self._secrets:
            s = s.replace(secret, MASK)
        return s

    def obj(self, o: Any) -> Any:
        if isinstance(o, str):
            return self.text(o)
        if isinstance(o, dict):
            return {k: (MASK if _SENSITIVE_KEY.search(str(k)) and isinstance(v, str) and v else self.obj(v))
                    for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [self.obj(x) for x in o]
        return o


class RunLog:
    def __init__(self, runs_dir: Path, run_id: str, redactor: Redactor):
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.dir = runs_dir / day
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{run_id}.jsonl"
        self.run_id = run_id
        self.redactor = redactor

    def write(self, kind: str, **fields: Any) -> dict[str, Any]:
        record = {"ts": datetime.now(timezone.utc).isoformat(), "run_id": self.run_id, "kind": kind,
                  **self.redactor.obj(fields)}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return record

    def save_screenshot(self, name: str, b64_png: str) -> Path | None:
        if not b64_png:
            return None
        if b64_png.startswith("data:"):
            b64_png = b64_png.split(",", 1)[1]
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:60]
        out = self.dir / f"{self.run_id}-{safe}.png"
        out.write_bytes(base64.b64decode(b64_png))
        return out
