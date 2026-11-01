"""The numbers that decide what counts as viral, and the features compared between outliers and controls.

Pure functions (no network, no files): channel baselines, outlier scores, format and language checks,
title features, caption parsing. Thresholds follow PLAN.md.
"""
from __future__ import annotations

import re
import statistics
from datetime import datetime, timezone

OUTLIER = 3.0          # views >= 3x the channel's median
BORDERLINE = 1.5
CONTROL_RANGE = (0.5, 1.5)
WINDOW_DAYS = (7, 180)  # long-form baseline: videos old enough to have settled, recent enough to be relevant
MIN_BASELINE = 10
SHORTS_SKIP_NEWEST = 3  # the Shorts tab has no dates: skip the newest (still growing), take the next 30
SHORTS_TAKE = 30
SHORT_MAX_SECONDS = 180


# ── baselines and scores ──────────────────────────────────────────────────────

def age_days(timestamp: float | None, now: float) -> float | None:
    return None if not timestamp else (now - timestamp) / 86400


def baseline_long(entries: list[dict], now: float) -> dict:
    """Flat /videos entries (approximate timestamps) -> median views of videos 7-180 days old."""
    views = [e["view_count"] for e in entries
             if e.get("view_count") is not None and e.get("timestamp")
             and WINDOW_DAYS[0] <= age_days(e["timestamp"], now) <= WINDOW_DAYS[1]]
    return _baseline(views, f"{WINDOW_DAYS[0]}-{WINDOW_DAYS[1]} days")


def baseline_shorts(entries: list[dict]) -> dict:
    """Flat /shorts entries (newest first, no dates) -> median of the 30 after the 3 newest."""
    views = [e["view_count"] for e in entries[SHORTS_SKIP_NEWEST:SHORTS_SKIP_NEWEST + SHORTS_TAKE]
             if e.get("view_count") is not None]
    return _baseline(views, f"Shorts {SHORTS_SKIP_NEWEST + 1}-{SHORTS_SKIP_NEWEST + SHORTS_TAKE} most recent")


def _baseline(views: list[int], window: str) -> dict:
    if len(views) < MIN_BASELINE:
        return {"median": None, "n": len(views), "window": window, "note": "sample too small"}
    return {"median": statistics.median(views), "n": len(views), "window": window, "note": ""}


def multiplier(views: int | None, median: float | None) -> float | None:
    if views is None or not median:
        return None
    return round(views / median, 2)


def tier(mult: float | None) -> str:
    if mult is None:
        return "unscored"
    return "outlier" if mult >= OUTLIER else "borderline" if mult >= BORDERLINE else "normal"


def views_per_day(views: int | None, timestamp: float | None, now: float) -> float | None:
    age = age_days(timestamp, now)
    if views is None or age is None:
        return None
    return round(views / max(age, 1.0), 1)


# ── format, language, sponsorship ─────────────────────────────────────────────

def is_short(meta: dict) -> bool | None:
    """A Short is <= 180 s and vertical. None when the dimensions are unknown."""
    duration = meta.get("duration")
    if duration is None:
        return None
    if duration > SHORT_MAX_SECONDS:
        return False
    w, h = meta.get("width"), meta.get("height")
    if w and h:
        return h > w
    return "/shorts/" in (meta.get("webpage_url") or meta.get("url") or "") or None


_ES = {"el", "la", "los", "las", "de", "del", "que", "y", "en", "un", "una", "con", "por", "para", "es", "mi",
       "canción", "cancion", "música", "musica", "hecha", "inteligencia", "artificial", "cómo", "como", "nueva"}
_EN = {"the", "a", "an", "of", "and", "to", "in", "is", "my", "with", "for", "this", "how", "you", "song",
       "music", "made", "new", "your", "on", "it", "what", "i"}


def spoken_language(meta: dict | None) -> str | None:
    """The video's declared audio language ('en-US' -> 'en'), the reliable signal when present."""
    lang = (meta or {}).get("language")
    return lang.split("-")[0].lower() if lang else None


def language_guess(text: str) -> str | None:
    """'es' / 'en' from common words, 'other' for mostly non-Latin script; None when unclear."""
    letters = [c for c in (text or "") if c.isalpha()]
    if letters and sum(1 for c in letters if ord(c) > 0x024F) / len(letters) > 0.3:
        return "other"
    words = re.findall(r"[a-záéíóúñü]+", (text or "").lower())
    es = sum(w in _ES for w in words)
    en = sum(w in _EN for w in words)
    if es + en < 2 or es == en:
        return None
    return "es" if es > en else "en"


def relevant(text: str, must_match: str | None) -> bool:
    """The niche's relevance gate (a regex from niches/<niche>.json) on title + description + tags."""
    return not must_match or bool(re.search(must_match, text or "", re.I))


def on_topic(meta: dict, cfg: dict) -> bool:
    """The full niche check once metadata is known: the AI signal (must_match, which may come from the channel
    name) AND the topic itself - YouTube's own category (e.g. "Music") or topic words in the title,
    description or tags (never just the channel name: an "IA para crear música" channel also posts football)."""
    own = " ".join([meta.get("title") or "", (meta.get("description") or "")[:500], " ".join(meta.get("tags") or [])])
    if not relevant(own + " " + (meta.get("channel") or ""), cfg.get("must_match")):
        return False
    if not cfg.get("topic_match") and not cfg.get("categories"):
        return True
    return bool(set(meta.get("categories") or []) & set(cfg.get("categories") or [])) or relevant(own, cfg.get("topic_match"))


def likely_paid_views(views: int | None, likes: int | None) -> bool:
    """Ad-driven views barely get likes: organic videos sit around 20-50 likes per 1,000 views; under 2 on a
    100K+ video means the views were most likely bought (e.g. a brand channel's promoted video)."""
    return bool(views and views >= 100_000 and likes is not None and likes / views < 0.002)


SPONSORED = re.compile(r"(#ad\b|#sponsored|\bsponsored by\b|\bpaid partnership\b|\bpatrocinad[oa] por\b|#publicidad\b|#patrocinado\b)", re.I)


def is_sponsored(text: str) -> bool:
    return bool(SPONSORED.search(text or ""))


# ── title (packaging) features ────────────────────────────────────────────────

EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐❤]")
FIRST_PERSON = re.compile(r"\b(i|i'm|i've|my|me|we|our|yo|mi|mis|me|hice|nosotros)\b", re.I)
AI_TOOLS = re.compile(r"\b(suno|udio|ai|a\.i\.|ia|chatgpt|elevenlabs|riffusion|mubert)\b", re.I)


def title_features(title: str) -> dict:
    t = title or ""
    words = re.findall(r"\S+", t)
    caps = [w for w in words if re.fullmatch(r"[A-ZÁÉÍÓÚÑ]{2,}[!?.,:]*", w)]
    return {
        "title_words": len(words),
        "title_chars": len(t),
        "title_number": bool(re.search(r"\d", t)),
        "title_question": "?" in t,
        "title_exclaim": "!" in t,
        "title_caps_word": len(caps) > 0,
        "title_emoji": bool(EMOJI.search(t)),
        "title_brackets": bool(re.search(r"[\(\[\|]", t)),
        "title_hashtag": "#" in t,
        "title_first_person": bool(FIRST_PERSON.search(t)),
        "title_names_ai_tool": bool(AI_TOOLS.search(t)),
    }


def post_time(timestamp: float | None) -> dict:
    if not timestamp:
        return {"post_weekday": None, "post_hour_utc": None}
    d = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return {"post_weekday": d.strftime("%a"), "post_hour_utc": d.hour}


# ── captions (YouTube json3) ──────────────────────────────────────────────────

def parse_json3(doc: dict) -> list[dict]:
    """Words with start times from YouTube's json3 captions (auto captions have per-word offsets)."""
    words = []
    for ev in doc.get("events") or []:
        start = ev.get("tStartMs", 0)
        for seg in ev.get("segs") or []:
            text = (seg.get("utf8") or "").strip()
            if not text or text == "\n":
                continue
            for i, w in enumerate(text.split()):
                words.append({"t": round((start + seg.get("tOffsetMs", 0)) / 1000, 2) + i * 0.01, "w": w})
    words.sort(key=lambda x: x["t"])
    return words


MUSIC_TAGS = re.compile(r"^\[?(music|música|musica|applause|aplausos|♪+|\[música\])\]?$", re.I)


def speech_stats(words: list[dict], duration: float | None) -> dict:
    spoken = [w for w in words if not MUSIC_TAGS.match(w["w"].strip("[]")) and "♪" not in w["w"]]
    first3 = [w["w"] for w in spoken if w["t"] < 3.0]
    first15 = [w for w in spoken if w["t"] < 15.0]
    minutes = (duration or (spoken[-1]["t"] if spoken else 0)) / 60
    return {
        "caption_words": len(spoken),
        "music_only": len(spoken) < 5,
        "words_first_3s": len(first3),
        "words_first_15s": len(first15),
        "wpm": round(len(spoken) / minutes, 1) if minutes > 0 else None,
        "hook_text": " ".join(w["w"] for w in spoken if w["t"] < 6.0)[:300],
        "first_words_at": spoken[0]["t"] if spoken else None,
    }
