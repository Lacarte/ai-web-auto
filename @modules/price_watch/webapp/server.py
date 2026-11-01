"""Deal monitor: a small local page for the scheduled build-deals check (http://127.0.0.1:8791).

    venv\\Scripts\\python @modules\\price_watch\\webapp\\server.py [--port 8791] [--open]
    run-deal-monitor.bat   (starts it if needed and opens the page)

Shows the Task Scheduler state (next / last run), every run with its steps (STEP lines of run-build-deals.bat's log),
the live log of a run in progress, the opportunities of the last cycle (deals at or under the deal prices) with a chime
when a new one appears, and the best price per product. Read-only, except "Run now", which asks Task Scheduler to start
the registered task (same thing as the 06:00 / 12:00 / 18:00 runs: searches only, never buys). Bound to 127.0.0.1."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

HERE = Path(__file__).resolve().parent
MOD = HERE.parent
DL = MOD / "@downloads"
WATCH_DIR = DL / "watch-build-deals"
REPORTS = DL / "build-deals-reports"
WATCH_FILE = MOD / "watches" / "build-deals.json"
CHIME = MOD / "sounds" / "opportunity.wav"
TASK = "AI Web-Auto build deals"
STEPS = {1: "Price watch", 2: "Option prices", 3: "Claude report"}
NOISE = re.compile(r"\|\s*(DEBUG|INFO)\s*\||^\s*$")
RUN_STALE_H = 3.5  # the task's time limit is 3 h: a log without DONE that is older than this was stopped
# The server runs under pythonw (no console): without this flag every powershell / schtasks call it makes opens a
# console window of its own - once a minute while the page is open (seen 2026-10-30)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ── data (pure: testable without a server) ───────────────────────────────────

def _json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def parse_log(path: Path, now: float | None = None) -> dict:
    """One run of run-build-deals.bat from its log: time, step reached, exit codes, status, report, deals, log tail."""
    text = path.read_text(encoding="utf-8", errors="replace")
    ts = path.stem.replace("run-", "")
    steps = [int(m) for m in re.findall(r"^STEP (\d)/3", text, re.M)]
    codes = {k: int(v) for k, v in re.findall(r"^(price_watch|config_check|claude) exit code (-?\d+)", text, re.M)}
    done = bool(re.search(r"^DONE\b", text, re.M))
    age_h = ((now or time.time()) - path.stat().st_mtime) / 3600
    if not done:
        status = "running" if age_h < RUN_STALE_H else "stopped"
    else:
        status = "ok" if codes and all(v == 0 for v in codes.values()) else "warn"
    blocked = re.findall(r"(WAITING: [^\n]+|Blocked[^\n]*|robot check[^\n]*|preflight failed[^\n]*)", text)
    report = REPORTS / f"report-{ts}.html"
    snap = _json(REPORTS / f"compare-{ts}.json", None)
    lines = [l for l in text.splitlines() if not NOISE.search(l)]
    return {"id": ts, "log": path.name, "started": _when(ts), "step": max(steps) if steps else 0,
            "step_name": STEPS.get(max(steps)) if steps else "starting", "exit_codes": codes, "done": done,
            "status": status, "report": report.name if report.exists() else None,
            "deals": len(snap.get("deals", [])) if snap else None, "notes": blocked[-3:], "tail": lines[-60:]}


def _when(ts: str) -> str | None:
    try:
        return datetime.strptime(ts, "%Y-%m-%d-%H%M").isoformat(timespec="minutes")
    except ValueError:
        return None


def runs(limit: int = 25) -> list[dict]:
    logs = sorted(REPORTS.glob("run-*.log"), reverse=True)[:limit] if REPORTS.exists() else []
    return [parse_log(p) for p in logs]


def opportunities() -> dict:
    cmp_ = _json(WATCH_DIR / "compare.json", {})
    return {"at": cmp_.get("at"), "cycle": cmp_.get("cycle"), "deals": cmp_.get("deals", [])}


def products() -> list[dict]:
    cmp_ = _json(WATCH_DIR / "compare.json", {})
    watch = _json(WATCH_FILE, {"products": {}})
    out = []
    for p in cmp_.get("products", []):
        cfg = watch["products"].get(p["product"], {})
        best = p.get("best") or {}
        out.append({"product": p["product"], "matches": p.get("matches", 0), "deal_price": cfg.get("deal_price"),
                    "max_price": cfg.get("max_price"), "best": {k: best.get(k) for k in ("store", "total", "landed_est", "url", "title")} if best else None,
                    "by_store": {s: {"total": o.get("total"), "url": o.get("url")} for s, o in (p.get("by_store") or {}).items()},
                    "auctions": len(p.get("auctions") or [])})
    configured = [x for x in _json(WATCH_DIR / "configured.json", {}).get("items", []) if x.get("confirmed")]
    for x in out:
        x["configured"] = [{k: c.get(k) for k in ("url", "title", "page_price", "shipping", "options", "checked_at")}
                           for c in configured if c.get("product") == x["product"]][:4]
    return out


_sched_cache: dict = {"at": 0.0, "data": None}


def schedule() -> dict:
    """Task Scheduler state of the registered task (cached 60 s; PowerShell is slow)."""
    if time.time() - _sched_cache["at"] < 60 and _sched_cache["data"]:
        return _sched_cache["data"]
    ps = (f"$t = Get-ScheduledTask -TaskName '{TASK}' -ErrorAction Stop; $i = $t | Get-ScheduledTaskInfo; "
          "[pscustomobject]@{state=[string]$t.State; next=$i.NextRunTime.ToString('s'); last=$i.LastRunTime.ToString('s'); "
          "result=$i.LastTaskResult; times=@($t.Triggers | ForEach-Object { ([datetime]$_.StartBoundary).ToString('HH:mm') })} | ConvertTo-Json -Compress")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True,
                           timeout=20, creationflags=NO_WINDOW)
        data = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else {"state": "not registered"}
    except Exception as ex:
        data = {"state": "unknown", "error": str(ex)[:120]}
    if data.get("last", "").startswith("1999"):
        data["last"] = None  # never run
    _sched_cache.update(at=time.time(), data=data)
    return data


def status() -> dict:
    watch = _json(WATCH_FILE, {"products": {}})
    rs = runs()
    return {"now": datetime.now().isoformat(timespec="seconds"), "schedule": schedule(), "runs": rs,
            "running": next((r for r in rs if r["status"] == "running"), None), "opportunities": opportunities(),
            "products": products(), "deal_prices": {n: p.get("deal_price") for n, p in watch["products"].items() if p.get("deal_price")}}


def start_run() -> tuple[bool, str]:
    if any(r["status"] == "running" for r in runs(3)):
        return False, "a run is already in progress"
    r = subprocess.run(["schtasks", "/Run", "/TN", TASK], capture_output=True, text=True, creationflags=NO_WINDOW)
    _sched_cache["at"] = 0.0
    return r.returncode == 0, (r.stdout or r.stderr).strip()[:200]


# ── server ───────────────────────────────────────────────────────────────────

SAFE_REPORT = re.compile(r"^(report|run|compare)-\d{4}-\d{2}-\d{2}-\d{4}\.(html|log|json)$")
SAFE_FONT = re.compile(r"^[\w.-]+\.woff2$")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, ctype: str):
        if path.exists() and path.is_file():
            self._send(200, path.read_bytes(), ctype)
        else:
            self._send(404, b"not found", "text/plain")

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path in ("/", "/index.html"):
            return self._file(HERE / "index.html", "text/html; charset=utf-8")
        if path == "/api/status":
            return self._send(200, json.dumps(status(), ensure_ascii=False).encode(), "application/json")
        if path == "/sound/opportunity.wav":
            from sounds import make_chime
            return self._file(make_chime(), "audio/wav")
        if path.startswith("/fonts/") and SAFE_FONT.match(path[7:]):
            return self._file(HERE / "fonts" / path[7:], "font/woff2")
        if path.startswith("/reports/") and SAFE_REPORT.match(path[9:]):
            ext = path.rsplit(".", 1)[-1]
            return self._file(REPORTS / path[9:], {"html": "text/html; charset=utf-8", "log": "text/plain; charset=utf-8",
                                                   "json": "application/json"}[ext])
        if path == "/dashboard":
            return self._file(WATCH_DIR / "dashboard.html", "text/html; charset=utf-8")
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if urlparse(self.path).path != "/api/run":
            return self._send(404, b"not found", "text/plain")
        if self.headers.get("Origin") not in (None, f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"):
            return self._send(403, b"forbidden", "text/plain")  # only this page may start a run
        ok, msg = start_run()
        self._send(200 if ok else 409, json.dumps({"ok": ok, "message": msg}).encode(), "application/json")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--open", action="store_true", help="open the page in the browser")
    a = ap.parse_args(argv)
    sys.path.insert(0, str(MOD))
    url = f"http://127.0.0.1:{a.port}/"
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    except OSError:  # already running: just open it
        if a.open:
            webbrowser.open(url)
        print(f"deal monitor already running at {url}")
        return 0
    print(f"deal monitor at {url} (Ctrl+C to stop)", flush=True)
    if a.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
