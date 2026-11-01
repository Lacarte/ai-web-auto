"""pack.md: everything the report is written from, with every number traceable to a saved file."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


def _j(p: Path, default=None):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def _n(x) -> str:
    if x is None:
        return "-"
    return f"{x:,.0f}" if isinstance(x, (int, float)) and abs(x) >= 100 else str(x)


def _when(ts) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if ts else "-"


DECLARED = re.compile(r"prompt|style|genre|made with|created with|creado con|hecho con|suno|udio|elevenlabs|bpm|vocals?\b|"
                      r"instrument|midjourney|kling|runway|veo\b|sora\b|ai[- ]generated|generad[oa] con|reimagin|reinterpret", re.I)


def declared(description: str, limit: int = 3) -> list[str]:
    """Lines where a creator states the style, prompt or tools behind a video ("Style: piano ballad", "Created
    with Suno") - the raw material for prompt templates. Quoted for analysis only."""
    lines = [re.sub(r"\s+", " ", l).strip() for l in (description or "").splitlines()]
    return [l[:200] for l in lines if l and DECLARED.search(l)][:limit]


def on_niche(r: dict, cfg: dict) -> bool:
    """A scored video is on-niche by its own title and description, in its ranking's language (flat listings carry
    no categories, so this is stricter than metrics.on_topic)."""
    from analysis import metrics as M
    text = f"{r.get('title') or ''} {r.get('description') or ''}"
    return (M.relevant(text, cfg.get("must_match")) and M.relevant(text, cfg.get("topic_match"))
            and M.language_guess(text) in (None, r["bucket"].split("-")[0])
            and M.language_guess(re.sub(r"#\S+", "", r.get("title") or "")) != "other")   # Latin hashtags hide the script


def wider_outliers(rows: list[dict], bucket: str, exclude: set, cfg: dict, max_age_days: int, n: int = 40) -> list[dict]:
    """More outliers of a ranking for grouping concepts and ideas: >= 3x, not shortlisted, on-niche by their own
    title and description, in the ranking's language, recent. Not verified with full details (paid views,
    sponsorship and exact format unchecked), so never a source for the brief's numbers."""
    out = []
    for r in sorted(rows, key=lambda r: -(r.get("multiplier") or 0)):
        if (r["bucket"] != bucket or r.get("tier") != "outlier" or r["id"] in exclude
                or (r.get("age_days") is not None and r["age_days"] > max_age_days) or not on_niche(r, cfg)):
            continue
        out.append(r)
        if len(out) >= n:
            break
    return out


def _vision(p: Path) -> str:
    v = _j(p)
    if not v:
        return "-"
    return (f"text {v.get('text')!r} · face {v.get('face')} · style {v.get('style')} · {v.get('subject')!r}")


def write_pack(run, partial: str | None = None) -> Path:
    """`partial`: why media could not be measured; the pack then says that only packaging was compared."""
    d = run.dir
    scored = _j(d / "scored.json", {"rows": [], "dropped": {}})
    final = _j(d / "shortlist.json", {})
    analysis = _j(d / "analysis.json", {})
    spot = _j(d / "spot_check.json", {"results": []})
    channels = _j(d / "channels.json", {})
    module_dir = Path(__file__).resolve().parents[1]
    niche = _j(module_dir / "niches" / f"{run.niche}.json", {})
    max_age_days = (_j(module_dir / "module.json", {}).get("settings") or {}).get("max_age_days", 365)
    lines = [f"# Evidence pack - {run.niche}", "",
             f"Run folder: `{d}`  ", f"Built: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC  ",
             f"Videos found: {sum(len(v) for v in _j(d / 'discovery.json', {}).values())}; channels read: {len(channels)}; "
             f"scored: {len(scored['rows'])}; dropped: {scored['dropped']}", "",
             "Multiplier = views / the channel's median views for the same format (long-form: its videos 7-180 days old; "
             "Shorts: its 30 most recent after the 3 newest). Outlier >= 3x, borderline 1.5-3x.", ""]
    if partial:
        measured = sum(1 for p in final.values() for grp in ("outliers", "controls") for r in p[grp]
                       if (d / "evidence" / r["id"] / "measures.json").exists())
        lines[1:1] = ["", f"> **PARTIAL - {partial}.** Only {measured} videos have media measurements (pacing, loudness, "
                      "opening words, frames). Titles, metadata, thumbnails and the local vision labels are compared; "
                      "conclusions about the video itself wait for a complete run.", ""]

    for b in sorted(final):
        p = final[b]
        rows = [r for r in scored["rows"] if r["bucket"] == b]
        lines += [f"## {b}", "",
                  f"Scored {len(rows)}: {sum(r['tier'] == 'outlier' for r in rows)} outliers, "
                  f"{sum(r['tier'] == 'borderline' for r in rows)} borderline. Shortlisted {len(p['outliers'])} outliers, "
                  f"{len(p['controls'])} controls (same channels, ordinary videos).", "",
                  "| # | x | views | channel median | views/day | age d | dur s | subs | title | channel | link |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for i, r in enumerate(p["outliers"], 1):
            lines.append(f"| {i} | {r['multiplier']} | {_n(r['views'])} | {_n(r['channel_median'])} | {_n(r['views_per_day'])} | "
                         f"{r['age_days']} | {r['duration']} | {_n(r.get('subscribers'))} | {(r['title'] or '').replace('|', '/')[:80]} | "
                         f"{(r['channel'] or '')[:30]} | {r['url']} |")
        lines += ["", "Controls (ordinary videos of the same channels):", ""]
        for r in p["controls"]:
            lines.append(f"- {r['multiplier']}x · {_n(r['views'])} views · {r['duration']} s · {(r['title'] or '')[:90]} · {r['url']}")
        if p.get("excluded"):
            lines += ["", "Excluded from the outliers (high multiplier, failed a check):", ""]
            for x in p["excluded"]:
                lines.append(f"- {(x.get('title') or x['id'])[:80]} — {x['reason']} — https://www.youtube.com/watch?v={x['id']}")

        a = analysis.get(b, {})
        lines += ["", "### Outliers vs controls", "",
                  "| pattern | feature | direction | outliers | controls | support | channels |", "|---|---|---|---|---|---|---|"]
        for c in a.get("comparison", [])[:40]:
            if c["kind"] == "bool":
                o, k = f"{c['outliers']} ({c['rate_outliers']:.0%})", f"{c['rate_controls']:.0%}"
            else:
                o, k = f"median {c['median_outliers']} ({c['share_on_side']:.0%} {c['direction']})", f"median {c['median_controls']}"
            lines.append(f"| {'YES' if c['pattern'] else ''} | {c['feature']} | {c['direction']} | {o} | {k} | {c['support']} | {c['channels']} |")
        bt = a.get("backtest", {})
        lines += ["", f"Back-test: `{json.dumps(bt, ensure_ascii=False)}`", ""]

        lines += ["### Outlier evidence", ""]
        for i, r in enumerate(p["outliers"], 1):
            m = _j(d / "meta" / f"{r['id']}.json", {})
            e = d / "evidence" / r["id"]
            meas = _j(e / "measures.json", {})
            feats = a.get("features", {}).get(r["id"], {})
            hook = None
            from platforms.youtube import pick_captions
            cap = pick_captions(e, m.get("caption_langs") or [])
            caps = [cap] if cap else []
            if caps or (e / "transcript.json").exists():
                from analysis import metrics as M
                words = M.parse_json3(_j(caps[0])) if caps else _j(e / "transcript.json")["words"]
                stats = M.speech_stats(words, m.get("duration") if caps else 30)
                hook = f"{stats['hook_text']!r} (first words at {stats['first_words_at']} s; {'captions' if caps else 'whisper'}; music-only: {stats['music_only']})"
            from analysis import demand as Dm
            asks = Dm.viewer_requests(_j(e / "comments.json", []))
            peak = Dm.replay_peak(m.get("heatmap"), m.get("duration"))
            comments = _j(e / "comments.json", [])[:6]
            lines += [f"#### {i}. {m.get('title')}", "",
                      f"- {r['url']} · {r['multiplier']}x · {_n(r['views'])} views · posted {_when(m.get('timestamp'))} · "
                      f"{m.get('duration')} s · {_n(m.get('like_count'))} likes · {_n(m.get('comment_count'))} comments · "
                      f"channel {m.get('channel')} ({_n(m.get('channel_follower_count'))} subs)",
                      f"- tags: {', '.join((m.get('tags') or [])[:12]) or '-'}",
                      f"- description: {(m.get('description') or '')[:400]!r}",
                      f"- creator-declared style / prompt / tools: {' // '.join(declared(m.get('description'))) or '-'}",
                      f"- opening words: {hook or 'no captions / transcript'}",
                      f"- measured: {meas.get('cuts_per_min')} cuts/min · first cut at {meas.get('first_cut_at')} s · "
                      f"starts loud: {meas.get('starts_loud')} · {meas.get('lufs')} LUFS · silence {meas.get('silence_share')} · "
                      f"{meas.get('width')}x{meas.get('height')} · measured {meas.get('measured_seconds')} s",
                      f"- chapters: {len(m.get('chapters') or [])} · heatmap points: {len(m.get('heatmap') or [])}",
                      f"- local vision (MiniCPM-V): thumbnail {_vision(e / 'vision_thumbnail.json')} · frame at 1 s {_vision(e / 'vision_opening.json')}",
                      f"- images: thumbnail `{e / 'thumbnail.jpg'}` · contact sheet `{meas.get('contact_sheet')}` · "
                      f"opening frames {', '.join('`' + f + '`' for f in meas.get('frames', []))}",
                      f"- features: {json.dumps(feats, ensure_ascii=False)}"]
            where = f"at {peak['at_s']} s ({peak['at_pct']}% in)" if peak else "no heatmap"
            lines.append(f"- most replayed (after the opening): {where}")
            if asks:
                lines.append("- viewers asking for: " + " | ".join(f"({x['likes']}) {x['text'][:140]!r}" for x in asks))
            if comments:
                lines.append("- top comments: " + " | ".join(f"({c.get('likes')}) {(c.get('text') or '')[:140]!r}" for c in comments))
            lines.append("")
        lines += ["### Control evidence (titles, openings)", ""]
        for r in p["controls"]:
            e = d / "evidence" / r["id"]
            meas = _j(e / "measures.json", {})
            lines.append(f"- {r['multiplier']}x · {(r['title'] or '')[:90]} · {meas.get('cuts_per_min')} cuts/min · "
                         f"first cut {meas.get('first_cut_at')} s · starts loud {meas.get('starts_loud')} · thumbnail `{e / 'thumbnail.jpg'}` · "
                         f"vision {_vision(e / 'vision_thumbnail.json')}")
        shortlisted = {r["id"] for grp in ("outliers", "controls") for r in p[grp]} | {x["id"] for x in p.get("excluded", [])}
        more = wider_outliers(scored["rows"], b, shortlisted, niche.get(b.split("-")[0], {}), max_age_days)
        lines += ["", f"### Wider outlier list ({len(more)}) - titles only, NOT verified", "",
                  "On-niche by their own title and description, >= 3x, not shortlisted. Paid views, sponsorship and exact "
                  "format are unchecked: use them to group concepts and inspire ideas, never for the brief's numbers.", ""]
        for r in more:
            lines.append(f"- {r['multiplier']}x · {_n(r['views'])} views · {(r['title'] or '').replace('|', '/')[:90]} · "
                         f"{(r.get('channel') or '')[:30]} · https://www.youtube.com/watch?v={r['id']}")
        lines.append("")

    lines += ["## Spot-check (watch page vs yt-dlp)", ""]
    for x in spot.get("results", []):
        lines.append(f"- {x.get('id')}: yt-dlp {_n(x.get('yt_dlp_views'))} · watch page {_n(x.get('watch_page_views'))} · drift {x.get('drift')} {x.get('error', '')}")
    out = d / "pack.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
