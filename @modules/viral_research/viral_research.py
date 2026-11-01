"""Viral research: which videos in a niche beat their own channel's normal, and what do they do differently?

    venv\\Scripts\\python @modules\\viral_research\\viral_research.py run --niche ai-music [--langs en,es] [--stage STAGE]

Stages (each cached in the run folder, so a run resumes where it stopped):
    discover  search query variants and hashtag Shorts pages per language
    baseline  each channel's normal: median views of its own recent uploads (Shorts and long-form separately)
    score     outlier multiplier = views / channel median; buckets = language x format
    shortlist top outliers + the same channels' ordinary videos (controls), confirmed with full metadata
    evidence  captions (or a local Whisper transcript), top comments, thumbnails, media
    measure   cuts, opening frames, loudness, speech - from the media itself
    analyze   patterns outliers share and controls don't; back-test on older vs newer videos
    check     view counts re-read from the watch page (independent of yt-dlp)
    pack      pack.md: everything the report is written from
Platform submodules live in platforms/ (YouTube now; TikTok, Instagram, Facebook later, same interface).
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import random
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from analysis import metrics as M          # noqa: E402
from analysis import patterns as P         # noqa: E402
from analysis.media import Media           # noqa: E402
from analysis import vision as V           # noqa: E402
from platforms import youtube as YT        # noqa: E402

SETTINGS = json.loads((HERE / "module.json").read_text(encoding="utf-8")).get("settings", {})
YT.configure(SETTINGS.get("cookies_from_browser"), SETTINGS.get("cookies_file"), SETTINGS.get("min_seconds_between_requests"))


def niche_dir(niche: str) -> Path:
    """Everything about one niche: runs/<niche>-<date>/ (raw data, one per run) and playbook/ (what to do)."""
    return HERE / "@downloads" / niche


class Incomplete(RuntimeError):
    """Too little evidence to analyse: the run ends as "incomplete" (rerun continues)."""
WORKERS = SETTINGS.get("parallel_requests", 4)
N_OUTLIERS = SETTINGS.get("outliers_per_bucket", 12)
LONG_SECONDS = SETTINGS.get("long_form_seconds_measured", 90)
MAX_AGE_DAYS = SETTINGS.get("max_age_days", 365)


# ── run folder ───────────────────────────────────────────────────────────────

class Run:
    def __init__(self, niche: str, run_dir: Path | None):
        self.niche = niche
        self.dir = run_dir or niche_dir(niche) / "runs" / f"{niche}-{date.today():%Y%m%d}"
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "meta").mkdir(exist_ok=True)
        self.now = time.time()

    _failed_lock = threading.Lock()

    def failed(self) -> dict:
        return self.load("failed.json", {})

    def remember_failure(self, key: str, reason: str) -> None:
        """Permanent failures (unavailable, private, no tab) - bot checks never get here (the gate retries them)."""
        with self._failed_lock:
            failed = self.failed()
            failed[key] = reason[:200]
            self.save("failed.json", failed)

    def path(self, name: str) -> Path:
        return self.dir / name

    def load(self, name: str, default=None):
        p = self.path(name)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

    def save(self, name: str, data) -> None:
        self.path(name).write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")

    def meta(self, video_id: str) -> dict | None:
        p = self.dir / "meta" / f"{video_id}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        if video_id in self.failed():
            return None   # unavailable / private last time: don't spend a request on it again
        try:
            m = YT.metadata(video_id)
        except YT.Blocked:
            raise
        except Exception as e:
            warn(f"    metadata {video_id}: {str(e)[:120]}")
            self.remember_failure(video_id, str(e))
            return None
        p.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
        return m

    def evidence(self, video_id: str) -> Path:
        d = self.dir / "evidence" / video_id
        d.mkdir(parents=True, exist_ok=True)
        return d


LOG_FILE: Path | None = None   # <run folder>/log.txt, set by main()


def log(msg: str) -> None:
    print(msg, flush=True)
    if LOG_FILE:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%H:%M:%S} {msg}\n")


# Titles, comments and errors are in every language; never let the Windows console encoding crash a stage.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ── structured progress for the dashboard (<run folder>/events.jsonl, append-only) ──

EVENTS_FILE: Path | None = None
RUN_ID: str | None = None
STAGE: str | None = None
_EV_LOCK = threading.Lock()
_LAST_PROGRESS: dict[str, float] = {}
ERROR_KINDS = [("rate_limit", r"429|too many requests|not a bot|sign in to confirm|bot check"),
               ("unavailable", r"unavailable|private video|removed|terminated|no listing|does not exist|members.only"),
               ("network", r"timed? ?out|connection|reset by peer|temporar|getaddrinfo")]


def emit(kind: str, **data) -> None:
    if not EVENTS_FILE:
        return
    record = json.dumps({"t": round(time.time(), 2), "run": RUN_ID, "kind": kind, "requests": YT.CALLS[0], **data}, ensure_ascii=False)
    with _EV_LOCK, EVENTS_FILE.open("a", encoding="utf-8") as f:
        f.write(record + "\n")


def progress(label: str, done: int, total: int) -> None:
    """At most one event per second per counter, plus the final one."""
    now = time.time()
    if done < total and now - _LAST_PROGRESS.get(label, 0) < 1.0:
        return
    _LAST_PROGRESS[label] = now
    emit("progress", stage=STAGE, label=label, done=done, total=total)


def classify(message: str) -> str:
    return next((kind for kind, pattern in ERROR_KINDS if re.search(pattern, message, re.I)), "other")


def warn(message: str) -> None:
    """A failure the run survives (one video, one tab): logged, and counted by kind on the dashboard."""
    log(message)
    emit("error", stage=STAGE, category=classify(message), message=message.strip()[:300])


def stage(key: str, title: str) -> None:
    global STAGE
    if STAGE:
        emit("stage_end", stage=STAGE)
    STAGE = key
    log(title)
    emit("stage_start", stage=key)


def _heartbeat() -> None:
    while True:
        time.sleep(15)
        emit("heartbeat")


def parallel(fn, items, workers=WORKERS, label: str | None = None, offset: int = 0, total: int | None = None):
    items = list(items)
    if label is None:
        with ThreadPoolExecutor(workers) as pool:
            return list(pool.map(fn, items))
    done = [0]
    lock = threading.Lock()

    def counted(x):
        try:
            return fn(x)
        finally:
            with lock:
                done[0] += 1
                progress(label, offset + done[0], total or len(items))
    with ThreadPoolExecutor(workers) as pool:
        return list(pool.map(counted, items))


def niche_config(niche: str) -> dict:
    return json.loads((HERE / "niches" / f"{niche}.json").read_text(encoding="utf-8"))


# ── 1. discover ──────────────────────────────────────────────────────────────

def discover(run: Run, langs: list[str]) -> dict:
    found = run.load("discovery.json", {})
    cfg = niche_config(run.niche)
    for lang in langs:
        if lang in found:
            continue
        pool: dict[str, dict] = {}
        jobs = [("search", q, False) for q in cfg[lang]["queries"]] + [("search<4min", q, True) for q in cfg[lang]["queries"]] \
            + [("hashtag", t, None) for t in cfg[lang]["hashtags"]]

        def fetch(job):
            kind, term, short = job
            try:
                return job, (YT.hashtag_shorts(term, 40) if kind == "hashtag" else YT.search(term, 30, under_4_min=short))
            except YT.Blocked:
                raise
            except Exception as e:
                warn(f"    {kind} '{term}': {str(e)[:120]}")
                return job, []
        for (kind, term, _), entries in parallel(fetch, jobs, label=f"Searches ({lang})"):
            for e in entries:
                row = pool.setdefault(e["id"], {"id": e["id"], "title": e.get("title"), "description": (e.get("description") or "")[:300],
                                                "views": e.get("view_count"), "duration": e.get("duration"),
                                                "channel_id": e.get("channel_id"), "channel": e.get("channel"),
                                                "from_hashtag": kind == "hashtag", "sources": []})
                row["sources"].append(f"{kind}:{term}")
        seed_examples(run, cfg[lang], pool)
        found[lang] = list(pool.values())
        run.save("discovery.json", found)
        log(f"  [{lang}] {len(pool)} videos from {len(jobs)} searches")
    return found


def seed_examples(run: Run, cfg: dict, pool: dict) -> None:
    """The user's own examples (`seed_channels`: @handles, `seed_videos`: IDs) join what the searches found: a seed
    channel's newest uploads (its baseline is recorded from the same listing), a seed video's full details."""
    channels = run.load("channels.json", {})
    for handle in cfg.get("seed_channels", []):
        try:
            got = YT.handle_tab(handle, "videos", 40)
        except YT.Blocked:
            raise
        except Exception as e:
            warn(f"    seed {handle}: {str(e)[:120]}")
            continue
        ch = got["channel_id"]
        channels.setdefault(ch, {})["videos"] = {**M.baseline_long(got["entries"], run.now), "entries": [
            {"id": e["id"], "views": e.get("view_count"), "timestamp": e.get("timestamp"), "title": e.get("title"),
             "duration": e.get("duration")} for e in got["entries"]]}
        for e in got["entries"]:
            row = pool.setdefault(e["id"], {"id": e["id"], "title": e.get("title"), "description": (e.get("description") or "")[:300],
                                            "views": e.get("view_count"), "duration": e.get("duration"), "channel_id": ch,
                                            "channel": got["channel"], "from_hashtag": False, "sources": []})
            row["sources"].append(f"seed:{handle}")
        log(f"    seed {handle}: {len(got['entries'])} uploads")
    run.save("channels.json", channels)
    for vid in cfg.get("seed_videos", []):
        m = run.meta(vid)
        if m:
            row = pool.setdefault(vid, {"id": vid, "title": m.get("title"), "description": (m.get("description") or "")[:300],
                                        "views": m.get("view_count"), "duration": m.get("duration"), "channel_id": m.get("channel_id"),
                                        "channel": m.get("channel"), "from_hashtag": False, "sources": []})
            row["sources"].append("seed:video")


def pick_channels(found: dict, cfg: dict, budget: int) -> list[str]:
    """Which channels' upload lists to read (one request each): only channels with an on-niche video (title and
    snippet match the niche and its topic) or a seed, seeds first, then by on-niche video count and best views,
    up to `budget`. Reading every channel a search touched cost 725 requests and a bot check (2026-10-25)."""
    stats: dict[str, list] = {}
    for lang, rows in found.items():
        c = cfg.get(lang, {})
        for v in rows:
            ch = v.get("channel_id")
            if not ch:
                continue
            text = f"{v.get('title') or ''} {v.get('description') or ''}"
            s = stats.setdefault(ch, [False, 0, 0])
            s[0] = s[0] or any(src.startswith("seed") for src in v.get("sources", []))
            s[1] += bool(M.relevant(text, c.get("must_match")) and M.relevant(text, c.get("topic_match")))
            s[2] = max(s[2], v.get("views") or 0)
    keep = [ch for ch, (seed, on, _) in stats.items() if seed or on]
    keep.sort(key=lambda ch: (not stats[ch][0], -stats[ch][1], -stats[ch][2]))
    return keep[:budget]


# ── 2. baseline ──────────────────────────────────────────────────────────────

def resolve(run: Run, found: dict) -> None:
    """Hashtag Shorts come without channel/title: fill them from full metadata."""
    missing = [v for rows in found.values() for v in rows if not v.get("channel_id")]
    if not missing:
        return
    log(f"  resolving {len(missing)} Shorts from hashtag pages (full metadata)")
    metas = parallel(lambda v: run.meta(v["id"]), missing, label="Hashtag Shorts (full details)")
    for v, m in zip(missing, metas):
        if m:
            v.update(channel_id=m["channel_id"], channel=m["channel"], title=m["title"], duration=m["duration"],
                     views=m["view_count"], description=(m.get("description") or "")[:300])
    run.save("discovery.json", found)


def baselines(run: Run, found: dict) -> dict:
    channels = run.load("channels.json", {})
    need = {}
    for rows in found.values():
        for v in rows:
            if not v.get("channel_id"):
                continue
            short = v.get("from_hashtag") or (v.get("duration") or 999) <= M.SHORT_MAX_SECONDS
            need.setdefault(v["channel_id"], set()).add("shorts" if short else "videos")
    picked = set(pick_channels(found, niche_config(run.niche), SETTINGS.get("max_channel_tabs", 250)))
    todo = [(ch, tab) for ch, tabs in need.items() if ch in picked for tab in tabs if tab not in channels.get(ch, {})]
    log(f"  {len(need)} channels found; {len(picked)} with an on-niche video or a seed (budget "
        f"{SETTINGS.get('max_channel_tabs', 250)}); {len(todo)} channel tabs to read")

    def fetch(job):
        ch, tab = job
        try:
            return job, YT.channel_tab(ch, tab, 40)
        except YT.Blocked:
            raise
        except Exception as e:   # e.g. the channel has no such tab: remember it as empty, don't ask again
            warn(f"    {ch}/{tab}: {str(e)[:100]}")
            return job, []
    for i in range(0, len(todo), 40):
        for (ch, tab), entries in parallel(fetch, todo[i:i + 40], label="Channel tabs read", offset=i, total=len(todo)):
            if entries is None:
                continue
            base = M.baseline_long(entries, run.now) if tab == "videos" else M.baseline_shorts(entries)
            channels.setdefault(ch, {})[tab] = {**base, "entries": [{"id": e["id"], "views": e.get("view_count"),
                                                                     "timestamp": e.get("timestamp"), "title": e.get("title"),
                                                                     "duration": e.get("duration")} for e in entries]}
        run.save("channels.json", channels)
        log(f"    {min(i + 40, len(todo))}/{len(todo)} tabs")
    return channels


# ── 3. score ─────────────────────────────────────────────────────────────────

def video_format(v: dict, meta: dict | None) -> str:
    if meta:
        s = M.is_short(meta)
        if s is not None:
            return "shorts" if s else "long"
    return "shorts" if v.get("from_hashtag") or (v.get("duration") or 999) <= M.SHORT_MAX_SECONDS else "long"


def language_fits(lang: str, meta: dict | None, v: dict) -> bool:
    """The declared audio language decides when YouTube has it; otherwise common words / script in the text."""
    spoken = M.spoken_language(meta)
    if spoken:
        return spoken == lang
    guess = M.language_guess(f"{v.get('title') or ''} {v.get('description') or ''}")
    return guess in (None, lang)


def niche_text(v: dict, meta: dict | None) -> str:
    m = meta or {}
    return " ".join([v.get("title") or m.get("title") or "", v.get("description") or (m.get("description") or "")[:500],
                     " ".join(m.get("tags") or []), v.get("channel") or m.get("channel") or ""])


def score(run: Run, found: dict, channels: dict) -> list[dict]:
    cfg = niche_config(run.niche)
    rows, dropped = [], {"language": 0, "off_niche": 0, "no_baseline": 0, "no_views": 0}
    for lang, vids in found.items():
        for v in vids:
            meta = run.load(f"meta/{v['id']}.json")
            fmt = video_format(v, meta)
            tab = channels.get(v.get("channel_id") or "", {}).get("shorts" if fmt == "shorts" else "videos")
            if not language_fits(lang, meta, v):
                dropped["language"] += 1
                continue
            if not M.relevant(niche_text(v, meta), cfg[lang].get("must_match")):
                dropped["off_niche"] += 1
                continue
            if not tab or not tab.get("median"):
                dropped["no_baseline"] += 1
                continue
            ts = (meta or {}).get("timestamp") or next((e["timestamp"] for e in tab["entries"] if e["id"] == v["id"] and e.get("timestamp")), None)
            views = (meta or {}).get("view_count") or v.get("views")
            mult = M.multiplier(views, tab["median"])
            if mult is None:
                dropped["no_views"] += 1
                continue
            rows.append({"id": v["id"], "bucket": f"{lang}-{fmt}", "lang": lang, "format": fmt, "title": v.get("title"),
                         "channel_id": v["channel_id"], "channel": v.get("channel"), "views": views, "channel_median": tab["median"],
                         "baseline_n": tab["n"], "multiplier": mult, "tier": M.tier(mult), "timestamp": ts,
                         "age_days": round(M.age_days(ts, run.now), 1) if ts else None,
                         "views_per_day": M.views_per_day(views, ts, run.now), "duration": (meta or {}).get("duration") or v.get("duration"),
                         "sources": v["sources"], "description": v.get("description")})
    rows.sort(key=lambda r: -(r["multiplier"] or 0))
    run.save("scored.json", {"rows": rows, "dropped": dropped})
    for b in sorted({r["bucket"] for r in rows}):
        br = [r for r in rows if r["bucket"] == b]
        log(f"  [{b}] {len(br)} scored, {sum(r['tier'] == 'outlier' for r in br)} outliers, "
            f"{sum(r['tier'] == 'borderline' for r in br)} borderline")
    log(f"  dropped: {dropped}")
    return rows


def mine_shorts(run: Run, rows: list[dict], channels: dict) -> list[dict]:
    """Shorts from niche channels' own Shorts tabs (the reference "watchlist" method): search results only give
    a length, so short horizontal videos pass for Shorts; a channel's Shorts tab holds real Shorts only.
    Niche channels = channels that already produced an on-niche video in that language."""
    niche, count = {}, {}
    for r in rows:
        niche.setdefault(r["lang"], set()).add(r["channel_id"])
        count[r["channel_id"]] = count.get(r["channel_id"], 0) + 1
    todo = sorted({ch for chans in niche.values() for ch in chans if "shorts" not in channels.get(ch, {})},
                  key=lambda ch: -count[ch])[:SETTINGS.get("max_shorts_tabs", 120)]   # the busiest niche channels first
    log(f"  reading the Shorts tab of {len(todo)} more niche channels (budget {SETTINGS.get('max_shorts_tabs', 120)})")

    def fetch(ch):
        try:
            return ch, YT.channel_tab(ch, "shorts", 40)
        except YT.Blocked:
            raise
        except Exception:
            return ch, []
    for ch, entries in parallel(fetch, todo, label="Niche channels' Shorts tabs"):
        channels.setdefault(ch, {})["shorts"] = {**M.baseline_shorts(entries), "entries": [
            {"id": e["id"], "views": e.get("view_count"), "timestamp": None, "title": e.get("title"), "duration": e.get("duration")}
            for e in entries]}
    run.save("channels.json", channels)
    have = {(r["bucket"], r["id"]) for r in rows}
    added = 0
    for lang, chans in niche.items():
        for ch in chans:
            tab = channels.get(ch, {}).get("shorts") or {}
            if not tab.get("median"):
                continue
            name = next((r["channel"] for r in rows if r["channel_id"] == ch), None)
            for e in tab["entries"]:
                mult = M.multiplier(e.get("views"), tab["median"])
                if (f"{lang}-shorts", e["id"]) in have or (mult or 0) < M.BORDERLINE:
                    continue
                rows.append({"id": e["id"], "bucket": f"{lang}-shorts", "lang": lang, "format": "shorts", "title": e.get("title"),
                             "channel_id": ch, "channel": name, "views": e["views"], "channel_median": tab["median"],
                             "baseline_n": tab["n"], "multiplier": mult, "tier": M.tier(mult), "timestamp": None, "age_days": None,
                             "views_per_day": None, "duration": e.get("duration"), "sources": ["channel shorts tab"], "description": ""})
                have.add((f"{lang}-shorts", e["id"]))
                added += 1
    rows.sort(key=lambda r: -(r["multiplier"] or 0))
    scored = run.load("scored.json", {})
    run.save("scored.json", {**scored, "rows": rows})
    log(f"  added {added} Shorts from niche channels' own Shorts tabs (>= {M.BORDERLINE}x their tab median)")
    return rows


# ── 4. shortlist ─────────────────────────────────────────────────────────────

def shortlist_reason(m: dict | None, grp: str, lang: str, fmt: str, cfg: dict, now: float) -> str | None:
    """Why a shortlisted video can't be used (None = it can). Outliers and controls pass the same niche check."""
    if not m:
        return "metadata unavailable"
    if video_format({}, m) != fmt:
        return f"not {fmt} (aspect/length)"
    if m.get("live_status") not in (None, "not_live", "was_live"):
        return "live stream"
    if not language_fits(lang, m, {"title": m.get("title"), "description": (m.get("description") or "")[:300]}):
        return f"not {lang} (declared audio language {m.get('language')})"
    if not M.on_topic(m, cfg[lang]):   # controls too: an off-niche control fakes niche-word "patterns"
        return f"off-niche (needs the niche signal and the topic: category {m.get('categories')} / topic words)"
    if grp == "outliers":
        age = M.age_days(m.get("timestamp"), now)
        if age is not None and age > MAX_AGE_DAYS:   # older videos had years to collect views: not comparable
            return f"older than {MAX_AGE_DAYS} days ({age:.0f})"
        if M.likely_paid_views(m.get("view_count"), m.get("like_count")):
            return f"likely paid views ({m.get('like_count')} likes on {m.get('view_count'):,} views)"
        if M.is_sponsored(f"{m.get('title')} {m.get('description')}"):
            return "sponsored"
    return None


def shortlist_entry(r: dict, m: dict, now: float) -> dict:
    mult = M.multiplier(m["view_count"], r["channel_median"])
    return {"id": r["id"], "channel_id": m["channel_id"], "channel": m["channel"], "title": m["title"],
            "views": m["view_count"], "channel_median": r["channel_median"], "multiplier": mult,
            "tier": M.tier(mult), "timestamp": m.get("timestamp"),
            "age_days": round(M.age_days(m.get("timestamp"), now), 1) if m.get("timestamp") else None,
            "views_per_day": M.views_per_day(m["view_count"], m.get("timestamp"), now),
            "duration": m.get("duration"), "subscribers": m.get("channel_follower_count"),
            "likes": m.get("like_count"), "comments": m.get("comment_count"),
            "url": m.get("webpage_url"), "sponsored": M.is_sponsored(f"{m.get('title')} {m.get('description')}")}


def shortlist(run: Run, rows: list[dict], channels: dict) -> dict:
    """Per ranking: up to N_OUTLIERS outliers that pass every check, at most 3 per channel, and ordinary videos of
    the same channels as controls. Search results carry no dates, so candidates are confirmed with full details in
    rounds until the ranking is full (old evergreen videos fill the top of the list in some niches), bounded by
    `max_shortlist_candidates` per ranking."""
    cfg = niche_config(run.niche)
    budget = SETTINGS.get("max_shortlist_candidates", 60)
    blocked = False

    def details(items):
        nonlocal blocked
        ids = [r["id"] for r in items if not run.path(f"meta/{r['id']}.json").exists()]
        if blocked or not ids:
            return
        try:
            parallel(run.meta, ids, label="Shortlisted videos (full details)")
        except YT.Blocked as e:   # keep going with what is cached; the rest is excluded as "metadata unavailable"
            blocked = True
            warn(f"  {e} - shortlisting from the videos whose details are cached")

    final = {}
    for b in sorted({r["bucket"] for r in rows}):
        lang, fmt = b.split("-")
        cand = sorted((r for r in rows if r["bucket"] == b and r["multiplier"] and r["multiplier"] >= M.BORDERLINE
                       and (r["age_days"] is None or r["age_days"] <= MAX_AGE_DAYS)), key=lambda r: -r["multiplier"])[:budget]
        keep = {"outliers": [], "controls": [], "excluded": []}
        per_channel, i = {}, 0
        while len(keep["outliers"]) < N_OUTLIERS and i < len(cand):
            batch = []
            while len(batch) < N_OUTLIERS - len(keep["outliers"]) + 4 and i < len(cand):
                if per_channel.get(cand[i]["channel_id"], 0) < 3:
                    batch.append(cand[i])
                i += 1
            details(batch)
            for r in batch:
                if per_channel.get(r["channel_id"], 0) >= 3 or len(keep["outliers"]) >= N_OUTLIERS:
                    continue
                m = run.load(f"meta/{r['id']}.json")
                reason = shortlist_reason(m, "outliers", lang, fmt, cfg, run.now)
                if reason:
                    keep["excluded"].append({"id": r["id"], "title": (m or {}).get("title") or r.get("title"), "reason": reason})
                    continue
                e = shortlist_entry(r, m, run.now)
                if (e["multiplier"] or 0) >= M.BORDERLINE:
                    keep["outliers"].append(e)
                    per_channel[r["channel_id"]] = per_channel.get(r["channel_id"], 0) + 1
        # Controls: ordinary videos (0.5-1.5x) of the kept outliers' channels, two per channel
        tabname = "shorts" if fmt == "shorts" else "videos"
        outlier_ids = {r["id"] for r in keep["outliers"]}
        pool = []
        for ch in dict.fromkeys(r["channel_id"] for r in keep["outliers"]):
            tab = (channels.get(ch) or {}).get(tabname) or {}
            if not tab.get("median"):
                continue
            ordinary = [e for e in tab["entries"] if e.get("views") and e["id"] not in outlier_ids
                        and M.CONTROL_RANGE[0] <= e["views"] / tab["median"] <= M.CONTROL_RANGE[1]]
            random.Random(ch).shuffle(ordinary)
            pool += [{"id": e["id"], "channel_id": ch, "channel_median": tab["median"]} for e in ordinary[:2]]
        details(pool)
        for r in pool:
            m = run.load(f"meta/{r['id']}.json")
            if shortlist_reason(m, "controls", lang, fmt, cfg, run.now):
                continue
            e = shortlist_entry(r, m, run.now)
            if M.CONTROL_RANGE[0] <= (e["multiplier"] or 0) <= 2.0:
                keep["controls"].append(e)
        keep["outliers"].sort(key=lambda r: -r["multiplier"])
        keep["controls"] = keep["controls"][:max(len(keep["outliers"]), 6)]
        final[b] = keep
        log(f"  [{b}] {len(keep['outliers'])} outliers, {len(keep['controls'])} controls, {len(keep['excluded'])} excluded "
            f"({i} of {len(cand)} candidates checked)")
    run.save("shortlist.json", final)
    return final


# ── 5-6. evidence and measurements ───────────────────────────────────────────

def whisper(media: Path, out: Path, lang: str, seconds: int = 30) -> dict | None:
    if out.exists():
        return json.loads(out.read_text(encoding="utf-8"))
    py = SETTINGS.get("whisper_python")
    if not py or not Path(py).exists():
        return None
    clip = out.with_name("opening.wav")
    ffmpeg = Media(SETTINGS["ffmpeg_dir"]).ffmpeg
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-t", str(seconds), "-i", str(media), "-vn", "-ac", "1", "-ar", "16000", str(clip)],
                   capture_output=True, timeout=300)
    if not clip.exists():
        return None
    p = subprocess.run([py, str(HERE / "analysis" / "whisper_worker.py"), str(clip), SETTINGS.get("whisper_model", "small"), lang],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    clip.unlink(missing_ok=True)
    if p.returncode != 0 or not p.stdout.strip():
        warn(f"    whisper failed: {p.stderr.strip()[-160:]}")
        return None
    doc = json.loads(p.stdout.strip().splitlines()[-1])
    out.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return doc


def evidence(run: Run, final: dict) -> None:
    media = Media(SETTINGS["ffmpeg_dir"])
    # Request budget: media for the top outliers and as many controls per ranking (the rest stay ranked, unmeasured).
    n = SETTINGS.get("measure_per_bucket", 8)
    every = [r for p in final.values() for grp in ("outliers", "controls") for r in p[grp]]
    missing = [r for r in every if not (run.dir / "evidence" / r["id"] / "thumbnail.jpg").exists()]
    if missing:   # image servers, not YouTube's pages: no bot check, so every shortlisted video gets one
        log(f"  thumbnails for {len(missing)} of {len(every)} shortlisted videos")
        parallel(lambda r: YT.thumbnail(r["id"], run.evidence(r["id"]) / "thumbnail.jpg"), missing, label="Thumbnails")
    jobs = [(b, grp, r) for b, p in final.items() for grp in ("outliers", "controls") for r in p[grp][:n]]
    log(f"  evidence for {len(jobs)} videos (media, captions, comments for outliers)")

    def collect(job):
        b, grp, r = job
        lang, fmt = b.split("-")
        d = run.evidence(r["id"])
        meta = run.load(f"meta/{r['id']}.json") or {}
        spoken = M.spoken_language(meta) or lang
        status = {}
        try:
            if not (d / "measures.json").exists():   # one yt-dlp call: media + captions in the spoken language
                path = YT.download(r["id"], d, SETTINGS["ffmpeg_dir"], None if fmt == "shorts" else LONG_SECONDS,
                                   sub_langs=f"{spoken}.*", duration=meta.get("duration"))
                (d / "measures.json").write_text(json.dumps(media.measure(path, d), ensure_ascii=False), encoding="utf-8")
            caps = YT.pick_captions(d, meta.get("caption_langs") or [])
            status["captions"] = bool(caps)
            if grp == "outliers" and not (d / "comments.json").exists():
                (d / "comments.json").write_text(json.dumps(YT.comments(r["id"], 40), ensure_ascii=False), encoding="utf-8")
            if not caps:
                path = next(iter(sorted(d.glob("media.*"))), None)
                if path:
                    whisper(path, d / "transcript.json", spoken)
        except YT.Blocked:
            raise
        except Exception as e:
            status["error"] = str(e)[:200]
            warn(f"    {r['id']}: {status['error']}")
        return status
    results = parallel(collect, jobs, workers=2, label="Videos downloaded and measured")
    measured = sum(1 for _, _, r in jobs if (run.dir / "evidence" / r["id"] / "measures.json").exists())
    log(f"  done: {sum(1 for s in results if 'error' not in s)}/{len(jobs)} without errors; {measured}/{len(jobs)} measured")
    if measured < len(jobs) / 2:   # patterns from half the evidence would be noise, not findings
        raise Incomplete(f"only {measured} of {len(jobs)} videos could be measured - run the same command again later")


def speech_for(run: Run, video_id: str, duration: float | None) -> dict | None:
    d = run.dir / "evidence" / video_id
    cap = YT.pick_captions(d, (run.load(f"meta/{video_id}.json") or {}).get("caption_langs") or [])
    if cap:
        words = M.parse_json3(json.loads(cap.read_text(encoding="utf-8")))
        return {**M.speech_stats(words, duration), "source": "captions"}
    t = d / "transcript.json"
    if t.exists():
        doc = json.loads(t.read_text(encoding="utf-8"))
        return {**M.speech_stats(doc["words"], min(duration or 30, 30)), "source": "whisper (first 30 s)"}
    return None


# ── 6. vision: the user's local vision model reads thumbnails and opening frames ──

def vision(run: Run, final: dict) -> None:
    """Local labels (text, face, style, subject) for every shortlisted thumbnail and measured opening frame.
    No YouTube requests: thumbnails come from the image servers, frames from the downloaded media."""
    cfg = SETTINGS.get("vision") or {}
    if not cfg.get("enabled", True):
        log("  local vision disabled in module.json")
        return
    jobs = []
    for p in final.values():
        for grp in ("outliers", "controls"):
            for r in p[grp]:
                d = run.dir / "evidence" / r["id"]
                for kind, image in (("thumbnail", d / "thumbnail.jpg"), ("opening", d / "frames" / "frame_001.0s.jpg")):
                    old = run.load(f"evidence/{r['id']}/vision_{kind}.json")
                    if image.exists() and len(V.reusable(old)) < len(V.QUESTIONS):   # new, or a question was reworded
                        jobs.append((d, kind, image, old))
    jobs.sort(key=lambda job: job[1] != "thumbnail")   # every thumbnail before any opening frame
    if not jobs:
        log("  every image already labelled")
        return
    log(f"  {len(jobs)} images for the local vision model (~30 s each on CPU)")
    try:
        model = V.LocalVision(cfg["server"], cfg["model"], cfg["mmproj"], Media(SETTINGS["ffmpeg_dir"]).ffmpeg,
                              port=cfg.get("port", 8098), threads=cfg.get("threads", 4), log=run.dir / "vision-server.log")
    except Exception as e:
        warn(f"    local vision model unavailable, skipping visual labels: {e}")
        return
    # Time-boxed: on a shared CPU (another model server running) an image can take minutes; the run goes on and
    # the next run labels what is left. Thumbnails come first in `jobs` (they matter most).
    deadline = time.time() + cfg.get("max_minutes", 20) * 60
    try:
        for i, (d, kind, image, old) in enumerate(jobs, 1):
            if time.time() > deadline:
                warn(f"    vision: time limit reached, {len(jobs) - i + 1} images left for the next run")
                break
            if model.proc.poll() is not None:
                warn(f"    vision: the model server stopped, {len(jobs) - i + 1} images left for the next run")
                break
            try:
                (d / f"vision_{kind}.json").write_text(json.dumps(model.look(image, old), ensure_ascii=False), encoding="utf-8")
            except Exception as e:
                warn(f"    vision {d.name}/{kind}: {str(e)[:120]}")
            progress("Images read by the local vision model", i, len(jobs))
    finally:
        model.close()


# ── 7. analyze ───────────────────────────────────────────────────────────────

def full_features(run: Run, r: dict) -> dict:
    m = run.load(f"meta/{r['id']}.json") or {}
    d = run.dir / "evidence" / r["id"]
    meas = json.loads((d / "measures.json").read_text(encoding="utf-8")) if (d / "measures.json").exists() else {}
    speech = speech_for(run, r["id"], m.get("duration"))
    desc = m.get("description") or ""
    pt = M.post_time(m.get("timestamp"))
    f = {**M.title_features(m.get("title") or ""), "duration_s": m.get("duration"), "tags_count": len(m.get("tags") or []),
         "has_chapters": bool(m.get("chapters")), "desc_chars": len(desc), "desc_links": desc.count("http"),
         "desc_hashtags": desc.count("#"), "post_hour_utc": pt["post_hour_utc"],
         "weekend_post": pt["post_weekday"] in ("Sat", "Sun") if pt["post_weekday"] else None}
    if meas:
        f.update({"cuts_per_min": meas.get("cuts_per_min"), "first_cut_at": meas.get("first_cut_at") if meas.get("first_cut_at") is not None else meas.get("measured_seconds"),
                  "starts_loud": meas.get("starts_loud"), "lufs": meas.get("lufs"), "silence_share": meas.get("silence_share")})
    if speech:
        f.update({"music_only": speech["music_only"], "speaks_in_first_3s": speech["words_first_3s"] > 0,
                  "words_first_15s": speech["words_first_15s"], "wpm": speech["wpm"]})
    load = lambda name: json.loads((d / name).read_text(encoding="utf-8")) if (d / name).exists() else None
    f.update(V.vision_features(load("vision_thumbnail.json"), load("vision_opening.json")))
    return {k: v for k, v in f.items() if v is not None}


def packaging_features(r: dict) -> dict:
    f = {**M.title_features(r.get("title") or ""), "duration_s": r.get("duration")}
    if r.get("timestamp"):
        pt = M.post_time(r["timestamp"])
        f.update(post_hour_utc=pt["post_hour_utc"], weekend_post=pt["post_weekday"] in ("Sat", "Sun"))
    return {k: v for k, v in f.items() if v is not None}


def analyze(run: Run, rows: list[dict], final: dict) -> dict:
    out = {}
    for b, p in final.items():
        o = [{"id": r["id"], "channel_id": r["channel_id"], "features": full_features(run, r)} for r in p["outliers"]]
        c = [{"id": r["id"], "channel_id": r["channel_id"], "features": full_features(run, r)} for r in p["controls"]]
        scored = [{**r, "features": packaging_features(r)} for r in rows if r["bucket"] == b]
        out[b] = {"comparison": P.compare(o, c), "backtest": P.backtest(scored), "features": {x["id"]: x["features"] for x in o + c}}
        n = sum(1 for x in out[b]["comparison"] if x["pattern"])
        bt = out[b]["backtest"]
        log(f"  [{b}] {n} patterns; back-test: {('holds' if bt.get('holds') else 'does not hold') if bt.get('ok') else bt.get('note')}")
    run.save("analysis.json", out)
    return out


# ── 8. independent check ─────────────────────────────────────────────────────

def check(run: Run, final: dict, per_bucket: int = 3) -> list[dict]:
    sample = [r for p in final.values() for r in p["outliers"][:per_bucket]]

    def one(r):
        try:
            page = YT.watch_page_views(r["id"])
        except YT.Blocked:
            raise
        except Exception as e:
            return {"id": r["id"], "error": str(e)[:100]}
        m = run.load(f"meta/{r['id']}.json") or {}
        drift = round((page - m["view_count"]) / m["view_count"], 3) if page and m.get("view_count") else None
        return {"id": r["id"], "yt_dlp_views": m.get("view_count"), "watch_page_views": page, "drift": drift}
    res = parallel(one, sample, label="View counts re-checked")
    run.save("spot_check.json", {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "results": res})
    ok = [x for x in res if x.get("drift") is not None and -0.02 <= x["drift"] <= 0.15]
    log(f"  {len(ok)}/{len(res)} view counts confirmed by the watch page (views only grow: -2%..+15% allowed)")
    return res


# ── dashboard ────────────────────────────────────────────────────────────────

def ensure_dashboard(run: Run) -> None:
    """Every run shows its dashboard: one dashboard process per run folder, separate from the run so it still
    shows how the run ended. A window is opened again when none is showing it."""
    import dashboard
    info = run.load("dashboard.json")
    if info and dashboard.pid_alive(info.get("pid", 0)):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{info['port']}/api/ping", timeout=3) as resp:
                ping = json.loads(resp.read())
            if Path(ping["run_dir"]).resolve() == run.dir.resolve():
                if ping["idle_seconds"] > 10:   # no window is polling it
                    dashboard.launch_browser(f"http://127.0.0.1:{info['port']}/")
                log(f"dashboard: http://127.0.0.1:{info['port']}/")
                return
        except Exception:
            pass
    # Started through `cmd /c start`, which exits at once: the dashboard's parent is then a dead process, not this
    # run, so stopping the run (taskkill /T, also the dashboard's Stop button) does not take the dashboard with it.
    cmd = ["cmd", "/c", "start", "", "/b", sys.executable, str(HERE / "viral_research.py"), "dashboard", "--niche", run.niche,
           "--run-dir", str(run.dir)]
    for breakaway in (0x01000000, 0):   # CREATE_BREAKAWAY_FROM_JOB (outlives the console's job), then without
        try:
            subprocess.Popen(cmd, creationflags=0x00000008 | 0x00000200 | breakaway,   # DETACHED_PROCESS | NEW_PROCESS_GROUP
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
            break
        except OSError:
            continue
    log("dashboard: opening in its own window")


def latest_run(niche: str | None) -> Path | None:
    """The newest run folder: @downloads/<niche>/runs/<niche>-<date>/ (or the older @downloads/runs/<niche>-<date>/)."""
    pattern = f"{niche}-*" if niche else "*"
    runs = sorted([*(HERE / "@downloads").glob(f"{niche or '*'}/runs/{pattern}"), *(HERE / "@downloads" / "runs").glob(pattern)],
                  key=lambda p: p.stat().st_mtime)
    return runs[-1] if runs else None


def playbook(niche: str, run_dir: Path | None) -> int:
    """<niche folder>/playbook/index.html from a run + niches/<niche>.playbook.json (formulas and ideas Claude writes)."""
    from analysis import playbook_page as PP
    run_dir = run_dir or latest_run(niche)
    content = HERE / "niches" / f"{niche}.playbook.json"
    book = HERE / "niches" / f"{niche}.concepts.json"
    if not run_dir:
        print("No run folder yet - start one with: run --niche <niche>")
        return 2
    if not content.exists():
        print(f"No playbook content: {content} - write it from pack.md and concepts.json (see INSTRUCTIONS.md, part 9)")
        return 2
    codebook = json.loads(book.read_text(encoding="utf-8"))["concepts"] if book.exists() else []
    cfg = niche_config(niche)
    lang = "en" if "en" in cfg else next(k for k, v in cfg.items() if isinstance(v, dict))
    name_words = "|".join(x for x in (cfg[lang].get("must_match"), cfg[lang].get("topic_match")) if x)
    print(PP.build(niche_dir(niche), run_dir, json.loads(content.read_text(encoding="utf-8")), codebook, name_words))
    return 0


def show_dashboard(niche: str | None, run_dir: Path | None, port: int = 0, open_browser: bool = True) -> int:
    """Serves the dashboard until no window has looked at it for 15 minutes and no run is going on."""
    import dashboard
    run_dir = run_dir or latest_run(niche)
    if not run_dir:
        print("No run folder yet - start one with: run --niche <niche>")
        return 2
    niche = niche or run_dir.name.rsplit("-", 1)[0]
    server = dashboard.serve(run_dir, niche, open_browser=open_browser, port=port)
    me = {"pid": os.getpid(), "port": server.server_address[1]}
    (run_dir / "dashboard.json").write_text(json.dumps(me), encoding="utf-8")
    started = time.time()
    print("Serving the dashboard - press Ctrl+C to close it.")
    try:
        while True:
            time.sleep(30)
            try:
                running = dashboard.pid_alive(int((run_dir / "run.pid").read_text().strip()))
            except (OSError, ValueError):
                running = False
            if not running and time.time() - max(server.last_poll, started) > dashboard.IDLE_EXIT_SECONDS:
                return 0
    except KeyboardInterrupt:
        return 0
    finally:
        try:
            if json.loads((run_dir / "dashboard.json").read_text(encoding="utf-8")) == me:
                (run_dir / "dashboard.json").unlink()
        except (OSError, ValueError):
            pass


# ── concepts: the ideas' evidence (run after pack.md, with a codebook Claude writes) ──

def concepts(run: Run, with_autocomplete: bool = False) -> int:
    """niches/<niche>.concepts.json -> <run>/concepts.json: per concept, proof against the channels' normal (outlier
    rate, lift, channels), crowding (recent supply), a holdout on the newest videos, and optionally what people
    type into YouTube search. The codebook is kept with the niche so a monthly refresh counts the same way."""
    from analysis import demand as Dm
    book_path = HERE / "niches" / f"{run.niche}.concepts.json"
    if not book_path.exists():
        print(f"No codebook: {book_path} - write it after reading pack.md (see INSTRUCTIONS.md, part 8)")
        return 2
    from analysis.pack import on_niche
    book = json.loads(book_path.read_text(encoding="utf-8"))["concepts"]
    cfg = niche_config(run.niche)
    rows = [r for r in (run.load("scored.json") or {}).get("rows") or [] if on_niche(r, cfg.get(r["bucket"].split("-")[0], {}))]
    print(f"{len(rows)} on-niche scored videos")
    result = {"built": datetime.now(timezone.utc).isoformat(timespec="seconds"), "codebook": str(book_path),
              "concepts": Dm.concept_counts(rows, book)}
    if with_autocomplete:
        result["autocomplete"] = Dm.demand_for({c["name"]: c.get("seeds", [])[:3] for c in book})
    run.save("concepts.json", result)
    print(f"{'concept':38} hits  out  rate  lift  ch  med-x  recent  holdout old->new")
    for x in result["concepts"]:
        h = x["holdout"]
        print(f"{x['name'][:38]:38} {x['hits']:4} {x['outliers']:4} {x['outlier_rate'] or 0:5.2f} {x['lift'] or 0:5.2f} "
              f"{x['channels_with_outliers']:3} {x['median_multiplier'] or 0:6} {x['recent_supply']:6}  "
              f"{h['older_rate']}({h['older_n']}) -> {h['newest_rate']}({h['newest_n']}) {'PROVEN' if x['proven'] else 'bet'}")
    print(f"saved {run.dir / 'concepts.json'}")
    return 0


# ── CLI ──────────────────────────────────────────────────────────────────────

STAGES = ["discover", "baseline", "score", "shortlist", "evidence", "vision", "analyze", "check", "pack"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--niche", required=True)
    r.add_argument("--langs", default="en,es")
    r.add_argument("--stage", choices=STAGES + ["all"], default="all", help="run from the start up to this stage")
    r.add_argument("--run-dir", type=Path)
    r.add_argument("--no-dashboard", action="store_true", help="don't open the live status page (it opens by default)")
    r.add_argument("--dashboard", action="store_true", help=argparse.SUPPRESS)   # the default now; kept for old commands
    d = sub.add_parser("dashboard", help="show the live status of a run (default: the newest run folder)")
    d.add_argument("--niche")
    d.add_argument("--run-dir", type=Path)
    d.add_argument("--port", type=int, default=0, help="fixed port (an open window reconnects after a restart)")
    d.add_argument("--no-browser", action="store_true")
    c = sub.add_parser("concepts", help="count the concepts of niches/<niche>.concepts.json in a run (after pack.md)")
    c.add_argument("--niche", required=True)
    c.add_argument("--run-dir", type=Path)
    c.add_argument("--autocomplete", action="store_true", help="also ask YouTube search autocomplete (<= 30 light requests)")
    pb = sub.add_parser("playbook", help="build <niche folder>/playbook/index.html (formulas, ideas, evidence) from a run")
    pb.add_argument("--niche", required=True)
    pb.add_argument("--run-dir", type=Path)
    pl = sub.add_parser("prelaunch", help="pre-launch decision research for a new channel (see prelaunch.py)")
    pl.add_argument("--niche", required=True)
    pl.add_argument("--run-dir", type=Path)
    pl.add_argument("--stage", help="run only this stage: snapshot, assets, audio, sample, previews (renders), build")
    a = ap.parse_args(argv)
    if a.cmd == "prelaunch":
        import prelaunch
        return prelaunch.main(a.niche, a.run_dir, a.stage)
    if a.cmd == "dashboard":
        return show_dashboard(a.niche, a.run_dir, a.port, not a.no_browser)
    if a.cmd == "concepts":
        return concepts(Run(a.niche, a.run_dir or latest_run(a.niche)), a.autocomplete)
    if a.cmd == "playbook":
        return playbook(a.niche, a.run_dir)
    problem = YT.available()
    if problem:
        print(f"ERROR: {problem}")
        return 2
    run = Run(a.niche, a.run_dir)
    global LOG_FILE
    LOG_FILE = run.dir / "log.txt"
    (run.dir / "run.pid").write_text(str(os.getpid()))
    atexit.register(lambda: (run.dir / "run.pid").unlink(missing_ok=True))
    langs =[x.strip() for x in a.langs.split(",") if x.strip()]
    upto = STAGES.index(a.stage) if a.stage != "all" else len(STAGES) - 1
    global EVENTS_FILE, RUN_ID
    EVENTS_FILE, RUN_ID = run.dir / "events.jsonl", datetime.now().strftime("%Y%m%d-%H%M%S")
    log(f"run folder: {run.dir}")
    emit("run_start", pid=os.getpid(), niche=run.niche, langs=langs, upto=STAGES[upto])
    if not a.no_dashboard:
        ensure_dashboard(run)
    threading.Thread(target=_heartbeat, daemon=True).start()
    YT.GATE.on_block = lambda until, reason: (
        warn(f"    YouTube bot check - pausing until {datetime.fromtimestamp(until):%H:%M:%S}, then retrying: {reason[:160]}"),
        emit("blocked", until=round(until, 1), reason=reason[:200]))
    YT.GATE.persist(run.dir / "gate.json")
    if YT.GATE.blocked_until > time.time():
        YT.GATE.on_block(YT.GATE.blocked_until, "still inside the previous run's pause")
    try:
        _pipeline(run, langs, upto)
    except (YT.Blocked, Incomplete) as e:
        status = "blocked" if isinstance(e, YT.Blocked) else "incomplete"
        log(f"{status.upper()}: {e}")
        emit("run_end", status=status, error=str(e)[:300])
        return 3
    except BaseException as e:   # KeyboardInterrupt included: the dashboard must not keep showing "running"
        emit("run_end", status="failed", error=f"{type(e).__name__}: {e}"[:300])
        raise
    emit("stage_end", stage=STAGE)
    emit("run_end", status="finished", last_stage=STAGE)
    return 0


def _pipeline(run: Run, langs: list[str], upto: int) -> None:
    stage("discover", "1. discover"); found = discover(run, langs)
    if upto < 1: return
    stage("baseline", "2. baseline"); resolve(run, found); channels = baselines(run, found)
    if upto < 2: return
    stage("score", "3. score"); rows = score(run, found, channels); rows = mine_shorts(run, rows, channels)
    if upto < 3: return
    stage("shortlist", "4. shortlist"); final = shortlist(run, rows, channels)
    if upto < 4: return
    partial = None
    stage("evidence", "5. evidence + measure")
    try:
        evidence(run, final)
    except (Incomplete, YT.Blocked) as e:   # titles, metadata and thumbnails still compare; the pack says what is missing
        partial = e
        warn(f"  PARTIAL: {e}; continuing with titles, metadata and thumbnails only")
    if upto < 5: return
    stage("vision", "6. vision"); vision(run, final)
    if upto < 6: return
    stage("analyze", "7. analyze"); analyze(run, rows, final)
    if upto < 7: return
    if not partial:   # the check reads YouTube's watch pages: skipped while YouTube refuses requests
        stage("check", "8. check"); check(run, final)
    if upto < 8: return
    from analysis.pack import write_pack
    stage("pack", "9. pack"); log(f"  {write_pack(run, str(partial) if partial else None)}")
    if partial:
        raise partial   # the run still ends as incomplete / blocked


if __name__ == "__main__":
    sys.exit(main())
