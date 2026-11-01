"""Pre-launch research: the numbers behind a go / pilot / stop decision for a new music channel.

Pure functions (no network, no files). The run stages in `prelaunch.py` feed them raw snapshots and write the
outputs. What this adds to the outlier pipeline, and why:
  * every upload of a sampled channel counts, winners and failures alike (search alone surfaces survivors);
  * age is kept next to every number (lifetime views of a 5-year-old video are not comparable to a 2-week-old one);
  * a channel-relative score is refused when the channel's own baseline is too small to divide by;
  * medians come with a bootstrap interval and a sample size, and every claim carries an evidence status;
  * every owner asset gets a rights status, and the strictest problem decides it.
"""
from __future__ import annotations

import random
import re
import statistics

# ── classification ───────────────────────────────────────────────────────────

NICHE = re.compile(r"samurai|ronin|bushido|shogun|katana|yasuke|ninja|japan|japanese|edo\b|kyoto|shakuhachi|koto\b|"
                   r"shamisen|taiko|sakura|geisha|\bzen\b|dojo|biwa|tokyo|musashi|wabi|ikigai|禅|侍", re.I)
MUSIC = re.compile(r"music|ambient|ambience|meditat|relax|sleep|study|focus|lo-?fi|beats|instrumental|soundtrack|\bost\b|"
                   r"healing|calm|chill|rain|piano|flute|drums?|taiko|chant|\bmix\b|playlist|hours?\b|bgm|hip.?hop|trap|"
                   r"phonk|shakuhachi|koto\b|♪|🎵|🎶|🎧", re.I)
OFF_TOPIC = re.compile(r"\bjennie\b|blackpink|gameplay|walkthrough|trailer|reaction|review|unboxing|let'?s play", re.I)
GUIDED = re.compile(r"guided|breath(e|ing|work)|body scan|affirmation|pranayama|meditation session", re.I)
EPIC = re.compile(r"taiko|war ?drums?|drums? of|epic|battle|warrior|\btrap\b|phonk|drill|\bbass\b|motivat|workout|"
                  r"training|gym|cinematic|\bpower\b|rage|fight", re.I)
LOFI = re.compile(r"lo-?fi|hip.?hop|chillhop|boom.?bap|\bbeats?\b|jazz.?hop|chill ?beats", re.I)
SLEEP = re.compile(r"sleep|insomnia|睡眠", re.I)
MEDITATION = re.compile(r"meditat|mindful|contemplat|瞑想", re.I)
AMBIENT = re.compile(r"ambient|ambience|\bzen\b|relax|healing|calm|shakuhachi|koto\b|peace|seren|tranquil|sooth|rain|flute|"
                     r"stress|anxiety|bamboo|temple|stillness|quiet|inner|study|focus|禅", re.I)
LABELS = {"lofi": "A. Japanese / samurai lo-fi focus", "zen_sleep": "B. Japanese zen / sleep ambient",
          "mindfulness": "C. Samurai mindfulness", "epic": "D. Epic samurai instrumentals"}
DIRECTIONS = tuple(LABELS)


def on_niche(title: str) -> bool:
    """Japanese-themed music, not a pop song, game video or trailer that happens to share a word."""
    t = title or ""
    return bool(NICHE.search(t) and MUSIC.search(t) and not OFF_TOPIC.search(t))


def direction(title: str) -> str:
    """Guided/breathing sessions are mindfulness whatever else they say; then rhythm decides (epic, lo-fi); then the use:
    sleep -> zen/sleep ambient, meditation -> mindfulness; any other calm Japanese ambient -> zen/sleep ambient."""
    t = title or ""
    for name, rx in (("mindfulness", GUIDED), ("epic", EPIC), ("lofi", LOFI), ("zen_sleep", SLEEP), ("mindfulness", MEDITATION),
                     ("zen_sleep", AMBIENT)):
        if rx.search(t):
            return name
    return "other"


# ── snapshots: dedupe and age ────────────────────────────────────────────────

def dedupe(rows: list[dict]) -> list[dict]:
    """One row per video ID: the most recent snapshot wins (ties: the larger view count, then input order).
    Output is sorted by video ID so the same input always gives the same file."""
    best: dict[str, dict] = {}
    for r in rows:
        vid = r.get("id")
        if not vid:
            continue
        cur = best.get(vid)
        key = (r.get("fetched_at") or 0, r.get("views") or -1)
        if cur is None or key > (cur.get("fetched_at") or 0, cur.get("views") or -1):
            best[vid] = r
    return [best[k] for k in sorted(best)]


def age_days(timestamp: float | None, at: float | None) -> float | None:
    if not timestamp or not at:
        return None
    return round(max(0.0, (at - timestamp) / 86400), 1)


def age_band(days: float | None) -> str:
    if days is None:
        return "unknown"
    for hi, name in ((7, "0-7d"), (30, "8-30d"), (90, "31-90d"), (365, "91-365d")):
        if days <= hi:
            return name
    return ">365d"


def views_precision(views: int | None) -> str:
    """Channel listings show 2-3 significant figures above 1,000 ("1.2K", "312K", "1.2M"); exact below."""
    if views is None:
        return "missing"
    return "exact" if views < 1000 else "rounded"


# ── channels ─────────────────────────────────────────────────────────────────

SIZE_BANDS = ((1_000, "<1K"), (10_000, "1K-10K"), (100_000, "10K-100K"), (float("inf"), ">=100K"))
MIN_BASELINE_N = 5
MIN_BASELINE_VIEWS = 100   # dividing by a median under 100 views turns noise into "50x" headlines


def size_band(subs: int | None) -> str:
    if subs is None:
        return "unknown"
    return next(name for hi, name in SIZE_BANDS if subs < hi)


def channel_age_band(first_upload_age: float | None, history_complete: bool) -> str:
    """New = the whole upload history is visible and starts within a year. Otherwise only a lower bound is known."""
    if first_upload_age is None:
        return "unknown"
    if history_complete:   # listing dates are approximate ("1 year ago" covers 12-23 months): 335 days keeps "new" honest
        return "new (<12 months)" if first_upload_age <= 335 else "established"
    return "established" if first_upload_age > 365 else "unknown (history truncated)"


def baseline(entries: list[dict], at: float, window: tuple[float, float] = (14, 365)) -> dict:
    """A channel's normal: median views of its long uploads aged within `window` days. `usable` is False when there
    are fewer than MIN_BASELINE_N such uploads or the median is under MIN_BASELINE_VIEWS."""
    v = sorted(e["views"] for e in entries if e.get("views") is not None and (e.get("duration") or 0) > 180
               and e.get("timestamp") and window[0] <= (age_days(e["timestamp"], at) or -1) <= window[1])
    if not v:
        return {"n": 0, "median": None, "p25": None, "p75": None, "usable": False, "reason": "no uploads in window"}
    med = statistics.median(v)
    reason = "" if len(v) >= MIN_BASELINE_N and med >= MIN_BASELINE_VIEWS else (
        f"only {len(v)} uploads" if len(v) < MIN_BASELINE_N else f"median {med:g} < {MIN_BASELINE_VIEWS}")
    return {"n": len(v), "median": med, "p25": quantile(v, .25), "p75": quantile(v, .75), "usable": not reason, "reason": reason}


def relative(views: int | None, base: dict) -> float | None:
    """views / the channel's own median - only when that median is a usable denominator."""
    if views is None or not base.get("usable"):
        return None
    return round(views / base["median"], 2)


# ── statistics ───────────────────────────────────────────────────────────────

def quantile(values: list[float], p: float) -> float | None:
    v = sorted(values)
    if not v:
        return None
    k = (len(v) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 2)


def bootstrap_median_ci(values: list[float], iters: int = 2000, level: float = 0.9, seed: int = 7) -> tuple | None:
    """Percentile bootstrap interval for the median; seeded, so the same data always gives the same interval."""
    if len(values) < 3:
        return None
    rng = random.Random(seed)
    meds = sorted(statistics.median(rng.choices(values, k=len(values))) for _ in range(iters))
    a = (1 - level) / 2
    return (meds[int(a * (iters - 1))], meds[int((1 - a) * (iters - 1))])


def summarize(values: list[float]) -> dict:
    v = [x for x in values if x is not None]
    if not v:
        return {"n": 0}
    ci = bootstrap_median_ci(v)
    return {"n": len(v), "median": statistics.median(v), "p25": quantile(v, .25), "p75": quantile(v, .75),
            "p90": quantile(v, .9), "ci90": list(ci) if ci else None,
            "share_under_1k": round(sum(x < 1000 for x in v) / len(v), 3),
            "share_10k_plus": round(sum(x >= 10_000 for x in v) / len(v), 3),
            "small_sample": len(v) < 10}


def without_top(values: list[float], k: int = 1) -> list[float]:
    """Sensitivity check: the same values minus the k largest."""
    return sorted(values)[:-k] if len(values) > k else []


# ── evidence ledger ──────────────────────────────────────────────────────────

STATUSES = ("MEASURED", "INFERRED", "UNTESTED", "UNAVAILABLE")
CONFIDENCE = ("HIGH", "MEDIUM", "LOW")


def check_claim(c: dict) -> list[str]:
    """Problems with one ledger entry: a MEASURED claim needs a source and a collection date; UNTESTED and
    UNAVAILABLE claims can't be HIGH confidence; every claim states its limitation."""
    p = []
    if c.get("status") not in STATUSES:
        p.append(f"status must be one of {STATUSES}")
    if c.get("confidence") not in CONFIDENCE:
        p.append(f"confidence must be one of {CONFIDENCE}")
    if c.get("status") == "MEASURED" and not (c.get("source") and c.get("date")):
        p.append("a MEASURED claim needs a source and a date")
    if c.get("status") in ("UNTESTED", "UNAVAILABLE") and c.get("confidence") == "HIGH":
        p.append(f"{c.get('status')} can't be HIGH confidence")
    if not c.get("limitations"):
        p.append("state the limitation")
    return p


# ── rights matrix ────────────────────────────────────────────────────────────

RIGHTS = ("VERIFIED FOR INTENDED USE", "CONDITIONALLY PERMITTED", "NEEDS EVIDENCE", "NEEDS WRITTEN CLARIFICATION", "BLOCKED")
EVIDENCE_NEEDED = (("terms_verified", "provider terms not verified for this use"),
                   ("plan_known", "subscription tier at creation not documented"),
                   ("provenance_complete", "creation record incomplete"))


def rights_status(a: dict) -> tuple[str, list[str]]:
    """The strictest problem decides. Keys (all optional, default False/None):
    blocked_reasons [str]   - a known conflict (e.g. a modern author's words presented as samurai wisdom)
    terms_verified bool     - the provider's current terms were read and allow the intended use
    plan_known bool         - the subscription tier at creation is documented
    provenance_complete bool- file hash + creation record (date, tool, prompt or source) exist
    clarify [str]           - questions only the vendor or a lawyer can answer
    conditions [str]        - duties that come with the permission (disclosure, attribution, no music library)"""
    if a.get("blocked_reasons"):
        return "BLOCKED", list(a["blocked_reasons"])
    needs = [label for key, label in EVIDENCE_NEEDED if not a.get(key)]
    if needs:
        return "NEEDS EVIDENCE", needs
    if a.get("clarify"):
        return "NEEDS WRITTEN CLARIFICATION", list(a["clarify"])
    if a.get("conditions"):
        return "CONDITIONALLY PERMITTED", list(a["conditions"])
    return "VERIFIED FOR INTENDED USE", []


def publishable(status: str) -> bool:
    return status in ("VERIFIED FOR INTENDED USE", "CONDITIONALLY PERMITTED")
