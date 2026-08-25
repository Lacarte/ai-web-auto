"""Cross-job site knowledge: which selectors actually worked, per domain.

The store is deliberately dumb — a JSON file of selector attempts with
success/failure counters. Entries that keep failing without ever succeeding
are pruned, so the file converges on what works instead of accumulating
everything ever tried.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from loguru import logger

from .models import now_iso


def domain_of(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
        return netloc.split("@")[-1].split(":")[0] or "unknown"
    except Exception:
        return "unknown"


def normalize_path(url: str) -> str:
    """Scheme+host+path with trailing slash and query stripped — enough to
    answer 'am I already where I was asked to go' without false negatives."""
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}{p.path.rstrip('/')}".lower()
    except Exception:
        return (url or "").strip().lower()


class SiteKnowledge:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, domain: str) -> Path:
        return self.root / f"{re.sub(r'[^a-z0-9.-]', '_', domain)}.json"

    def _load(self, domain: str) -> dict:
        path = self._path(domain)
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Knowledge file for {domain} unreadable ({e}); starting fresh")
        return {"domain": domain, "updated": None, "selectors": {}}

    def _save(self, domain: str, data: dict) -> None:
        data["domain"] = domain
        data["updated"] = now_iso()
        tmp = self._path(domain).with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self._path(domain))

    @staticmethod
    def _key(action: str, selector: str) -> str:
        return f"{action}:{selector}"

    def record(self, url: str, action: str, selector: str, ok: bool,
               error_code: Optional[str] = None) -> None:
        if not selector or len(selector) > 500:
            return
        domain = domain_of(url)
        if domain == "unknown":
            return
        data = self._load(domain)
        key = self._key(action, selector)
        entry = data["selectors"].setdefault(key, {
            "action": action,
            "selector": selector,
            "ok": 0,
            "fail": 0,
            "last_error_code": None,
            "last_used": None,
            "last_url_path": None,
        })
        if ok:
            entry["ok"] += 1
        else:
            entry["fail"] += 1
        entry["last_error_code"] = None if ok else error_code
        entry["last_used"] = now_iso()
        entry["last_url_path"] = urlparse(url).path[:200]
        self._prune(data)
        self._save(domain, data)

    @staticmethod
    def _prune(data: dict) -> None:
        # Never-worked selectors that failed 3+ times are noise; drop them.
        for key in [k for k, v in data["selectors"].items()
                    if v["ok"] == 0 and v["fail"] >= 3]:
            del data["selectors"][key]

    def best_for(self, url: str, limit: int = 12) -> list[dict]:
        """Highest-confidence known selectors for the URL's domain."""
        domain = domain_of(url)
        if domain == "unknown":
            return []
        data = self._load(domain)
        entries = sorted(
            data["selectors"].values(),
            key=lambda e: (e["ok"] - e["fail"], e["ok"], e.get("last_used") or ""),
            reverse=True,
        )
        return [e for e in entries if e["ok"] > 0][:limit]

    def summary_for_prompt(self, url: str, limit: int = 10) -> str:
        best = self.best_for(url, limit=limit)
        if not best:
            return ""
        lines = ["KNOWN-GOOD SELECTORS previously verified on this site:"]
        for e in best:
            lines.append(
                f"  - {e['action']} `{e['selector']}` "
                f"(worked {e['ok']}x, failed {e['fail']}x)"
            )
        lines.append("Prefer these over rediscovery when they match the goal.")
        return "\n".join(lines)

    def stats(self) -> dict[str, dict]:
        out = {}
        for path in self.root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                sel = data.get("selectors", {})
                out[data.get("domain", path.stem)] = {
                    "selectors": len(sel),
                    "proven": sum(1 for s in sel.values() if s["ok"] > 0),
                }
            except (json.JSONDecodeError, OSError):
                continue
        return out
