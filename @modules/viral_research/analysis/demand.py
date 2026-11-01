"""Demand and gaps behind the ideas: what viewers ask for, where they rewatch, which concepts beat their channels'
normal and how crowded they are. Pure functions over what a run already saved, plus YouTube's search
autocomplete (a separate, light endpoint) for what people type."""
from __future__ import annotations

import json
import re
import statistics
import time
import urllib.parse
import urllib.request

# A viewer asking for a video: "do X next", "part 2", "haz una versión salsa", "porfa sube..."
REQUEST = re.compile(r"\b(please|pls|plz|can you|could you|would you|do (?:a|an|one|the|more)|make (?:a|an|one|more)|"
                     r"next|part 2|part two|more of|we need|i need|cover of|version of|"
                     r"por ?favor|porfa|puedes|podr[ií]as|haz(?:me)?|hagan|necesitamos|quiero (?:una|un|m[aá]s)|"
                     r"parte 2|segunda parte|otra (?:canci[oó]n|versi[oó]n)|versi[oó]n (?:de|en))\b", re.I)


def viewer_requests(comments: list[dict] | None, limit: int = 5) -> list[dict]:
    """Comments that ask for a video, most liked first (the uploader's own replies are not viewers)."""
    asks = [c for c in comments or [] if not c.get("author_is_uploader") and REQUEST.search(c.get("text") or "")]
    asks.sort(key=lambda c: -(c.get("likes") or 0))
    return [{"likes": c.get("likes") or 0, "text": re.sub(r"\s+", " ", c["text"]).strip()[:200]} for c in asks[:limit]]


def replay_peak(heatmap: list[dict] | None, duration: float | None, skip_share: float = 0.05) -> dict | None:
    """The most-replayed moment after the opening (the first 5% is high on every video: everyone starts there)."""
    if not heatmap or not duration:
        return None
    later = [h for h in heatmap if h["start_time"] >= duration * skip_share]
    if not later:
        return None
    top = max(later, key=lambda h: h["value"])
    return {"at_s": round(top["start_time"], 1), "at_pct": round(100 * top["start_time"] / duration),
            "value": round(top["value"], 2)}


def concept_counts(rows: list[dict], codebook: list[dict], holdout_days: int = 30, recent_days: int = 90) -> list[dict]:
    """Each concept of the codebook ({"name", "match": regex, "buckets": [...] optional}) against the scored videos.

    hits / outliers / outlier rate and lift over the bucket's overall outlier rate say whether it is proven;
    channels says it is not one channel's luck; recent supply (videos under `recent_days` old) says how crowded
    it is; the holdout compares the outlier rate on videos older than `holdout_days` with the newest ones, so a
    concept built from the past is checked on videos it was not built from."""
    def rate(rs):
        return round(sum(r["tier"] == "outlier" for r in rs) / len(rs), 3) if rs else None
    overall = {b: rate([r for r in rows if r["bucket"] == b]) for b in {r["bucket"] for r in rows}}
    out = []
    for c in codebook:
        rx = re.compile(c["match"], re.I)
        hits = [r for r in rows if (not c.get("buckets") or r["bucket"] in c["buckets"])
                and rx.search(f"{r.get('title') or ''} {r.get('description') or ''}")]
        outs = [r for r in hits if r["tier"] == "outlier"]
        base = [overall[r["bucket"]] for r in hits if overall.get(r["bucket"]) is not None]
        expected = statistics.mean(base) if base else None
        dated = [r for r in hits if r.get("age_days") is not None]
        older = [r for r in dated if r["age_days"] > holdout_days]
        newest = [r for r in dated if r["age_days"] <= holdout_days]
        out.append({
            "name": c["name"], "hits": len(hits), "outliers": len(outs), "outlier_rate": rate(hits),
            "lift": round(rate(hits) / expected, 2) if hits and expected else None,
            "channels_with_outliers": len({r["channel_id"] for r in outs}),
            "median_multiplier": round(statistics.median([r["multiplier"] for r in outs]), 1) if outs else None,
            "recent_supply": sum(1 for r in dated if r["age_days"] <= recent_days),
            "holdout": {"older_rate": rate(older), "older_n": len(older), "newest_rate": rate(newest), "newest_n": len(newest)},
            "proven": len(outs) >= 3 and len({r["channel_id"] for r in outs}) >= 2,
            "examples": [{"id": r["id"], "multiplier": r["multiplier"], "title": (r.get("title") or "")[:90]}
                         for r in sorted(outs, key=lambda r: -r["multiplier"])[:3]],
        })
    return sorted(out, key=lambda c: (not c["proven"], -(c["lift"] or 0)))


def parse_autocomplete(body: str) -> list[str]:
    """YouTube's suggest endpoint answers JSONP: window.google.ac.h(["query",[["suggestion",0,[...]],...],{...}])."""
    start, end = body.find("("), body.rfind(")")
    if start < 0 or end <= start:
        return []
    data = json.loads(body[start + 1:end])
    return [s[0] for s in (data[1] if len(data) > 1 else []) if isinstance(s, list) and s]


def autocomplete(term: str, lang: str, timeout: int = 15) -> list[str]:
    """What people type after `term` on YouTube search (one light request, not a page)."""
    url = ("https://suggestqueries-clients6.youtube.com/complete/search?client=youtube&ds=yt&"
           + urllib.parse.urlencode({"hl": lang, "q": term}))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return parse_autocomplete(r.read().decode("utf-8", errors="replace"))


def demand_for(terms: dict[str, list[str]], pause: float = 1.5, cap: int = 30) -> dict:
    """Autocomplete for each concept's seed terms ({concept: ["seed", ...]}), paced and capped."""
    out, n = {}, 0
    for concept, seeds in terms.items():
        out[concept] = {}
        for seed in seeds:
            if n >= cap:
                return out
            lang = "es" if re.search(r"[áéíóúñ]|\b(canci[oó]n|m[uú]sica|ia)\b", seed, re.I) else "en"
            try:
                out[concept][seed] = autocomplete(seed, lang)
            except Exception as e:   # demand is a supporting signal: a failed lookup is recorded, not fatal
                out[concept][seed] = {"error": str(e)[:120]}
            n += 1
            time.sleep(pause)
    return out
