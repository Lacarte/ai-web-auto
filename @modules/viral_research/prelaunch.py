"""Pre-launch research: should a new music channel launch, for whom, with what - and is it cleared to publish?

    venv\\Scripts\\python @modules\\viral_research\\viral_research.py prelaunch --niche black-samurai-prelaunch [--stage STAGE] [--run-dir DIR]

Stages (each resumable; raw evidence is written once and never overwritten):
    snapshot  copy the source runs' raw files into raw/ with their SHA-256
    assets    inventory the owner's assets (hash, size, probe, quotes) - nothing in the owner's project is changed
    audio     audio audit of the owner's tracks (analysis/audio_audit.py)
    sample    stratified competitor sample; reads the upload lists of new channels seen in this month's searches
    previews  three 40-s visual variants of the owner's video for an offline viewer test (renders a copied excerpt only)
    build     normalized/ tables, analysis.json, and the ten outputs from the templates in niches/<niche>/
Raw (raw/), normalized (normalized/) and interpretation (the templates) stay separate; `build` alone is offline.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import random
import re
import shutil
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from analysis import prelaunch as PL      # noqa: E402

STAGES = ["snapshot", "assets", "audio", "sample", "previews", "build"]
OUTPUTS = ["decision_report.md", "opportunity_comparison.md", "music_quality_report.md", "visual_experiment.md",
           "economics.md", "pilot_plan.md"]


def log(msg: str) -> None:
    print(msg, flush=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_once(path: Path, data) -> bool:
    """Raw evidence is immutable: an existing file is never replaced. Returns True when written."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return True


def load(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


class Run:
    def __init__(self, niche: str, run_dir: Path | None):
        self.niche = niche
        self.cfg = json.loads((HERE / "niches" / f"{niche}.json").read_text(encoding="utf-8"))
        self.knowledge = HERE / "niches" / niche
        base = HERE / "@downloads" / niche / "runs"
        if run_dir is None:
            existing = sorted(base.glob("*")) if base.exists() else []
            run_dir = existing[-1] if existing else base / datetime.now().strftime("%Y%m%d-%H%M%S")
        self.dir = Path(run_dir)
        self.raw = self.dir / "raw"
        self.norm = self.dir / "normalized"
        for d in (self.raw, self.norm):
            d.mkdir(parents=True, exist_ok=True)
        self.created = self.dir.name

    def owner(self, key: str) -> Path:
        return Path(self.cfg["owner"]["root"]) / self.cfg["owner"][key]


# ── 1. snapshot ──────────────────────────────────────────────────────────────

def snapshot(run: Run) -> None:
    src = run.cfg["sources"]
    copied = load(run.raw / "snapshot.json", {})
    for source, files in src["raw_files"].items():
        folder = HERE / src[source]
        for name in files:
            dst = run.raw / source / name
            p = folder / name
            if not p.exists():
                copied.setdefault(f"{source}/{name}", {"missing": True})
                continue
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst)
            copied[f"{source}/{name}"] = {"from": str(p), "sha256": sha256(dst), "bytes": dst.stat().st_size,
                                          "source_modified_utc": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")}
    (run.raw / "snapshot.json").write_text(json.dumps(copied, indent=1, sort_keys=True), encoding="utf-8")
    log(f"  snapshot: {sum(1 for v in copied.values() if not v.get('missing'))} files, {sum(1 for v in copied.values() if v.get('missing'))} missing")


# ── 2. assets ────────────────────────────────────────────────────────────────

KINDS = {".mp3": "music", ".wav": "music", ".m4a": "music", ".png": "image", ".jpg": "image", ".psd": "image-source",
         ".mp4": "video", ".ttf": "font", ".otf": "font", ".txt": "licence", ".json": "text", ".m3u": "playlist"}


def probe(ffprobe: str, path: Path) -> dict:
    p = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration,bit_rate:format_tags=encoder,creation_time,comment",
                        "-of", "json", str(path)], capture_output=True, text=True, errors="replace")
    try:
        f = json.loads(p.stdout or "{}").get("format", {})
    except json.JSONDecodeError:
        return {}
    return {"duration_s": round(float(f["duration"]), 2) if f.get("duration") else None, "tags": f.get("tags", {})}


def assets(run: Run) -> None:
    out = run.raw / "assets.json"
    if out.exists():
        log("  assets: already inventoried (raw/assets.json is immutable)")
        return
    o = run.cfg["owner"]
    root = Path(o["root"])
    files = []
    for key in ("sounds", "originals", "images", "videos", "fonts"):
        folder = run.owner(key)
        for p in sorted(folder.iterdir()) if folder.exists() else []:
            if p.is_file():
                files.append((key, p))
    files.append(("quotes", run.owner("quotes")))
    for name in o["renders"] + o["render_reports"]:
        files.append(("renders", root / o["render_dir"] / name))
    inv = []
    for group, p in files:
        if not p.exists():
            inv.append({"group": group, "path": str(p.relative_to(root)), "missing": True})
            continue
        rec = {"group": group, "path": str(p.relative_to(root)).replace("\\", "/"), "kind": KINDS.get(p.suffix.lower(), "other"),
               "bytes": p.stat().st_size, "sha256": sha256(p),
               "modified_utc": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")}
        if p.suffix.lower() in (".mp3", ".mp4", ".m4a", ".wav"):
            rec.update(probe(o["ffprobe"], p))
        inv.append(rec)
        log(f"    {rec['path']}")
    quotes = load(run.owner("quotes"), {}).get("quotes", [])
    write_once(out, {"root": str(root), "files": inv, "quotes": quotes,
                     "inventoried_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    log(f"  assets: {len(inv)} files, {len(quotes)} quotes")


# ── 3. audio ─────────────────────────────────────────────────────────────────

def playlist_tracks(run: Run) -> list[Path]:
    lines = run.owner("playlist").read_text(encoding="utf-8").splitlines()
    return [run.owner("sounds") / l.strip() for l in lines if l.strip() and not l.startswith("#")]


def audio(run: Run) -> None:
    out = run.raw / "audio_audit.json"
    if out.exists():
        log("  audio: already audited")
        return
    from analysis import audio_audit as AA
    tracks = playlist_tracks(run)
    originals = {p.name: run.owner("originals") / p.name for p in tracks}
    res = AA.audit(run.cfg["owner"]["ffmpeg"], tracks, originals)
    res["audited_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    write_once(out, res)
    log(f"  audio: {len(res['tracks'])} tracks audited")


# ── 4. sample ────────────────────────────────────────────────────────────────

def channel_sources(run: Run) -> dict:
    """Every channel upload list we hold: the 2026-10-27 refresh (validation/tabs.json) + channel pages read here."""
    out = {}
    for cid, t in (load(run.raw / "validation" / "tabs.json", {}) or {}).items():
        if t.get("entries"):
            out[cid] = {**t, "source": "validation/tabs.json"}
    for p in sorted((run.raw / "channel_pages").glob("*.json")):
        t = load(p)
        if t and t.get("entries"):
            out[t["channel_id"]] = {**t, "source": f"channel_pages/{p.name}"}
    return out


def new_candidates(run: Run, known: set, per_direction: int) -> list[str]:
    """Channels behind this month's on-niche uploads, per direction, not yet read: the most uploads first."""
    fresh = load(run.raw / "validation" / "fresh_month.json", {}) or {}
    picked = []
    for d in PL.DIRECTIONS:
        count = Counter()
        for key, v in sorted(fresh.items()):
            if not key.startswith(d + "|"):
                continue
            for e in v.get("entries", []):
                if e.get("channel_id") and PL.on_niche(e.get("title") or "") and e["channel_id"] not in known:
                    count[e["channel_id"]] += 1
        for cid, _ in sorted(count.items(), key=lambda x: (-x[1], x[0]))[:per_direction]:
            if cid not in picked:
                picked.append(cid)
    return picked


def sample(run: Run) -> None:
    from platforms import youtube as YT
    s = run.cfg["sample"]
    known = set(channel_sources(run))
    todo = [c for c in new_candidates(run, known, s["new_candidates_per_direction"])
            if not (run.raw / "channel_pages" / f"{c}.json").exists()]
    failed = load(run.raw / "failed.json", {}) or {}
    log(f"  sample: {len(known)} channels held, {len(todo)} new channels to read")
    for i, cid in enumerate(todo, 1):
        if cid in failed:
            continue
        try:
            page = YT.channel_page(cid, s["page_limit"])
        except YT.Blocked:
            raise
        except Exception as e:   # recorded, not retried in this run
            failed[cid] = str(e)[:200]
            (run.raw / "failed.json").write_text(json.dumps(failed, indent=1), encoding="utf-8")
            continue
        page["fetched_at"] = time.time()
        write_once(run.raw / "channel_pages" / f"{cid}.json", page)
        log(f"    {i}/{len(todo)} {page.get('channel')}: {len(page['entries'])} uploads, subs {page.get('subs')}")


# ── 5. previews ──────────────────────────────────────────────────────────────

def previews(run: Run, start: float = 600, seconds: int = 40) -> None:
    """V1 minimal (the base video), V2 + speaker light, V3 + light, quotes and scroll - all from the same 40-s excerpt
    copied into the run folder, so the owner's finished videos are only read. Blind copies A/B/C + key."""
    o = run.cfg["owner"]
    root = Path(o["root"])
    vis = run.dir / "visual"
    vis.mkdir(exist_ok=True)
    excerpt = vis / "excerpt.mp4"
    if not excerpt.exists():
        subprocess.run([o["ffmpeg"], "-v", "error", "-ss", str(start), "-t", str(seconds), "-i", str(root / o["render_dir"] / "final_video.mp4"),
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "copy", str(excerpt)], check=True)
    py = root / ".venv" / "Scripts" / "python.exe"
    tool = root / "tools" / "speaker_light.py"
    if not (vis / "excerpt_light.mp4").exists():
        subprocess.run([str(py), str(tool), str(excerpt)], check=True, cwd=root)
    if not (vis / "excerpt_light_scroll.mp4").exists():
        subprocess.run([str(py), str(tool), str(excerpt), "--quotes", str(run.owner("quotes")),
                        "--scroll", str(run.owner("images") / "roller.png")], check=True, cwd=root)
    variants = {"V1": excerpt, "V2": vis / "excerpt_light.mp4", "V3": vis / "excerpt_light_scroll.mp4"}
    key_file = vis / "blind_key.json"
    if not key_file.exists():
        letters = ["A", "B", "C"]
        random.Random(run.created).shuffle(letters)
        key = dict(zip(letters, variants))
        for letter, v in key.items():
            shutil.copy2(variants[v], vis / f"clip_{letter}.mp4")
        key_file.write_text(json.dumps({"seed": run.created, "key": key, "excerpt_start_s": start, "seconds": seconds}, indent=1),
                            encoding="utf-8")
    log(f"  previews: {', '.join(f'{k}={v.name}' for k, v in variants.items())}; blind copies clip_A/B/C.mp4")


# ── 6. build ─────────────────────────────────────────────────────────────────

def videos_and_channels(run: Run) -> tuple[list[dict], list[dict]]:
    s = run.cfg["sample"]
    rows, chans = [], []
    for cid, t in sorted(channel_sources(run).items()):
        at = t["fetched_at"]
        ents = t["entries"]
        dated = [e for e in ents if e.get("timestamp")]
        first_age = PL.age_days(min(e["timestamp"] for e in dated), at) if dated else None
        complete = len(ents) < s["page_limit"]
        base = PL.baseline(ents, at)
        long_on = [e for e in ents if (e.get("duration") or 0) > 180 and PL.on_niche(e.get("title") or "")]
        dirs = Counter(PL.direction(e.get("title") or "") for e in long_on)
        long_all = [e for e in ents if (e.get("duration") or 0) > 180]
        niche_share = len(long_on) / len(long_all) if long_all else 0
        dom = (dirs.most_common(1)[0][0] if long_on and dirs.most_common(1)[0][1] >= s["min_on_niche_uploads"]
               and niche_share >= s["min_on_niche_share"] else "off-niche")
        span = PL.age_days(min(e["timestamp"] for e in dated), max(e["timestamp"] for e in dated)) if len(dated) > 1 else None
        rel = [PL.relative(e.get("views"), base) for e in long_on if e.get("timestamp")
               and 14 <= (PL.age_days(e["timestamp"], at) or -1) <= 365]
        rel = [x for x in rel if x is not None]
        chans.append({"channel_id": cid, "channel": t.get("channel"), "url": f"https://www.youtube.com/channel/{cid}",
                      "subs": t.get("subs"), "size_band": PL.size_band(t.get("subs")), "listed_uploads": len(ents),
                      "history_complete": complete, "first_listed_upload_age_d": first_age,
                      "channel_age_band": PL.channel_age_band(first_age, complete),
                      "uploads_per_week": round(len(dated) / max((span or 0) / 7, 1), 2) if span else None,
                      "on_niche_long_uploads": len(long_on), "direction": dom, "baseline_n": base["n"],
                      "baseline_median": base["median"], "baseline_usable": base["usable"], "baseline_note": base["reason"],
                      "share_2x_own": round(sum(x >= 2 for x in rel) / len(rel), 3) if rel else None,
                      "share_half_own": round(sum(x <= 0.5 for x in rel) / len(rel), 3) if rel else None,
                      "source": t["source"], "fetched_utc": datetime.fromtimestamp(at, timezone.utc).date().isoformat()})
        for e in ents:
            title = e.get("title") or ""
            age = PL.age_days(e.get("timestamp"), at)
            rows.append({"id": e["id"], "url": f"https://www.youtube.com/watch?v={e['id']}", "channel_id": cid,
                         "channel": t.get("channel"), "title": title, "upload_ts_approx": e.get("timestamp"), "age_days": age,
                         "age_band": PL.age_band(age), "duration_s": e.get("duration"),
                         "format": "long" if (e.get("duration") or 0) > 180 else "short-or-unknown",
                         "views": e.get("views"), "views_precision": PL.views_precision(e.get("views")),
                         "on_niche": PL.on_niche(title), "direction": PL.direction(title) if PL.on_niche(title) else "off-niche",
                         "relative_to_channel": PL.relative(e.get("views"), base), "subs": t.get("subs"),
                         "size_band": PL.size_band(t.get("subs")), "channel_age_band": PL.channel_age_band(first_age, complete),
                         "fetched_at": at, "source": t["source"]})
    return PL.dedupe(rows), chans


def stratified(chans: list[dict], per_direction: int) -> dict[str, list[dict]]:
    """Per direction, round-robin over (channel age x size) strata so new and small channels are not crowded out
    by big established ones; inside a stratum, by channel ID (deterministic, not by success)."""
    out = {}
    for d in PL.DIRECTIONS:
        pool = [c for c in chans if c["direction"] == d]
        strata = defaultdict(list)
        for c in sorted(pool, key=lambda c: c["channel_id"]):
            strata[(c["channel_age_band"], c["size_band"])].append(c)
        picked, keys = [], sorted(strata)
        while len(picked) < per_direction and any(strata[k] for k in keys):
            for k in keys:
                if strata[k] and len(picked) < per_direction:
                    picked.append(strata[k].pop(0))
        out[d] = picked
    return out


def direction_stats(rows: list[dict], sampled: dict[str, list[dict]], run: Run) -> dict:
    res = {}
    fresh = load(run.raw / "validation" / "fresh_month.json", {}) or {}
    searches = load(run.raw / "validation" / "searches.json", {}) or {}
    for d, chans in sampled.items():
        ids = {c["channel_id"] for c in chans}
        mine = [r for r in rows if r["channel_id"] in ids and r["direction"] == d and r["format"] == "long" and r["views"] is not None]
        settled = [r["views"] for r in mine if r["age_days"] is not None and 30 <= r["age_days"] <= 365]
        recent = [r["views"] for r in mine if r["age_days"] is not None and 7 <= r["age_days"] <= 60]
        small = [r["views"] for r in mine if r["age_days"] is not None and 30 <= r["age_days"] <= 365 and r["size_band"] in ("<1K", "1K-10K")]
        meds = [c["baseline_median"] for c in chans if c["baseline_median"] is not None]
        fresh_e = {e["id"]: e for k, v in fresh.items() if k.startswith(d + "|") for e in v.get("entries", [])}
        fresh_on = [e for e in fresh_e.values() if PL.on_niche(e.get("title") or "") and e.get("views") is not None]
        inc = {e["id"]: e for k, v in searches.items() if k.startswith(d + "|") and k.endswith("|relevance") for e in v.get("entries", [])}
        inc_v = [e["views"] for e in inc.values() if e.get("views") is not None]
        res[d] = {
            "label": PL.LABELS[d], "channels": len(chans),
            "strata": dict(Counter(f"{c['channel_age_band']} / {c['size_band']}" for c in chans)),
            "settled_uploads": PL.summarize(settled), "recent_uploads": PL.summarize(recent),
            "small_channel_settled": PL.summarize(small), "channel_medians": PL.summarize(meds),
            "channel_medians_without_top": PL.summarize(PL.without_top(meds)),
            "consistency_share_2x_own": PL.summarize([c["share_2x_own"] for c in chans if c["share_2x_own"] is not None]),
            "new_channels": [{"channel": c["channel"], "subs": c["subs"], "first_listed_upload_age_d": c["first_listed_upload_age_d"],
                              "baseline_median": c["baseline_median"], "uploads_per_week": c["uploads_per_week"]}
                             for c in chans if c["channel_age_band"].startswith("new")],
            "fresh_month": {"uploads": len(fresh_on), "channels": len({e.get("channel_id") for e in fresh_on}),
                            **{k: v for k, v in PL.summarize([e["views"] for e in fresh_on]).items() if k != "ci90"}},
            "search_incumbents": {k: v for k, v in PL.summarize(inc_v).items() if k in ("n", "median", "p25", "p75")}
                                 | {"share_1m_plus": round(sum(v >= 1_000_000 for v in inc_v) / len(inc_v), 3) if inc_v else None},
        }
    return res


def velocity_stats(run: Run) -> dict:
    v = load(run.raw / "validation" / "velocity.json", {}) or {}
    out = defaultdict(list)
    for vid, x in sorted(v.items()):
        if not (x.get("now") and x.get("then") and x.get("timestamp")) or x["now_at"] - x["then_at"] < 43200:
            continue
        age = (x["now_at"] - x["timestamp"]) / 86400
        life = x["now"] / max(age, 1)
        recent = (x["now"] - x["then"]) / ((x["now_at"] - x["then_at"]) / 86400)
        if x["now"] >= 5000 and life > 0:
            out[PL.age_band(age)].append(round(recent / life, 3))
    return {band: {"n": len(r), "median_recent_over_lifetime": statistics.median(r)} for band, r in sorted(out.items())}


def old_report_check(run: Run) -> dict:
    """Reproduce the 2026-10-25 run's headline counts from its raw files, and the checks that undo its claims."""
    disc = load(run.raw / "samurai_zen_run" / "discovery.json", {}) or {}
    scored = load(run.raw / "samurai_zen_run" / "scored.json", {}) or {}
    rows = scored.get("rows", [])
    ids = Counter(r["id"] for r in rows)
    el = [r for r in rows if r["bucket"] == "en-long" and r.get("age_days") is not None]
    by_age = {}
    for name, lo, hi in (("<=30d", 0, 30), (">30d", 30.0001, 1e9)):
        g = [r for r in el if lo <= r["age_days"] <= hi]
        by_age[name] = {"n": len(g), "outliers": sum(r["tier"] == "outlier" for r in g)}
    return {"discovered": {k: len(v) for k, v in disc.items()}, "scored_rows": len(rows), "unique_ids": len(ids),
            "duplicate_ids": sum(1 for n in ids.values() if n > 1), "dropped": scored.get("dropped"),
            "off_topic_rows": sum(1 for r in rows if PL.OFF_TOPIC.search(f"{r.get('channel') or ''} {r.get('title') or ''}")),
            "en_long_outlier_rate": round(sum(r["tier"] == "outlier" for r in rows if r["bucket"] == "en-long") /
                                          max(1, sum(r["bucket"] == "en-long" for r in rows)), 3),
            "en_long_outliers_by_age": by_age}


# ── tables ───────────────────────────────────────────────────────────────────

def md_table(header: list[str], rows: list[list]) -> str:
    def cell(x):
        if x is None:
            return "–"
        if isinstance(x, float):
            return f"{x:,.2f}".rstrip("0").rstrip(".") if abs(x) < 100 else f"{x:,.0f}"
        if isinstance(x, int):
            return f"{x:,}"
        return str(x).replace("|", "/")
    return "\n".join(["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] +
                     ["| " + " | ".join(cell(c) for c in r) + " |" for r in rows])


def fmt_ci(s: dict) -> str:
    if not s.get("n"):
        return "n=0"
    ci = s.get("ci90")
    core = f"{s['median']:,.0f}" + (f" [{ci[0]:,.0f}–{ci[1]:,.0f}]" if ci else "")
    return core + f" (n={s['n']}{', small' if s.get('small_sample') else ''})"


BENCH_AGES = (("0-7d", 0, 7), ("8-30d", 7.0001, 30), ("31-90d", 30.0001, 90))


def benchmark_table(rows: list[dict]) -> str:
    """Expected ranges for a new channel's videos: views at matched age, long on-niche uploads of every held channel
    under 10K subscribers (not only the sample, for sample size)."""
    out = []
    for d in PL.DIRECTIONS:
        for name, lo, hi in BENCH_AGES:
            v = [r["views"] for r in rows if r["direction"] == d and r["format"] == "long" and r["views"] is not None
                 and r["size_band"] in ("<1K", "1K-10K") and r["age_days"] is not None and lo <= r["age_days"] <= hi]
            s = PL.summarize(v)
            out.append([PL.LABELS[d], name, s["n"], s.get("p25"), s.get("median"), s.get("p75"),
                        "small sample" if s.get("small_sample") or not s["n"] else ""])
    return md_table(["Direction", "Video age", "Uploads", "p25 views", "Median views", "p75 views", "Note"], out)


def tables(stats: dict, rows: list[dict], audio: dict, rights: list[dict], old: dict, vel: dict, econ: dict) -> dict:
    t = {}
    order = list(PL.DIRECTIONS)
    t["comparison_table"] = md_table(
        ["Measure (MEASURED)"] + [stats[d]["label"] for d in order],
        [["Channels sampled: total (new <12 mo · under 10K subs · 100K+ subs)"] + [
            f"{stats[d]['channels']} ({sum(v for k, v in stats[d]['strata'].items() if k.startswith('new'))} · "
            f"{sum(v for k, v in stats[d]['strata'].items() if k.endswith(('<1K', '1K-10K')))} · "
            f"{sum(v for k, v in stats[d]['strata'].items() if k.endswith('>=100K'))})" for d in order],
         ["Uploads 30–365 d: median [90% CI] views"] + [fmt_ci(stats[d]["settled_uploads"]) for d in order],
         ["Uploads 30–365 d: IQR"] + [f"{stats[d]['settled_uploads'].get('p25') or 0:,.0f}–{stats[d]['settled_uploads'].get('p75') or 0:,.0f}" if stats[d]["settled_uploads"].get("n") else "–" for d in order],
         ["Uploads 30–365 d: share under 1K / 10K+"] + [f"{stats[d]['settled_uploads'].get('share_under_1k', 0):.0%} / {stats[d]['settled_uploads'].get('share_10k_plus', 0):.0%}" if stats[d]["settled_uploads"].get("n") else "–" for d in order],
         ["Same, channels under 10K subs"] + [fmt_ci(stats[d]["small_channel_settled"]) for d in order],
         ["Uploads 7–60 d (recent)"] + [fmt_ci(stats[d]["recent_uploads"]) for d in order],
         ["Channel medians: median [90% CI]"] + [fmt_ci(stats[d]["channel_medians"]) for d in order],
         ["…without the top channel (sensitivity)"] + [fmt_ci(stats[d]["channel_medians_without_top"]) for d in order],
         ["Share of a channel's uploads ≥2× its own median (median across channels)"] + [f"{stats[d]['consistency_share_2x_own']['median']:.0%}" if stats[d]["consistency_share_2x_own"].get("n") else "–" for d in order],
         ["New channels (<12 months) in sample"] + [len(stats[d]["new_channels"]) for d in order],
         ["This month's search-visible uploads: count / channels / median views"] + [f"{stats[d]['fresh_month']['uploads']} / {stats[d]['fresh_month']['channels']} / {stats[d]['fresh_month'].get('median') or 0:,.0f}" for d in order],
         ["Top-60 search incumbents: median views / share ≥1M"] + [f"{stats[d]['search_incumbents'].get('median') or 0:,.0f} / {stats[d]['search_incumbents'].get('share_1m_plus') or 0:.0%}" for d in order]])
    t["new_channels_table"] = md_table(["Direction", "Channel", "Subs", "First listed upload (days ago)", "Typical views (14–365 d)", "Uploads/week"],
        [[stats[d]["label"].split(" ", 1)[0], c["channel"], c["subs"], c["first_listed_upload_age_d"], c["baseline_median"], c["uploads_per_week"]]
         for d in order for c in stats[d]["new_channels"]])
    t["benchmark_table"] = benchmark_table(rows)
    t["velocity_table"] = md_table(["Video age", "Videos (≥5K views)", "Median of recent ÷ lifetime views/day"],
                                   [[b, v["n"], v["median_recent_over_lifetime"]] for b, v in vel.items()])
    t["old_check_table"] = md_table(["Check (reproduced from raw files)", "Result"], [
        ["Videos discovered (en / es)", f"{old['discovered'].get('en', 0):,} / {old['discovered'].get('es', 0):,}"],
        ["Scored rows / unique video IDs / IDs counted twice", f"{old['scored_rows']} / {old['unique_ids']} / {old['duplicate_ids']}"],
        ["Dropped before scoring", json.dumps(old["dropped"])],
        ["Rows from an unrelated artist or non-music video", old["off_topic_rows"]],
        ["Share of English long videos called 'outliers'", f"{old['en_long_outlier_rate']:.0%}"],
        ["Outliers among videos ≤30 days old", f"{old['en_long_outliers_by_age']['<=30d']['outliers']} of {old['en_long_outliers_by_age']['<=30d']['n']}"],
        ["Outliers among videos >30 days old", f"{old['en_long_outliers_by_age']['>30d']['outliers']} of {old['en_long_outliers_by_age']['>30d']['n']}"]])
    tr = audio["tracks"]
    t["music_table"] = md_table(["#", "Track", "Length", "LUFS (orig → now)", "True peak dBTP (orig → now)", "LRA LU (orig → now)",
                                 "Beat clarity", "≈BPM", "Starts hard", "EQ: lows / low-mids / highs (dB)"],
        [[i, n.rsplit("_", 1)[0].replace("_", " "), f"{r['duration_s'] / 60:.2f} min",
          f"{(r.get('original') or {}).get('integrated_lufs')} → {r['integrated_lufs']}",
          f"{(r.get('original') or {}).get('true_peak_dbtp')} → {r['true_peak_dbtp']}",
          f"{(r.get('original') or {}).get('lra_lu')} → {r['lra_lu']}", r["beat_clarity"], r["tempo_bpm_est"],
          "yes" if r["edges"]["hard_start"] else "no",
          " / ".join(f"{(r.get('eq_change_db') or {}).get(k, 0):+.1f}" for k in ("20-150Hz", "150-500Hz", "6000-11000Hz"))]
         for i, (n, r) in enumerate(tr.items(), 1)])
    t["pairs_table"] = md_table(["Track A", "Track B", "Same generation", "Shared-passage score", "Where (A s / B s)"],
        [[p["a"].rsplit("_", 1)[0].replace("_", " "), p["b"].rsplit("_", 1)[0].replace("_", " "), "yes" if p["same_generation"] else "no",
          p["score"], f"{p['a_at_s']} / {p['b_at_s']}"]
         for p in sorted(audio["pairs"], key=lambda p: -(p["score"] or 0)) if p["above_unrelated_p95"] or p["same_generation"]])
    t["unrelated_p95"] = f"{audio['unrelated_pair_p95']:.3f}"
    t["unrelated_median"] = f"{audio['unrelated_pair_median']:.3f}"
    t["joins_table"] = md_table(["After", "Next", "End of previous (dB)", "Start of next (dB)", "Jump (dB)", "Next starts hard"],
        [[j["from"].rsplit("_", 1)[0].replace("_", " "), j["to"].rsplit("_", 1)[0].replace("_", " "), j["end_of_a_db"], j["start_of_b_db"],
          j["jump_db"], "yes" if j["b_hard_start"] else "no"] for j in audio["joins"]])
    cnt = Counter(r["status"] for r in rights)
    t["rights_summary"] = md_table(["Status", "Assets"], [[s, cnt.get(s, 0)] for s in PL.RIGHTS])
    t["rights_blocked"] = "\n".join(f"- `{r['asset']}` — {r['reasons']}" for r in rights if r["status"] == "BLOCKED") or "- none"
    t.update(econ_tables(econ))
    return t


def econ_tables(e: dict) -> dict:
    """Production capacity and cost per finished video under stated assumptions; every input is in economics.json."""
    t = {}
    rows = []
    for s in e["scenarios"]:
        kept_min = e["music_generation_minutes"][s["music_plan"]] * s["kept_share"]
        videos = kept_min / s["video_minutes"]
        monthly = sum(e["plans_usd"][p] for p in s["paid_tools"])
        rows.append([s["name"], s["music_plan"], " + ".join(s["paid_tools"]), f"${monthly:,.2f}", f"{s['kept_share']:.0%}",
                     f"{kept_min:.0f} min", f"{s['video_minutes']} min", f"{videos:.1f}",
                     f"${monthly / videos:,.2f}" if videos else "–", s["note"]])
    t["capacity_table"] = md_table(["Scenario", "Music plan", "Paid tools", "Monthly cost (list)", "Kept share (assumption)",
                                    "Unique music kept / month", "Video length", "Unique videos / month", "Tool cost per video", "Note"], rows)
    rev = []
    for rpm in e["illustrative_rpm_usd"]:
        rev.append([f"${rpm:.2f}", f"{e['budget_usd'] / (rpm / 1000):,.0f}"])
    t["breakeven_table"] = md_table(["Illustrative RPM (not an estimate)", f"Monetized views per month to cover ${e['budget_usd']}"], rev)
    return t


def render(template: str, values: dict) -> str:
    missing = sorted(set(re.findall(r"{{(\w+)}}", template)) - set(values))
    if missing:
        raise KeyError(f"template placeholders without a value: {missing}")
    return re.sub(r"{{(\w+)}}", lambda m: str(values[m.group(1)]), template)


def csv_text(rows: list[dict], fields: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v) for k, v in r.items()})
    return buf.getvalue()


def rights_rows(run: Run) -> list[dict]:
    """Every inventoried asset + every quote, classified by analysis/prelaunch.rights_status from the facts in
    niches/<niche>/rights.json (defaults per group, overrides per file, quote classes from the provenance check)."""
    facts = load(run.knowledge / "rights.json")
    inv = load(run.raw / "assets.json")
    out = []
    for f in inv["files"]:
        if f.get("missing"):
            out.append({"asset": f["path"], "kind": "missing", "status": "NEEDS EVIDENCE", "reasons": "file not found", "sha256": ""})
            continue
        group_facts = dict(facts["groups"].get(f["group"], {}))
        for pattern, over in facts.get("overrides", {}).items():
            if re.search(pattern, f["path"]):
                group_facts.update(over)
        status, reasons = PL.rights_status(group_facts)
        out.append({"asset": f["path"], "kind": f["kind"], "sha256": f["sha256"], "status": status, "reasons": "; ".join(reasons),
                    "intended_use": group_facts.get("intended_use", ""), "provider": group_facts.get("provider", ""),
                    "evidence": group_facts.get("evidence", ""), "missing_provenance": group_facts.get("missing_provenance", ""),
                    "open_questions": "; ".join(group_facts.get("clarify") or []),
                    "conditions": "; ".join(group_facts.get("conditions") or [])})
    classes = facts["quotes"]["classes"]
    for i, q in enumerate(inv["quotes"], 1):
        cls = facts["quotes"]["by_number"].get(str(i), "ORIGINAL/GENERIC")
        qf = dict(classes[cls])
        status, reasons = PL.rights_status(qf)
        out.append({"asset": f"quote #{i}: {q['en'][:60]}", "kind": f"quote ({cls})", "sha256": "", "status": status,
                    "reasons": "; ".join(reasons), "intended_use": qf.get("intended_use", ""), "provider": "Gemini suggestion",
                    "evidence": facts["quotes"].get("source_note", {}).get(str(i), qf.get("evidence", "")),
                    "missing_provenance": qf.get("missing_provenance", ""), "open_questions": "",
                    "conditions": "; ".join(qf.get("conditions") or [])})
    return out


def build(run: Run) -> None:
    s = run.cfg["sample"]
    rows, chans = videos_and_channels(run)
    sampled = stratified(chans, s["channels_per_direction"])
    in_sample = {c["channel_id"]: d for d, cs in sampled.items() for c in cs}
    for c in chans:
        c["in_sample_for"] = in_sample.get(c["channel_id"], "")
    for r in rows:
        r["in_sample"] = r["channel_id"] in in_sample
    stats = direction_stats(rows, sampled, run)
    vel = velocity_stats(run)
    old = old_report_check(run)
    audio_res = load(run.raw / "audio_audit.json")
    rights = rights_rows(run)
    econ = load(run.knowledge / "economics.json")
    claims = load(run.knowledge / "claims.json")["claims"]
    problems = {c["id"]: PL.check_claim(c) for c in claims}
    problems = {k: v for k, v in problems.items() if v}
    if problems:
        raise ValueError(f"evidence ledger entries with problems: {problems}")
    analysis = {"directions": stats, "velocity": vel, "old_report_check": old, "sampled_channels": {d: [c["channel_id"] for c in cs] for d, cs in sampled.items()},
                "rights_counts": dict(Counter(r["status"] for r in rights))}
    (run.dir / "analysis.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    vid_fields = ["id", "url", "channel_id", "channel", "title", "upload_ts_approx", "age_days", "age_band", "duration_s", "format",
                  "views", "views_precision", "on_niche", "direction", "relative_to_channel", "subs", "size_band", "channel_age_band",
                  "in_sample", "fetched_at", "source"]
    (run.dir / "competitor_dataset.csv").write_text(csv_text([r for r in rows if r["in_sample"]], vid_fields), encoding="utf-8")
    (run.norm / "all_videos.csv").write_text(csv_text(rows, vid_fields), encoding="utf-8")
    ch_fields = list(chans[0].keys()) if chans else []
    (run.norm / "channels.csv").write_text(csv_text(chans, ch_fields), encoding="utf-8")
    (run.dir / "rights_matrix.csv").write_text(csv_text(rights, ["asset", "kind", "status", "reasons", "open_questions", "conditions",
                                                                  "intended_use", "provider", "evidence", "missing_provenance", "sha256"]),
                                               encoding="utf-8")
    (run.dir / "evidence_ledger.csv").write_text(csv_text(claims, ["id", "claim", "status", "confidence", "source", "date", "limitations",
                                                                   "decision_relevance"]), encoding="utf-8")
    values = tables(stats, rows, audio_res, rights, old, vel, econ)
    values.update({k: v for k, v in load(run.knowledge / "values.json", {}).items()})
    for name in OUTPUTS:
        tpl = (run.knowledge / name).read_text(encoding="utf-8")
        (run.dir / name).write_text(render(tpl, values), encoding="utf-8")
    tools = tool_versions(run)   # recorded in raw/ before raw/ is hashed, so the first build's manifest equals the next
    manifest = {
        "run": run.created, "niche": run.niche, "settings": run.cfg["sample"],
        "raw": {str(p.relative_to(run.raw)).replace("\\", "/"): sha256(p) for p in sorted(run.raw.rglob("*")) if p.is_file()},
        "knowledge": {p.name: sha256(p) for p in sorted(run.knowledge.glob("*")) if p.is_file()},
        "outputs": sorted(OUTPUTS + ["competitor_dataset.csv", "rights_matrix.csv", "evidence_ledger.csv", "analysis.json"]),
        "tools": tools, "failed_fetches": load(run.raw / "failed.json", {}),
        "limitations": load(run.knowledge / "values.json", {}).get("limitations_list", []),
    }
    (run.dir / "research_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    log(f"  build: {len(rows)} videos / {len(chans)} channels held, {sum(len(v) for v in sampled.values())} sampled; "
        f"{len(rights)} rights rows; outputs in {run.dir}")


def tool_versions(run: Run) -> dict:
    cached = run.raw / "tools.json"
    if not cached.exists():
        def first_line(cmd):
            try:
                return subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=30).stdout.splitlines()[0]
            except Exception as e:
                return f"unavailable: {e}"
        from platforms import youtube as YT
        write_once(cached, {"python": sys.version.split()[0], "ffmpeg": first_line([run.cfg["owner"]["ffmpeg"], "-version"]),
                            "yt-dlp": first_line([YT.YTDLP, "--version"])})
    return load(cached)


def main(niche: str, run_dir: Path | None, only: str | None = None) -> int:
    """All stages except `previews` (it renders, so it runs only when asked for by name), or just `only`."""
    run = Run(niche, run_dir)
    log(f"prelaunch run: {run.dir}")
    steps = {"snapshot": snapshot, "assets": assets, "audio": audio, "sample": sample, "previews": previews, "build": build}
    names = [only] if only else [s for s in STAGES if s != "previews"]
    for name in names:
        log(f"- {name}")
        steps[name](run)
    return 0
