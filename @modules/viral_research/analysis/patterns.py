"""Which features separate outliers from the same channels' ordinary videos, and does that hold up later?

A pattern is a feature that is clearly more (or less) common in outliers than in controls AND appears in at
least 3 outliers across at least 2 channels ("recipes over luck"). The back-test learns patterns on the
older half of the scored videos and checks them on the newer half. Pure functions.
"""
from __future__ import annotations

import statistics

MIN_OUTLIERS = 3
MIN_CHANNELS = 2
MIN_GAP = 0.20          # boolean: rate difference (20 points)
MIN_SIDE = 0.70         # numeric: share of outliers above (or below) the controls' median


def _values(rows, key):
    return [r for r in rows if r["features"].get(key) is not None]


def compare(outliers: list[dict], controls: list[dict]) -> list[dict]:
    """rows: {"id", "channel_id", "features": {...}} -> one comparison per feature, with a pattern flag."""
    keys = sorted({k for r in outliers + controls for k in r["features"]})
    out = []
    for k in keys:
        o, c = _values(outliers, k), _values(controls, k)
        if len(o) < MIN_OUTLIERS or not c:
            continue
        sample = o[0]["features"][k]
        if isinstance(sample, bool):
            has_o = [r for r in o if r["features"][k]]
            lacks_o = [r for r in o if not r["features"][k]]
            rate_o = len(has_o) / len(o)
            rate_c = sum(1 for r in c if r["features"][k]) / len(c)
            gap = rate_o - rate_c
            side = has_o if gap >= 0 else lacks_o
            channels = len({r["channel_id"] for r in side})
            out.append({
                "feature": k, "kind": "bool", "direction": "more" if gap >= 0 else "less",
                "outliers": f"{len(has_o)}/{len(o)}", "rate_outliers": round(rate_o, 2), "rate_controls": round(rate_c, 2),
                "gap": round(gap, 2), "support": len(side), "channels": channels,
                "pattern": abs(gap) >= MIN_GAP and len(side) >= MIN_OUTLIERS and channels >= MIN_CHANNELS,
                "examples": [r["id"] for r in side[:5]],
            })
        elif isinstance(sample, (int, float)):
            med_c = statistics.median(r["features"][k] for r in c)
            above = [r for r in o if r["features"][k] > med_c]
            below = [r for r in o if r["features"][k] < med_c]
            share = len(above) / len(o)
            side = above if share >= 0.5 else below
            channels = len({r["channel_id"] for r in side})
            out.append({
                "feature": k, "kind": "number", "direction": "higher" if share >= 0.5 else "lower",
                "median_outliers": round(statistics.median(r["features"][k] for r in o), 2), "median_controls": round(med_c, 2),
                "share_on_side": round(max(share, len(below) / len(o)), 2), "support": len(side), "channels": channels,
                "pattern": max(share, len(below) / len(o)) >= MIN_SIDE and len(side) >= MIN_OUTLIERS and channels >= MIN_CHANNELS,
                "examples": [r["id"] for r in side[:5]],
            })
    out.sort(key=lambda p: (not p["pattern"], -(abs(p.get("gap", 0)) or p.get("share_on_side", 0))))
    return out


def matches(row: dict, pattern: dict) -> bool | None:
    v = row["features"].get(pattern["feature"])
    if v is None:
        return None
    if pattern["kind"] == "bool":
        return bool(v) == (pattern["direction"] == "more")
    return v > pattern["median_controls"] if pattern["direction"] == "higher" else v < pattern["median_controls"]


def backtest(rows: list[dict], outlier_at: float = 3.0, ordinary_below: float = 1.5) -> dict:
    """rows: scored videos {"id","channel_id","timestamp","multiplier","features"}. Learn on the older half,
    test on the newer half: do videos matching more learned patterns out-perform the rest?"""
    dated = sorted((r for r in rows if r.get("timestamp") and r.get("multiplier") is not None), key=lambda r: r["timestamp"])
    if len(dated) < 30:
        return {"ok": False, "note": f"only {len(dated)} dated, scored videos - too few for a back-test"}
    half = len(dated) // 2
    older, newer = dated[:half], dated[half:]
    learned = [p for p in compare([r for r in older if r["multiplier"] >= outlier_at],
                                  [r for r in older if r["multiplier"] < ordinary_below]) if p["pattern"]]
    if not learned:
        return {"ok": False, "note": "no pattern found in the older half", "older": len(older), "newer": len(newer)}

    def score(r):
        return sum(1 for p in learned if matches(r, p))

    scores = [score(r) for r in newer]
    cut = statistics.median(scores)
    high = [r for r, s in zip(newer, scores) if s > cut]
    low = [r for r, s in zip(newer, scores) if s <= cut]
    if not high or not low:
        return {"ok": False, "note": "newer videos all match the same number of patterns", "learned": [p["feature"] for p in learned]}
    summary = lambda grp: {"videos": len(grp), "median_multiplier": round(statistics.median(r["multiplier"] for r in grp), 2),
                           "outlier_rate": round(sum(r["multiplier"] >= outlier_at for r in grp) / len(grp), 2)}
    hi, lo = summary(high), summary(low)
    return {"ok": True, "cutoff_timestamp": newer[0]["timestamp"], "learned": [p["feature"] + ":" + p["direction"] for p in learned],
            "matching_more": hi, "matching_fewer": lo,
            "holds": hi["median_multiplier"] > lo["median_multiplier"] and hi["outlier_rate"] >= lo["outlier_rate"]}
