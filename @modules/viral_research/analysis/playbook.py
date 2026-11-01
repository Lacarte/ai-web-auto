"""The playbook's evidence: every formula slot (title, description, tags, upload settings, channel) counted on the
outliers and on the same channels' ordinary videos (controls), from a run's raw metadata and the channels' own upload
lists. Pure functions plus `mine(run_dir, codebook)`; the page is built by analysis/playbook_page.py."""
from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from pathlib import Path

GENRE = re.compile(r"metal|rock|flamenc|rumba|soul|r&b|rnb|blues|doo.?wop|\bska\b|jazz|salsa|bachata|cumbia|reggae|"
                   r"reggaet[oó]n|country|gospel|bolero|disco|house|trap|\brap\b|hip.?hop|\bpop\b|edm|lo-?fi|ballad|balada|"
                   r"orquest|sinf[oó]nic|celt|son cubano|cha cha|motown|synthwave|phonk|drill|dancehall|afro|amapiano|tango|"
                   r"mariachi|ranchera|corrido|viking|nordic|opera|choir|coro", re.I)
ERA = re.compile(r"\b(19[2-9]0'?s|[2-9]0'?s|a[ñn]os [2-9]0|retro|vintage|old school)\b", re.I)
EMOTION = re.compile(r"emotiv|dram[aá]tic|emotional|\bsad\b|triste|💔|heartbreak|desamor|nostalg|rom[aá]ntic|melanc|"
                     r"llorar|\blost\b|perd[ií]|adi[oó]s|coraz[oó]n", re.I)
AI_WORD = re.compile(r"\b(ai|ia|a\.i\.)\b|suno|udio|inteligencia artificial|artificial intelligence", re.I)
COVER = re.compile(r"\bcover\b|versi[oó]n|version|remix|reimagin|reinterpret", re.I)
OFFICIAL = re.compile(r"official|oficial", re.I)
SERIES = re.compile(r"\b(ep\.?|episode|episodio|part|parte|vol\.?|volume|v)\s?\d", re.I)
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐❤]")
DISCLOSURE = re.compile(r"creado con|hecho con|made with|generad[oa] con|ai[- ]generated|generated (with|by) ai|"
                        r"inteligencia artificial|artificial intelligence|\b(ai|ia)\b", re.I)
STYLE_LINE = re.compile(r"style|estilo|g[ée]nero|genre|bpm|instrument|guitar|guitarra|vocals?|voz|prompt|cinematic|"
                        r"rumba|metal|soul|ballad|balada", re.I)
CTA = re.compile(r"suscr[ií]b|subscribe|like|comenta|comment|share|comparte", re.I)
NAME_MUSIC = re.compile(r"\b(ai|ia|a\.i\.)\b|music|m[uú]sica|sound|songs?|canciones|melod|records?|studio|beats?|fm|radio|tunes",
                        re.I)


def title_slots(title: str) -> dict:
    t = title or ""
    bracket = re.search(r"[(\[]([^)\]]{1,40})[)\]]", t)
    return {"starts_artist_dash_song": bool(re.match(r"^[^|()\[\]]{2,45}\s[–—-]\s", t)), "pipe": "|" in t,
            "brackets": bool(re.search(r"[(\[]", t)), "genre_word": bool(GENRE.search(t)), "era_word": bool(ERA.search(t)),
            "emotion_word": bool(EMOTION.search(t)), "ai_word": bool(AI_WORD.search(t)), "cover_word": bool(COVER.search(t)),
            "official_word": bool(OFFICIAL.search(t)), "series_number": bool(SERIES.search(t)), "emoji": bool(EMOJI.search(t)),
            "all_caps_word": bool(re.search(r"\b[A-ZÁÉÍÓÚÑ]{4,}\b", t)), "chars": len(t), "words": len(t.split()),
            "bracket_tag": bracket.group(1).strip().lower() if bracket else None}


def description_slots(desc: str) -> dict:
    d = desc or ""
    lines = [l.strip() for l in d.splitlines() if l.strip()]
    first = lines[0] if lines else ""
    short = [l for l in lines if len(l) <= 45 and not l.startswith("#") and "http" not in l and not l.startswith(("🎵", "👉"))]
    kind = ("hashtags" if first.startswith("#") else "link" if "http" in first else
            "style" if STYLE_LINE.search(first) else "story / hook" if first else "empty")
    return {"chars": len(d), "lines": len(lines), "first_line_kind": kind,
            "first_line_is_style": kind == "style", "first_line_is_story": kind == "story / hook",
            "ai_disclosure": bool(DISCLOSURE.search(d)), "hashtags": len(re.findall(r"#\w+", d)),
            "hashtags_last": bool(lines) and lines[-1].startswith("#"), "links": d.count("http"),
            "lyrics": len(short) >= 8, "subscribe_cta": bool(CTA.search(d)), "style_line": bool(STYLE_LINE.search(d)),
            "timestamps": len(re.findall(r"\b\d{1,2}:\d{2}\b", d)) >= 3}


def tag_slots(tags: list[str] | None, title: str) -> dict:
    tags = [t.lower() for t in tags or []]
    words = set(re.findall(r"\w+", (title or "").lower()))
    return {"count": len(tags), "genre_tag": any(GENRE.search(t) for t in tags), "ai_tag": any(AI_WORD.search(t) for t in tags),
            "era_tag": any(ERA.search(t) for t in tags),
            "title_words_tag": any(set(re.findall(r"\w+", t)) & words - {"ai", "ia", "the", "la", "el", "de"} for t in tags)}


def settings(meta: dict) -> dict:
    return {"category": (meta.get("categories") or ["-"])[0], "declared_language": bool(meta.get("language")),
            "creator_subtitles": bool(meta.get("caption_langs")), "chapters": bool(meta.get("chapters")),
            "comments_on": meta.get("comment_count") is not None, "age_restricted": bool(meta.get("age_limit")),
            "public": meta.get("availability") in (None, "public")}


def channel_name_kind(name: str) -> dict:
    n = name or ""
    return {"genre_or_era_word": bool(GENRE.search(n) or ERA.search(n)), "music_or_ai_word": bool(NAME_MUSIC.search(n)),
            "words": len(n.split())}


def concept_of(text: str, codebook: list[dict]) -> str | None:
    return next((c["name"] for c in codebook if re.search(c["match"], text or "", re.I)), None)


def title_signature(title: str) -> str | None:
    """The part of a title a channel repeats on every upload: what follows the last "|" or sits in the last
    brackets, with numbers and years blanked ("(Flamenco 2026)" -> "flamenco #")."""
    t = title or ""
    m = re.search(r"[(\[]([^)\]]{3,60})[)\]]\s*$", t) or re.search(r"\|\s*([^|]{3,60})$", t)
    return re.sub(r"\d+", "#", m.group(1).strip().lower()) if m else None


def channel_profile(entries: list[dict], now: float, codebook: list[dict]) -> dict:
    """From a channel's own upload list (newest 30-40): rhythm, consistency (one concept / one genre / one title
    signature?), its normal."""
    dated = [e for e in entries if e.get("timestamp")]
    recent = [e for e in dated if now - e["timestamp"] <= 90 * 86400]
    concepts = Counter(concept_of(e.get("title") or "", codebook) for e in entries)
    concepts.pop(None, None)
    genres = Counter(m.group(0).lower() for e in entries for m in [GENRE.search(e.get("title") or "")] if m)
    top_concept, top_n = concepts.most_common(1)[0] if concepts else (None, 0)
    top_genre, genre_n = genres.most_common(1)[0] if genres else (None, 0)
    sigs = Counter(s for e in entries for s in [title_signature(e.get("title"))] if s)
    top_sig, sig_n = sigs.most_common(1)[0] if sigs else (None, 0)
    views = [e["views"] for e in entries if e.get("views")]
    share = lambda k: round(k / len(entries), 2) if entries else None
    return {"uploads_listed": len(entries), "uploads_per_week_90d": round(len(recent) / (90 / 7), 1) if dated else None,
            "main_concept": top_concept, "main_concept_share": share(top_n),
            "main_genre_word": top_genre, "main_genre_share": share(genre_n),
            "title_signature": top_sig, "title_signature_share": share(sig_n),
            "consistency": max(share(genre_n) or 0, share(sig_n) or 0, share(top_n) or 0),
            "median_views": statistics.median(views) if views else None}


def _count(items: list[dict], key: str) -> int:
    return sum(1 for x in items if x.get(key) is True)


def _median(items: list[dict], key: str):
    vals = [x[key] for x in items if isinstance(x.get(key), (int, float)) and not isinstance(x.get(key), bool)]
    return round(statistics.median(vals), 1) if vals else None


def compare(outs: list[dict], ctrls: list[dict]) -> dict:
    """{slot: {"outliers": k, "controls": k, "n": [n_out, n_ctrl]}} for booleans, medians for numbers."""
    keys = {k for x in outs + ctrls for k, v in x.items() if isinstance(v, (bool, int, float))}
    res = {}
    for k in sorted(keys):
        if any(isinstance(x.get(k), bool) for x in outs + ctrls):
            res[k] = {"outliers": _count(outs, k), "controls": _count(ctrls, k), "n": [len(outs), len(ctrls)]}
        else:
            res[k] = {"outliers_median": _median(outs, k), "controls_median": _median(ctrls, k), "n": [len(outs), len(ctrls)]}
    return res


def mine(run_dir: Path, codebook: list[dict], name_words: str | None = None) -> dict:
    """`name_words`: the niche's own vocabulary (its must_match | topic_match) to tell whether a channel's name says
    what it is about — genre words alone miss names like "Zen Samurai Meditations"."""
    load = lambda p: json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    final = load(run_dir / "shortlist.json") or {}
    channels = load(run_dir / "channels.json") or {}
    now = max((e["t"] for e in map(json.loads, (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines())
               if e.get("t")), default=0) if (run_dir / "events.jsonl").exists() else 0
    out = {"rankings": {}, "channels": []}
    seen = set()
    for b, p in sorted(final.items()):
        groups = {}
        for grp in ("outliers", "controls"):
            metas = [m for m in (load(run_dir / "meta" / f"{r['id']}.json") for r in p.get(grp, [])) if m]
            groups[grp] = metas
        sl = lambda f: ([f(m) for m in groups["outliers"]], [f(m) for m in groups["controls"]])
        t_o, t_c = sl(lambda m: title_slots(m.get("title")))
        d_o, d_c = sl(lambda m: description_slots(m.get("description")))
        g_o, g_c = sl(lambda m: tag_slots(m.get("tags"), m.get("title")))
        s_o, s_c = sl(settings)
        out["rankings"][b] = {
            "n": [len(groups["outliers"]), len(groups["controls"])],
            "title": compare(t_o, t_c), "description": compare(d_o, d_c), "tags": compare(g_o, g_c),
            "settings": compare([{k: v for k, v in s.items() if k != "category"} for s in s_o],
                                [{k: v for k, v in s.items() if k != "category"} for s in s_c]),
            "categories": {"outliers": Counter(s["category"] for s in s_o), "controls": Counter(s["category"] for s in s_c)},
            "bracket_tags": Counter(t["bracket_tag"] for t in t_o if t["bracket_tag"]).most_common(8),
            "first_line_kinds": {"outliers": Counter(d["first_line_kind"] for d in d_o),
                                 "controls": Counter(d["first_line_kind"] for d in d_c)},
            "top_tags": Counter(t.lower() for m in groups["outliers"] for t in (m.get("tags") or [])).most_common(15),
        }
        for r, m in zip(p.get("outliers", []), groups["outliers"]):
            ch = m.get("channel_id")
            if ch in seen or m.get("channel") in seen:   # one row per channel, even with several outliers
                continue
            seen.update({ch, m.get("channel")})
            fmt = "shorts" if b.endswith("shorts") else "videos"
            entries = ((channels.get(ch) or {}).get(fmt) or {}).get("entries") or []
            out["channels"].append({"name": m.get("channel"), "ranking": b, "followers": m.get("channel_follower_count"),
                                    "best_multiplier": r.get("multiplier"), "best_title": m.get("title"),
                                    "niche_word_in_name": bool(name_words and re.search(name_words, m.get("channel") or "", re.I)),
                                    **channel_name_kind(m.get("channel")), **channel_profile(entries, now, codebook)})
    return out
