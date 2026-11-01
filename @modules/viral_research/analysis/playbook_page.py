"""The niche playbook page: <niche folder>/playbook/index.html, built from a run (the evidence) and
niches/<niche>.playbook.json (the formulas and ideas Claude writes from it).

Formula cards never carry typed numbers: each evidence item points at a stat ("slot", "measure", "concept",
"channels") and the numbers are filled in here from the run's files, so a rebuild after a new run updates them.
The scorecard keeps whatever the user already filled in."""
from __future__ import annotations

import csv
import json
import shutil
import statistics
from pathlib import Path

from analysis import playbook as PB

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "playbook_template.html"
SCORE_FIELDS = ["idea", "ranking", "concept", "working_title", "score", "predicted_band_vs_your_median", "spice_lever",
                "published_url", "published_date", "your_channel_median_at_publish", "views_day_7", "views_day_28",
                "multiplier_day_28", "in_band"]


def _j(p: Path, default=None):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def _pct(k, n):
    return f"{k}/{n}" if n else "-"


def resolve(ref: dict, stats: dict, analysis: dict, concepts: list[dict]) -> dict:
    """One evidence item -> {"kind", "label", "value", "strength"}; strength: strong / some / none / info."""
    label = ref.get("label", "")
    if "slot" in ref:                                   # "es-long/tags/count"
        rank, part, key = ref["slot"].split("/")
        v = (((stats.get("rankings") or {}).get(rank) or {}).get(part) or {}).get(key)
        if not v:
            return {"kind": "measured", "label": label, "value": "no data", "strength": "none"}
        n_o, n_c = v["n"]
        if "outliers" in v:
            diff = v["outliers"] / n_o - v["controls"] / n_c if n_o and n_c else 0
            strength = "strong" if abs(diff) >= 0.3 and v["outliers"] >= 3 else "some" if abs(diff) >= 0.15 else "none"
            return {"kind": "measured", "label": label, "strength": strength,
                    "value": f"{_pct(v['outliers'], n_o)} outliers vs {_pct(v['controls'], n_c)} controls"}
        o, c = v["outliers_median"], v["controls_median"]
        rel = abs((o or 0) - (c or 0)) / max(abs(c or 0), abs(o or 0), 1e-9)
        return {"kind": "measured", "label": label, "value": f"median {o} vs {c}",
                "strength": "strong" if rel >= 0.3 else "some" if rel >= 0.15 else "none"}
    if "measure" in ref:                                # "en-long/lufs" from analysis.json
        rank, feature = ref["measure"].split("/")
        c = next((x for x in (analysis.get(rank) or {}).get("comparison", []) if x["feature"] == feature), None)
        if not c:
            return {"kind": "measured", "label": label, "value": "no data", "strength": "none"}
        if c["kind"] == "bool":
            value = f"{c['outliers']} outliers ({c['rate_outliers']:.0%}) vs {c['rate_controls']:.0%} of controls"
        else:
            value = f"median {c['median_outliers']} vs {c['median_controls']}"
        return {"kind": "measured", "label": label, "value": value, "strength": "strong" if c["pattern"] else "some"}
    if "concept" in ref:
        c = next((x for x in concepts if x["name"] == ref["concept"]), None)
        if not c:
            return {"kind": "measured", "label": label, "value": "no data", "strength": "none"}
        return {"kind": "measured", "label": label or c["name"], "strength": "strong" if c["proven"] and (c["lift"] or 0) >= 1.2 else "some",
                "value": f"{c['outliers']}/{c['hits']} beat their channel · {c['channels_with_outliers']} channels · "
                         f"lift {c['lift']} · median {c['median_multiplier']}×"}
    if "channels" in ref:
        return {"kind": "measured", "label": label, **channel_aggregate(ref["channels"], stats.get("channels") or [])}
    return {"kind": ref.get("kind", "practice"), "label": label, "value": ref.get("text", ""), "strength": "info"}


def channel_aggregate(name: str, channels: list[dict]) -> dict:
    long = [c for c in channels if c["ranking"].endswith("long")]
    one_sound = [c for c in long if (c.get("consistency") or 0) >= 0.6]
    if name == "one_sound":
        return {"value": f"{len(one_sound)} of {len(long)} winning long-video channels keep one sound or title signature "
                         f"on 60%+ of uploads", "strength": "some"}
    if name == "sound_in_name":
        genre = [c for c in one_sound if (c.get("main_genre_share") or 0) >= 0.5]
        named = [c for c in genre if c["genre_or_era_word"]]
        return {"value": f"{len(named)} of {len(genre)} single-genre winning channels put the sound in the name "
                         f"({', '.join(c['name'] for c in named)})", "strength": "some"}
    if name == "niche_in_name":
        named = [c for c in long if c.get("niche_word_in_name")]
        return {"value": f"{len(named)} of {len(long)} winning long-video channels say the niche in their name "
                         f"({', '.join(c['name'] for c in named[:6])})", "strength": "some"}
    if name == "rhythm":
        w = sorted(c["uploads_per_week_90d"] for c in long if c.get("uploads_per_week_90d") is not None)
        return {"value": f"median {statistics.median(w)} uploads/week (range {w[0]}–{w[-1]}, {len(w)} channels)" if w else "no data",
                "strength": "some"}
    if name == "small":
        f = [c["followers"] for c in long if c.get("followers")]
        small = sum(1 for x in f if x < 10_000)
        return {"value": f"{small} of {len(f)} winning long-video channels had under 10,000 subscribers", "strength": "some"}
    return {"value": "no data", "strength": "none"}


def merge_scorecard(path: Path, ideas: list[dict]) -> list[dict]:
    """Ideas as scorecard rows, keeping what the user already filled in (matched by working title, then number)."""
    old = {}
    if path.exists():
        with path.open(encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                old[row.get("working_title")] = row
                old[f"#{row.get('idea')}"] = row
    rows = []
    for i in ideas:
        row = {"idea": i["n"], "ranking": i["ranking"], "concept": i["concept"], "working_title": i["title"],
               "score": i["score"], "predicted_band_vs_your_median": i["band"], "spice_lever": i["lever"]}
        prev = old.get(i["title"]) or old.get(f"#{i['n']}") or {}
        rows.append({**{k: prev.get(k, "") for k in SCORE_FIELDS}, **row})
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=SCORE_FIELDS)
        w.writeheader()
        w.writerows(rows)
    return rows


def score(idea: dict) -> int:
    return idea["e"] * idea["d"] * idea["g"] - idea.get("risk", 0) - idea.get("cost", 0)


def band(s: int) -> str:
    return "2–5×" if s >= 25 else "1.5–3×" if s >= 10 else "1–2×" if s >= 5 else "0.5–1.5×"


def build(niche_dir: Path, run_dir: Path, content: dict, codebook: list[dict], name_words: str | None = None) -> Path:
    out = niche_dir / "playbook"
    (out / "assets").mkdir(parents=True, exist_ok=True)
    stats = PB.mine(run_dir, codebook, name_words)
    (out / "playbook_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    analysis = _j(run_dir / "analysis.json", {})
    concepts = (_j(run_dir / "concepts.json", {}) or {}).get("concepts", [])
    final = _j(run_dir / "shortlist.json", {})

    def cards(items):
        return [{**c, "evidence": [resolve(r, stats, analysis, concepts) for r in c.get("evidence", [])]} for c in items]

    ideas = []
    for i in content.get("ideas", []):
        s = score(i)
        ideas.append({**i, "score": s, "band": band(s)})
    gallery = []
    for b, p in sorted(final.items()):
        for r in p.get("outliers", [])[:6]:
            src = run_dir / "evidence" / r["id"] / "thumbnail.jpg"
            if src.exists():
                shutil.copyfile(src, out / "assets" / f"{r['id']}.jpg")
            m = _j(run_dir / "meta" / f"{r['id']}.json", {})
            gallery.append({"id": r["id"], "ranking": b, "title": m.get("title") or r.get("title"), "channel": m.get("channel"),
                            "multiplier": r.get("multiplier"), "views": m.get("view_count"), "thumb": f"assets/{r['id']}.jpg"
                            if src.exists() else None, "url": f"https://www.youtube.com/watch?v={r['id']}",
                            "note": (content.get("gallery_notes") or {}).get(r["id"])})
    disc = _j(run_dir / "discovery.json", {}) or {}
    picked = [r for p in final.values() for g in ("outliers", "controls") for r in p.get(g, [])]
    spot = (_j(run_dir / "spot_check.json", {}) or {}).get("results", [])
    meta = [[f"{len({v['id'] for vs in disc.values() for v in vs}):,}", "videos found"],
            [f"{len(_j(run_dir / 'channels.json', {}) or {}):,}", "channels read"],
            [str(sum(len(p.get('outliers', [])) for p in final.values())), "outliers"],
            [str(sum(len(p.get('controls', [])) for p in final.values())), "controls (same channels)"],
            [str(sum((run_dir / 'evidence' / r['id'] / 'measures.json').exists() for r in picked)), "videos measured"],
            [f"{sum(1 for x in spot if x.get('drift') is not None and -0.02 <= x['drift'] <= 0.15)}/{len(spot)}",
             "view counts re-checked on YouTube"]]
    data = {**{k: v for k, v in content.items() if k not in ("channel", "tracks", "ideas")}, "meta": meta,
            "channel": cards(content.get("channel", [])),
            "tracks": [{**t, "cards": cards(t.get("cards", []))} for t in content.get("tracks", [])],
            "ideas": sorted(ideas, key=lambda i: (-i["score"], i["n"])), "gallery": gallery, "concepts": concepts,
            "channels": stats["channels"], "run": run_dir.name}
    merge_scorecard(out / "scorecard.csv", ideas)
    page = TEMPLATE.read_text(encoding="utf-8").replace(
        "__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    (out / "index.html").write_text(page, encoding="utf-8")
    return out / "index.html"
