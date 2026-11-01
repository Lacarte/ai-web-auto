"""Live status of a viral-research run, in a Chrome/Edge app window (same approach as Seamless-app's dashboard).

A tiny HTTP server bound to 127.0.0.1 serves one page that polls /api/state every second.

Where the status comes from:
- runs started by this version write `events.jsonl` (stage start/end, done/total counters, classified errors,
  a heartbeat every 15 s, the final outcome) and `run.pid`; `reduce_events()` replays them (pure, tested);
- older runs only have `log.txt`: the stages come from its lines, liveness from the process table.
Stop ends the pipeline process (a rerun continues where it stopped).

    venv\\Scripts\\python @modules\\viral_research\\viral_research.py dashboard [--niche ai-music] [--run-dir DIR]
"""
from __future__ import annotations

import ctypes
import json
import mimetypes
import os
import re
import secrets
import subprocess
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

HERE = Path(__file__).resolve().parent
PAGE = HERE / "dashboard.html"
# (key, title, what the stage does and why - shown on the dashboard next to its live numbers)
STAGES = [
    ("discover", "Discover videos",
     "Searches YouTube for the niche: every query and hashtag page, in each language. One request per search; "
     "it collects video IDs, views and channels."),
    ("baseline", "Channel baselines",
     "Reads each channel's own video list (one request per channel) to learn its normal: the median views of "
     "its recent videos, per format. A video only counts as viral against its own channel's normal."),
    ("score", "Score + Shorts tabs",
     "Divides every video's views by its channel's normal: 3x or more is an outlier. Drops other languages, "
     "off-niche videos and channels with too little history. No YouTube requests."),
    ("shortlist", "Shortlist",
     "Keeps the strongest outliers per ranking (at most 3 per channel) and ordinary videos of the same channels "
     "as controls, then fetches their full details to confirm format, date, language and niche, and to exclude "
     "paid or sponsored views."),
    ("evidence", "Download + measure",
     "Thumbnails for every shortlisted video. For the top 8 outliers and 8 controls per ranking: the opening "
     "of the video, captions and comments, measured with ffmpeg (cuts per minute, loudness, silence) and "
     "Whisper when there are no captions."),
    ("vision", "Local vision labels",
     "Your local vision model (MiniCPM-V on llama-server, on this computer) reads each thumbnail and opening "
     "frame: written text, a face, the visual style, the subject. No internet."),
    ("analyze", "Patterns + back-test",
     "Compares outliers with the controls feature by feature. A pattern needs a 20-point gap, 3+ outliers and "
     "2+ channels; the back-test checks that it also held on the older half of the videos."),
    ("check", "Spot-check",
     "Re-reads a few view counts from YouTube's watch pages to confirm the numbers yt-dlp returned. Skipped "
     "while YouTube refuses requests."),
    ("pack", "Evidence pack",
     "Writes pack.md: every number with the file it came from. Claude writes the report from it."),
]
KEYS = [k for k, _, _ in STAGES]
TITLES = {k: t for k, t, _ in STAGES}
IDLE_EXIT_SECONDS = 900    # a dashboard process with no open window and no running pipeline exits after this
STALL_SECONDS = 300        # alive, but no progress for this long -> "stalled" (usually YouTube rate limits)
RATE_LIMIT_WINDOW = 600
STAGE_LINE = re.compile(r"^(\d\d):(\d\d):(\d\d) (\d)\. (\w+)")
BROWSERS = [r"%ProgramFiles%\Google\Chrome\Application\chrome.exe", r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
            r"%LocalAppData%\Google\Chrome\Application\chrome.exe", r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
            r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"]


def pid_alive(pid: int) -> bool:
    """Windows-safe liveness check (os.kill(pid, 0) would terminate the process on Windows)."""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = ctypes.c_ulong()
    ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(handle)
    return bool(ok) and code.value == 259  # STILL_ACTIVE


_PROC_CACHE: dict[str, tuple[float, bool]] = {}


def pipeline_process_running(niche: str) -> bool:
    """For runs without run.pid: is a `viral_research.py run --niche <niche>` process in the process table?"""
    hit = _PROC_CACHE.get(niche)
    if hit and time.time() - hit[0] < 10:
        return hit[1]
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -match 'viral_research\\.py\"? run' -and $_.CommandLine -match '--niche " + re.escape(niche) + "' } | "
          "Measure-Object | Select-Object -ExpandProperty Count")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=20,
                             creationflags=NO_WINDOW).stdout
        alive = int((out or "0").strip() or 0) > 0
    except Exception:
        alive = False
    _PROC_CACHE[niche] = (time.time(), alive)
    return alive


def _j(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
    except (json.JSONDecodeError, OSError):
        return default


NO_WINDOW = 0x08000000   # CREATE_NO_WINDOW: the dashboard runs detached; a console child would pop up a window
SETTINGS = (_j(HERE / "module.json", {}) or {}).get("settings", {})
PROCESS_KINDS = [
    ("pipeline", "Research run", "viral_research.py run: works through the stages below"),
    ("downloads", "YouTube requests", "yt-dlp: searches, channel lists, video details, media"),
    ("measure", "Measuring", "ffmpeg / ffprobe: cuts, loudness, silence, frames"),
    ("whisper", "Speech to text", "faster-whisper: the opening words of videos without captions"),
    ("vision", "Local vision model", "llama-server + MiniCPM-V: reads thumbnails and opening frames"),
    ("dashboard", "Dashboard", "this page's server"),
]
PS_PROCESSES = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='llama-server.exe' OR "
                "Name='yt-dlp.exe' OR Name='ffmpeg.exe' OR Name='ffprobe.exe'\" | "
                "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress")


def classify_processes(rows: list[dict], settings: dict) -> list[dict]:
    """This module's processes by the work they do, from Windows process-table rows (pure)."""
    vision = settings.get("vision") or {}
    model = Path(vision.get("model") or "no-model").name.lower()
    server = (vision.get("server") or "no-server").lower()   # other apps may serve the same model (another llama.cpp)
    ffmpeg_dir = (settings.get("ffmpeg_dir") or "no-dir").lower()
    seen, found = set(), {k: [] for k, _, _ in PROCESS_KINDS}
    for r in rows:
        name, cmd = (r.get("Name") or "").lower(), r.get("CommandLine") or ""
        args = re.sub(r'^\s*("[^"]*"|\S+)\s*', "", cmd)   # the venv's python.exe is a shim: same arguments twice
        if name == "python.exe":
            if (name, args) in seen:
                continue
            seen.add((name, args))
        kind = None
        if name == "python.exe" and re.search(r'viral_research\.py"?\s+run\b', args):
            kind = "pipeline"
        elif name == "python.exe" and re.search(r'viral_research\.py"?\s+dashboard\b', args):
            kind = "dashboard"
        elif name == "python.exe" and "whisper_worker.py" in args:
            kind = "whisper"
        elif name == "yt-dlp.exe":
            kind = "downloads"
        elif name in ("ffmpeg.exe", "ffprobe.exe") and ffmpeg_dir in cmd.lower():
            kind = "measure"
        elif name == "llama-server.exe" and model in cmd.lower() and server in cmd.lower():
            kind = "vision"
        if kind:
            port = re.search(r"--port\s+(\d+)", args)
            found[kind].append(f"port {port.group(1)}" if kind == "vision" and port else str(r.get("ProcessId")))
    return [{"key": k, "name": n, "about": a, "count": len(found[k]), "detail": ", ".join(found[k][:4])}
            for k, n, a in PROCESS_KINDS]


def gate_info(gate: dict | None, now: float, max_blocked: float) -> dict:
    """YouTube access from gate.json (the bot-check pause the pipeline saves)."""
    if not gate or not gate.get("trips"):
        return {"state": "ok", "pacing": SETTINGS.get("min_seconds_between_requests", 1.5)}
    until, since = gate.get("until", 0), gate.get("since") or now
    return {"state": "paused" if until > now else "retrying", "until": until, "attempts": gate["trips"],
            "since": since, "gives_up_after": since + max_blocked}


def level(text: str) -> str:
    """Per-item problems are indented 4 spaces in log.txt; stage summaries ("3 excluded") are not warnings."""
    if re.search(r"error|failed|traceback|429|not a bot|exception", text, re.I):
        return "error"
    if re.search(r"^\S+ {5}\S|no listing|unavailable|stopped from", text, re.I):
        return "warn"
    return "info"


# ── status from events (pure) ────────────────────────────────────────────────

def read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue   # a line being written right now
    return out


def reduce_events(events: list[dict], now: float, alive: bool) -> dict | None:
    """The latest run's status from its events. alive: is the pipeline process (run_start pid) still running?"""
    starts = [e for e in events if e.get("kind") == "run_start"]
    if not starts:
        return None
    run = starts[-1]["run"]
    ev = [e for e in events if e.get("run") == run]
    stages = {k: {"key": k, "title": t, "about": a, "status": "pending", "started": None, "seconds": None} for k, t, a in STAGES}
    current, end, stopped, last_progress, blocked_until, requests = None, None, None, ev[0]["t"], None, None
    progress: dict[str, list[dict]] = {}
    errors: list[dict] = []
    for e in ev:
        kind, t = e["kind"], e["t"]
        if e.get("requests") is not None:
            requests = e["requests"]
        if kind == "stage_start" and e.get("stage") in stages:
            current = e["stage"]
            stages[current].update(status="running", started=t)
            last_progress = t
        elif kind == "stage_end" and e.get("stage") in stages:
            s = stages[e["stage"]]
            s.update(status="done", seconds=round(t - (s["started"] or t)))
            last_progress = t
        elif kind == "progress":
            progress.setdefault(e.get("stage") or "", []).append(e)
            last_progress = t
        elif kind == "error":
            errors.append(e)
        elif kind == "run_end":
            end = e
        elif kind == "stopped":
            stopped = e
        elif kind == "blocked":
            blocked_until = e.get("until")
            last_progress = t   # a deliberate pause is not a stall
    if end and end.get("status") in ("finished", "blocked", "incomplete"):
        status = end["status"]
    elif end:
        status = "stopped" if "KeyboardInterrupt" in (end.get("error") or "") else "failed"
    elif stopped:
        status = "stopped"
    elif not alive:
        status = "crashed"
    elif blocked_until and blocked_until > now:
        status = "waiting"     # paused by YouTube's bot check; retries automatically at blocked_until
    elif now - last_progress > STALL_SECONDS:
        status = "stalled"
    else:
        status = "running"
    for s in stages.values():
        if s["status"] == "running":
            if status in ("running", "stalled", "waiting"):
                s["seconds"] = round(now - s["started"])
            else:
                s["status"] = "stopped" if status in ("stopped", "finished") else "failed"
    task = None
    if current and status in ("running", "stalled", "waiting") and progress.get(current):
        last = progress[current][-1]
        same = [p for p in progress[current] if p["label"] == last["label"]]
        task = {"label": last["label"], "done": last["done"], "total": last["total"],
                "pct": round(100 * last["done"] / last["total"]) if last["total"] else None, "eta": None}
        recent = [p for p in same if p["t"] >= last["t"] - 300] or same   # rate over the last 5 minutes
        first = recent[0]
        if last["done"] > first["done"] and last["t"] > first["t"]:
            rate = (last["done"] - first["done"]) / (last["t"] - first["t"])
            task["eta"] = round((last["total"] - last["done"]) / rate)
    elif current and status in ("running", "stalled", "waiting"):
        task = {"label": stages[current]["title"], "done": None, "total": None, "pct": None, "eta": None}
    by_kind: dict[str, int] = {}
    for e in errors:
        by_kind[e.get("category", "other")] = by_kind.get(e.get("category", "other"), 0) + 1
    rate_limited = sum(1 for e in errors if e.get("category") == "rate_limit" and now - e["t"] < RATE_LIMIT_WINDOW)
    return {"status": status, "stages": list(stages.values()), "task": task, "started": ev[0]["t"],
            "elapsed": round((end["t"] if end else now) - ev[0]["t"]), "pid": starts[-1].get("pid"),
            "errors": {"total": len(errors), "by_kind": by_kind, "recent_rate_limits": rate_limited,
                       "last": [{"category": e.get("category"), "message": e.get("message")} for e in errors[-5:]]},
            "idle_seconds": round(now - last_progress), "end": end, "blocked_until": blocked_until, "requests": requests,
            "end_reason": (end or {}).get("error")}


# ── status from an older run's log.txt ───────────────────────────────────────

def log_times(lines: list[str], last_write: float) -> list[float | None]:
    """Absolute times for "HH:MM:SS ..." lines, safe across midnight: the last line is anchored to the file's
    modification time and earlier lines count back from it (a clock that goes down means a new day)."""
    secs, day, prev = [], 0, None
    for l in lines:
        m = re.match(r"(\d\d):(\d\d):(\d\d)", l)
        if not m:
            secs.append(None)
            continue
        s = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        if prev is not None and s < prev - 60:
            day += 1
        prev = s
        secs.append(s + day * 86400)
    known = [s for s in secs if s is not None]
    if not known:
        return [None] * len(lines)
    return [None if s is None else last_write - (known[-1] - s) for s in secs]


def reduce_log(lines: list[str], times: list[float | None], now: float, alive: bool) -> dict:
    starts = {}
    for l, t in zip(lines, times):
        m = STAGE_LINE.match(l)
        if m and m.group(5) in KEYS and t is not None:
            starts[m.group(5)] = t
    finished = any(re.match(r"\d\d:\d\d:\d\d +\S*pack\.md", l) for l in lines)
    status = "finished" if finished else ("running" if alive else ("stopped" if lines else "waiting"))
    current = max((KEYS.index(k) for k in starts), default=-1)
    stages = []
    for i, (key, title, about) in enumerate(STAGES):
        nxt = next((starts[k] for k in KEYS[i + 1:] if k in starts), None)
        if key not in starts:
            st, secs = "pending", None
        elif i < current or finished:
            st, secs = "done", round((nxt or now) - starts[key]) if nxt else None
        else:
            st, secs = ("running", round(now - starts[key])) if alive else ("stopped", None)
        stages.append({"key": key, "title": title, "about": about, "status": st, "started": starts.get(key), "seconds": secs})
    first = next((t for t in times if t is not None), None)
    task = {"label": TITLES[KEYS[current]], "done": None, "total": None, "pct": None, "eta": None} \
        if status == "running" and current >= 0 else None
    return {"status": status, "stages": stages, "task": task, "started": first,
            "elapsed": round(now - first) if first else None, "pid": None,
            "errors": {"total": sum(1 for l in lines if level(l) == "error"), "by_kind": {}, "recent_rate_limits": 0, "last": []},
            "idle_seconds": None, "end": None}


# ── the dashboard's view of a run folder ─────────────────────────────────────

class Watcher:
    def __init__(self, run_dir: Path, niche: str):
        self.dir = run_dir
        self.niche = niche
        self._cache: dict[str, tuple[tuple, object]] = {}
        self._processes: list[dict] = classify_processes([], SETTINGS)
        self._process_thread: threading.Thread | None = None

    def _watch_processes(self) -> None:
        """The process table every 10 s, in the background (a PowerShell query takes about a second)."""
        while True:
            try:
                out = subprocess.run(["powershell", "-NoProfile", "-Command", PS_PROCESSES], capture_output=True, text=True,
                                     encoding="utf-8", errors="replace", timeout=30, creationflags=NO_WINDOW).stdout.strip()
                rows = json.loads(out) if out else []
                self._processes = classify_processes(rows if isinstance(rows, list) else [rows], SETTINGS)
            except Exception:
                pass
            time.sleep(10)

    def processes(self) -> list[dict]:
        if self._process_thread is None:
            self._process_thread = threading.Thread(target=self._watch_processes, daemon=True)
            self._process_thread.start()
        return self._processes

    def about(self) -> dict:
        """What this run is about: the niche, its searches and the method's limits (niche file + module.json)."""
        cfg = _j(HERE / "niches" / f"{self.niche}.json", {}) or {}
        langs = [k for k, v in cfg.items() if isinstance(v, dict)]
        vision = SETTINGS.get("vision") or {}
        return {"niche": cfg.get("niche", self.niche), "languages": langs,
                "queries": {l: cfg[l].get("queries", []) for l in langs},
                "hashtags": {l: cfg[l].get("hashtags", []) for l in langs},
                "outliers_per_bucket": SETTINGS.get("outliers_per_bucket"), "measure_per_bucket": SETTINGS.get("measure_per_bucket"),
                "max_age_days": SETTINGS.get("max_age_days"), "pacing": SETTINGS.get("min_seconds_between_requests"),
                "vision_model": Path(vision["model"]).stem if vision.get("enabled", True) and vision.get("model") else None,
                "whisper_model": SETTINGS.get("whisper_model"), "cookies": bool(SETTINGS.get("cookies_from_browser") or SETTINGS.get("cookies_file"))}

    def gate(self) -> dict:
        from platforms.youtube import GATE
        return gate_info(_j(self.dir / "gate.json"), time.time(), GATE.max_blocked)

    def facts(self) -> dict:
        """One line per stage with what the run folder holds so far."""
        from analysis import vision as V
        d, f = self.dir, {}
        disc = _j(d / "discovery.json", {}) or {}
        if disc:
            ids = {v["id"] for vs in disc.values() for v in vs}
            f["discover"] = f"{len(ids):,} videos found (" + ", ".join(f"{l}: {len(v):,}" for l, v in disc.items()) + ")"
        ch = _j(d / "channels.json", {}) or {}
        if ch:
            normal = sum(1 for c in ch.values() if any((c.get(t) or {}).get("median") for t in ("videos", "shorts")))
            f["baseline"] = f"{len(ch):,} channels read; {normal:,} have enough recent videos for a normal"
        sc = _j(d / "scored.json", {}) or {}
        if sc.get("rows") is not None:
            rows, drop = sc["rows"], sc.get("dropped") or {}
            f["score"] = (f"{len(rows):,} scored, {sum(r['tier'] == 'outlier' for r in rows):,} at 3x or more; dropped: "
                          + ", ".join(f"{k.replace('_', ' ')} {v:,}" for k, v in drop.items()))
        final = _j(d / "shortlist.json", {}) or {}
        if final:
            count = lambda g: sum(len(p.get(g, [])) for p in final.values())
            f["shortlist"] = (f"{count('outliers')} outliers + {count('controls')} controls in {len(final)} rankings; "
                              f"{count('excluded')} excluded by a check")
            ids = [r["id"] for p in final.values() for g in ("outliers", "controls") for r in p.get(g, [])]
            n = SETTINGS.get("measure_per_bucket", 8)
            budget = sum(min(n, len(p.get(g, []))) for p in final.values() for g in ("outliers", "controls"))
            ev = [d / "evidence" / i for i in ids]
            f["evidence"] = (f"{sum((e / 'thumbnail.jpg').exists() for e in ev)}/{len(ids)} thumbnails; "
                             f"{sum((e / 'measures.json').exists() for e in ev)}/{budget} videos measured")
            images = [(img, lab) for e in ev for img, lab in ((e / "thumbnail.jpg", e / "vision_thumbnail.json"),
                                                               (e / "frames" / "frame_001.0s.jpg", e / "vision_opening.json"))
                      if img.exists()]
            done = sum(1 for _, lab in images if len(V.reusable(_j(lab))) == len(V.QUESTIONS))
            f["vision"] = f"{done}/{len(images)} images labelled (thumbnails and opening frames)"
        an = _j(d / "analysis.json", {}) or {}
        if an:
            f["analyze"] = "; ".join(f"{b}: {sum(1 for c in a.get('comparison', []) if c.get('pattern'))} patterns"
                                     + (", back-test holds" if (a.get("backtest") or {}).get("holds") else "")
                                     for b, a in sorted(an.items()))
        spot = (_j(d / "spot_check.json", {}) or {}).get("results") or []
        if spot:
            ok = sum(1 for x in spot if x.get("drift") is not None and -0.02 <= x["drift"] <= 0.15)
            f["check"] = f"{ok}/{len(spot)} view counts confirmed by the watch page"
        if (d / "pack.md").exists():
            partial = "**PARTIAL" in (d / "pack.md").read_text(encoding="utf-8", errors="replace")[:3000]
            f["pack"] = "pack.md written" + (" - PARTIAL: the videos themselves are not measured yet" if partial else "")
        return f

    def _cached(self, name: str, deps: list[Path], build):
        key = tuple(p.stat().st_mtime if p.exists() else 0 for p in deps) + (int(time.time() // 5),)
        hit = self._cache.get(name)
        if hit and hit[0] == key:
            return hit[1]
        value = build()
        self._cache[name] = (key, value)
        return value

    def _log_lines(self) -> list[str]:
        p = self.dir / "log.txt"
        if not p.exists():
            return []
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max((i for i, l in enumerate(lines) if "run folder:" in l), default=0)   # the latest run
        return lines[start:]

    def state(self, since: int = 0) -> dict:
        now = time.time()
        lines = self._log_lines()
        events = read_events(self.dir / "events.jsonl")
        pid = next((e.get("pid") for e in reversed(events) if e.get("kind") == "run_start"), None)
        core = reduce_events(events, now, pid_alive(pid) if pid else False) if events else None
        if core is None:   # a run from before events.jsonl existed
            log = self.dir / "log.txt"
            times = log_times(lines, log.stat().st_mtime if log.exists() else now)
            core = reduce_log(lines, times, now, pipeline_process_running(self.niche))
        if core["status"] == "running" and core["task"] and core["task"]["total"] is None:
            core["task"] = self._task_from_files(core["task"], core["stages"])
        log = [{"seq": n, "text": l, "level": level(l)} for n, l in enumerate(lines) if n >= since and "Deprecated Feature" not in l]
        files = [self.dir / n for n in ("discovery.json", "channels.json", "scored.json", "shortlist.json", "analysis.json",
                                        "spot_check.json", "pack.md")]
        return {"niche": self.niche, "run_dir": str(self.dir), **{k: v for k, v in core.items() if k != "end"},
                "about": self._cached("about", [], self.about), "facts": self._cached("facts", files, self.facts),
                "processes": self.processes(), "gate": self.gate(),
                "buckets": self._cached("buckets", [self.dir / "scored.json", self.dir / "shortlist.json"], self._buckets),
                "gallery": self._cached("gallery", [self.dir / "shortlist.json"], self._gallery),
                "log": log[-400:], "next_seq": len(lines),
                "files": {name: f"/file?p={quote(name)}" for name in ("pack.md", "report.md", "report.html", "log.txt")
                          if (self.dir / name).exists()}}

    def _task_from_files(self, task: dict, stages: list[dict]) -> dict:
        """Older runs: count finished work in the run folder (cached for 5 s)."""
        running = next((s for s in stages if s["status"] == "running"), None)
        if running and running["key"] == "shortlist":
            lines = self._log_lines()
            m = next((re.search(r"full metadata for (\d+)", l) for l in reversed(lines) if "full metadata for" in l), None)
            if m and running["started"]:
                total = int(m.group(1))
                done = self._cached("meta_count", [], lambda: sum(
                    1 for f in os.scandir(self.dir / "meta") if f.stat().st_mtime >= running["started"] - 2))
                return {**task, "label": "Shortlisted videos (full details)", "done": min(done, total), "total": total,
                        "pct": round(100 * min(done, total) / total), "eta": None}
            return task
        if not running or running["key"] != "evidence":
            return task

        def count():
            final = _j(self.dir / "shortlist.json", {})
            ids = [r["id"] for p in final.values() for g in ("outliers", "controls") for r in p.get(g, [])]
            return len(ids), sum(1 for i in ids if (self.dir / "evidence" / i / "measures.json").exists())
        total, done = self._cached("evidence_count", [self.dir / "shortlist.json"], count)
        task = {**task, "label": "Videos downloaded and measured", "done": done, "total": total,
                "pct": round(100 * done / total) if total else None}
        elapsed = time.time() - (running["started"] or time.time())
        if done and elapsed > 10:
            task["eta"] = round((total - done) * elapsed / done)
        return task

    def _buckets(self) -> list[dict]:
        scored = _j(self.dir / "scored.json", {}).get("rows", [])
        final = _j(self.dir / "shortlist.json", {})
        out = []
        for b in sorted({r["bucket"] for r in scored} | set(final)):
            rows = [r for r in scored if r["bucket"] == b]
            p = final.get(b, {})
            ids = [r["id"] for g in ("outliers", "controls") for r in p.get(g, [])]
            out.append({"bucket": b, "scored": len(rows), "outliers": sum(r["tier"] == "outlier" for r in rows),
                        "shortlisted": len(p.get("outliers", [])), "controls": len(p.get("controls", [])),
                        "excluded": len(p.get("excluded", [])),
                        "measured": sum(1 for i in ids if (self.dir / "evidence" / i / "measures.json").exists())})
        return out

    def _gallery(self) -> list[dict]:
        final = _j(self.dir / "shortlist.json", {})
        cards = []
        for b, p in sorted(final.items()):
            for r in p.get("outliers", []):
                vid = r["id"]
                e = self.dir / "evidence" / vid
                meas = _j(e / "measures.json", {})
                vis = _j(e / "vision_thumbnail.json")
                file_url = lambda name: "/file?p=" + quote(f"evidence/{vid}/{name}") if (e / name).exists() else None
                cards.append({"id": vid, "bucket": b, "title": r.get("title"), "channel": r.get("channel"),
                              "multiplier": r.get("multiplier"), "views": r.get("views"), "url": r.get("url"),
                              "thumb": file_url("thumbnail.jpg"), "sheet": file_url("contact_sheet.jpg"),
                              "cuts_per_min": meas.get("cuts_per_min"), "starts_loud": meas.get("starts_loud"),
                              "measured": bool(meas),
                              "vision": {"text": bool(vis.get("text_words")), "face": vis.get("face"), "style": vis.get("style")}
                              if vis else None})
        return cards


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _local(self) -> bool:
        """Refuse requests not addressed to this machine (DNS rebinding)."""
        return (self.headers.get("Host") or "").split(":")[0] in ("127.0.0.1", "localhost")

    def do_GET(self):
        if not self._local():
            return self._send(403, b"forbidden", "text/plain")
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path == "/":
            page = PAGE.read_text(encoding="utf-8").replace("__TOKEN__", self.server.token)
            return self._send(200, page.encode(), "text/html; charset=utf-8")
        if url.path == "/api/state":
            self.server.last_poll = time.time()   # an open window polls every second
            state = self.server.watcher.state(int(q.get("since", ["0"])[0]))
            return self._send(200, json.dumps(state).encode(), "application/json")
        if url.path == "/api/ping":   # is this dashboard alive, which run, and is a window showing it?
            body = {"run_dir": str(self.server.watcher.dir), "idle_seconds": round(time.time() - self.server.last_poll)}
            return self._send(200, json.dumps(body).encode(), "application/json")
        if url.path == "/file":
            root = self.server.watcher.dir.resolve()
            p = (root / q.get("p", [""])[0]).resolve()
            if root not in p.parents or not p.is_file():
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            if p.suffix in (".md", ".txt", ".log"):
                ctype = "text/plain; charset=utf-8"
            return self._send(200, p.read_bytes(), ctype)
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if not self._local() or urlparse(self.path).path != "/api/stop" or self.headers.get("X-Control-Token") != self.server.token:
            return self._send(403, b"forbidden", "text/plain")
        run_dir = self.server.watcher.dir
        pid = _j(run_dir / "run.pid")
        if not pid or not pid_alive(int(pid)):
            return self._send(409, b"This run has no stoppable process (it was started before run.pid existed, or it has ended).",
                              "text/plain; charset=utf-8")
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)  # with its yt-dlp children
        events = read_events(run_dir / "events.jsonl")
        run = next((e.get("run") for e in reversed(events) if e.get("kind") == "run_start"), None)
        with (run_dir / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": round(time.time(), 2), "run": run, "kind": "stopped", "by": "dashboard"}) + "\n")
        with (run_dir / "log.txt").open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%H:%M:%S} stopped from the dashboard - run the same command again to continue\n")
        (run_dir / "run.pid").unlink(missing_ok=True)
        self._send(200, b"stopped", "text/plain")


def launch_browser(url: str) -> str:
    for candidate in BROWSERS:
        path = os.path.expandvars(candidate)
        if os.path.isfile(path):
            subprocess.Popen([path, f"--app={url}", "--window-size=1440,960", "--window-position=80,40"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return path
    webbrowser.open(url)
    return "default browser"


def serve(run_dir: Path, niche: str, open_browser: bool = True, port: int = 0) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.watcher = Watcher(run_dir, niche)
    server.token = secrets.token_urlsafe(16)
    server.last_poll = 0.0
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"dashboard: {url}", flush=True)
    if open_browser:
        launch_browser(url)
    return server
