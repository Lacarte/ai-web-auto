"""YouTube submodule: discovery, channel baselines, metadata, captions, comments, thumbnails, media.

Everything goes through yt-dlp (free, no API key) with Node as its JavaScript runtime; the view-count
spot-check reads the watch page HTML directly (a separate code path from yt-dlp's parser).
Other platforms (TikTok, Instagram, Facebook) get their own module with the same functions.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote

NAME = "youtube"
YTDLP = str(Path(sys.executable).with_name("yt-dlp.exe" if sys.platform == "win32" else "yt-dlp"))
BASE = ["--js-runtimes", "node", "--no-warnings", "--ignore-config", "--no-update"]
UTF8 = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}   # else "you’re" arrives as "you�re"
BOT_CHECK = re.compile(r"not a bot|sign in to confirm|HTTP Error 429|too many requests", re.I)
SP_VIDEOS = "EgIQAQ%253D%253D"         # search filter: type = video
SP_UNDER_4_MIN = "EgIYAQ%253D%253D"    # search filter: duration < 4 min (Shorts are confirmed by aspect later)
KEEP = ("id", "title", "description", "channel", "channel_id", "channel_url", "channel_follower_count", "uploader",
        "view_count", "like_count", "comment_count", "timestamp", "upload_date", "duration", "width", "height",
        "aspect_ratio", "tags", "categories", "chapters", "heatmap", "language", "live_status", "availability",
        "webpage_url", "thumbnail", "age_limit", "was_live")


class YtDlpError(RuntimeError):
    pass


class Blocked(RuntimeError):
    """YouTube kept answering with bot checks for too long: the run ends as "blocked" (rerun later)."""


class Gate:
    """One brake shared by every worker: polite pacing between YouTube requests, and after a bot check
    ("Sign in to confirm you're not a bot", HTTP 429) a pause for everyone with growing backoff; the same
    request is then retried, so nothing is skipped. After `max_blocked` seconds of continuous blocking, give up."""

    def __init__(self, min_interval=1.5, backoffs=(300, 600, 1200, 1800), max_blocked=5400, clock=time.time, sleep=time.sleep):
        self.min_interval, self.backoffs, self.max_blocked = min_interval, backoffs, max_blocked
        self.clock, self.sleep = clock, sleep
        self.lock = threading.Lock()
        self.next_ok = 0.0
        self.blocked_until = 0.0
        self.blocked_since: float | None = None
        self.trips = 0
        self.gave_up: str | None = None
        self.on_block = None   # callback(until, reason) - the pipeline logs it and tells the dashboard
        self.state_file: Path | None = None

    def persist(self, path: Path) -> None:
        """Keep a pause across restarts: a restarted run must not ask YouTube again before the pause ends, and
        the backoff keeps growing instead of starting over. A pause that ended long ago is forgotten."""
        self.state_file = path
        try:
            s = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if self.clock() - s.get("until", 0) < self.backoffs[-1]:
            self.blocked_until, self.blocked_since, self.trips = s["until"], s["since"], s["trips"]

    def _save(self) -> None:
        if not self.state_file:
            return
        if self.trips:
            self.state_file.write_text(json.dumps({"until": self.blocked_until, "since": self.blocked_since,
                                                   "trips": self.trips}), encoding="utf-8")
        else:
            self.state_file.unlink(missing_ok=True)

    def wait(self) -> None:
        while True:
            with self.lock:
                if self.gave_up:
                    raise Blocked(self.gave_up)
                now = self.clock()
                delay = self.blocked_until - now if self.blocked_until > now else max(0.0, self.next_ok - now)
                if delay <= 0:
                    self.next_ok = now + self.min_interval
                    return
            self.sleep(min(delay, 5.0))

    def trip(self, reason: str) -> None:
        with self.lock:
            now = self.clock()
            if self.blocked_until > now:
                return                      # already paused by another worker
            if self.blocked_since is None:
                self.blocked_since = now
            if now - self.blocked_since >= self.max_blocked:
                self.gave_up = f"YouTube kept asking for a bot check for {round((now - self.blocked_since) / 60)} min: {reason[:160]}"
                raise Blocked(self.gave_up)
            self.blocked_until = now + self.backoffs[min(self.trips, len(self.backoffs) - 1)]
            self.trips += 1
            self._save()
            until, callback = self.blocked_until, self.on_block
        if callback:
            callback(until, reason)

    def ok(self) -> None:
        with self.lock:
            if self.trips:
                self.trips, self.blocked_since = 0, None
                self._save()


GATE = Gate()
CALLS = [0]   # yt-dlp calls made by this process (the request budget shown on the dashboard)
EXTRA: list[str] = []   # e.g. --cookies-from-browser firefox (see configure)


def configure(cookies_from_browser: str | None = None, cookies_file: str | None = None, min_interval: float | None = None) -> None:
    """Optional signed-in cookies (rarely bot-checked; use a secondary account) and request pacing."""
    EXTRA.clear()
    if cookies_from_browser:
        EXTRA.extend(["--cookies-from-browser", cookies_from_browser])
    elif cookies_file:
        EXTRA.extend(["--cookies", cookies_file])
    if min_interval is not None:
        GATE.min_interval = min_interval


def clean_stderr(text: str) -> str:
    """yt-dlp's stderr without the Python-version notice it prints first (it would hide the real ERROR line)."""
    lines = [l for l in (text or "").splitlines() if l.strip() and not l.startswith(("Deprecated Feature", "WARNING:"))]
    return " | ".join(lines).strip()[-400:]


def _ytdlp(args: list[str], timeout: int, done=None) -> subprocess.CompletedProcess:
    """Every yt-dlp call: through the gate; a bot check pauses everyone and retries this same call.
    done(p): did the call achieve its purpose despite errors (e.g. media saved, only captions refused)?"""
    while True:
        GATE.wait()
        CALLS[0] += 1
        p = subprocess.run([YTDLP, *BASE, *EXTRA, *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env=UTF8)
        achieved = done(p) if done else p.returncode == 0 or bool(p.stdout.strip())
        if not achieved and BOT_CHECK.search(p.stderr or ""):
            GATE.trip(clean_stderr(p.stderr))
            continue
        if achieved:
            GATE.ok()
        return p


def _run(args: list[str], timeout: int = 180) -> dict:
    """Run yt-dlp and return its JSON."""
    p = _ytdlp(args, timeout)
    if p.stdout.strip():
        return json.loads(p.stdout.strip().splitlines()[-1] if args[0] == "--dump-json" else p.stdout)
    raise YtDlpError(clean_stderr(p.stderr) or f"yt-dlp exit {p.returncode}")


def _flat(url: str, limit: int, extra: list[str] | None = None) -> list[dict]:
    doc = _run(["--flat-playlist", "--dump-single-json", "--playlist-end", str(limit), *(extra or []), url])
    if not isinstance(doc, dict):   # yt-dlp prints "null" when a tab can't be read
        raise YtDlpError(f"no listing for {url}")
    return [e for e in (doc.get("entries") or []) if e and e.get("id")]


# ── discovery ────────────────────────────────────────────────────────────────

def search(query: str, limit: int = 30, under_4_min: bool = False) -> list[dict]:
    sp = SP_UNDER_4_MIN if under_4_min else SP_VIDEOS
    return _flat(f"https://www.youtube.com/results?search_query={quote(query)}&sp={sp}", limit)


def hashtag_shorts(tag: str, limit: int = 40) -> list[dict]:
    return _flat(f"https://www.youtube.com/hashtag/{quote(tag.lstrip('#').lower())}/shorts", limit)


# ── channels ─────────────────────────────────────────────────────────────────

def channel_tab(channel_id: str, tab: str, limit: int = 40) -> list[dict]:
    """Newest-first uploads of a channel tab ('videos' or 'shorts'), flat: views (+ approximate dates on videos)."""
    extra = ["--extractor-args", "youtubetab:approximate_date"] if tab == "videos" else []
    try:
        return _flat(f"https://www.youtube.com/channel/{channel_id}/{tab}", limit, extra)
    except YtDlpError as e:
        if re.search(r"does not have a .* tab|This channel does not have", str(e), re.I):
            return []
        raise


def channel_page(channel_id: str, limit: int = 60) -> dict:
    """A channel's /videos tab with what the tab itself shows about the channel (name, subscriber count) - one request.
    Views above 1,000 are rounded the way YouTube displays them (1.2K, 312K, 1.2M); dates are approximate."""
    doc = _run(["--flat-playlist", "--dump-single-json", "--playlist-end", str(limit),
                "--extractor-args", "youtubetab:approximate_date", f"https://www.youtube.com/channel/{channel_id}/videos"])
    if not isinstance(doc, dict):
        raise YtDlpError(f"no listing for {channel_id}/videos")
    return {"channel_id": channel_id, "channel": doc.get("channel") or doc.get("uploader"), "subs": doc.get("channel_follower_count"),
            "entries": [{"id": e["id"], "title": e.get("title"), "views": e.get("view_count"), "timestamp": e.get("timestamp"),
                         "duration": e.get("duration")} for e in (doc.get("entries") or []) if e and e.get("id")]}


def handle_tab(handle: str, tab: str = "videos", limit: int = 40) -> dict:
    """A channel known by its @handle (a seed the user gave): its ID, name and newest uploads in one request."""
    m = re.search(r"@[\w.\-]+", handle)          # "@Name", "Name" or "https://www.youtube.com/@Name/videos"
    h = m.group(0) if m else "@" + handle.strip().strip("/")
    extra = ["--extractor-args", "youtubetab:approximate_date"] if tab == "videos" else []
    doc = _run(["--flat-playlist", "--dump-single-json", "--playlist-end", str(limit), *extra,
                f"https://www.youtube.com/{h}/{tab}"])
    if not isinstance(doc, dict):
        raise YtDlpError(f"no listing for {h}/{tab}")
    return {"channel_id": doc.get("channel_id") or doc.get("uploader_id"), "channel": doc.get("channel") or doc.get("uploader"),
            "entries": [e for e in (doc.get("entries") or []) if e and e.get("id")]}


# ── one video ────────────────────────────────────────────────────────────────

def metadata(video_id: str) -> dict:
    full = _run(["--dump-json", "--skip-download", f"https://www.youtube.com/watch?v={video_id}"])
    meta = {k: full.get(k) for k in KEEP}
    meta["caption_langs"] = sorted((full.get("subtitles") or {}).keys())
    meta["auto_caption_langs"] = sorted(k for k in (full.get("automatic_captions") or {}) if k.endswith("-orig") or len(k) <= 3)
    return meta


def captions(video_id: str, lang: str, out_dir: Path, spoken: str | None = None) -> Path | None:
    """json3 captions (word timings) in the language actually spoken: the creator's subtitles, else YouTube's
    auto captions of that language. `spoken` = the video's declared audio language (metadata 'language');
    the many "xx-orig" tracks belong to auto-dubbed audio and say nothing about the original language."""
    out_dir.mkdir(parents=True, exist_ok=True)
    spoken = (spoken or lang).split("-")[0].lower()
    for sub_args in (["--write-subs", "--sub-langs", f"{spoken}.*"],
                     ["--write-auto-subs", "--sub-langs", f"{spoken}-orig,{spoken}"],
                     ["--write-subs", "--sub-langs", f"{lang}.*"]):
        _ytdlp(["--skip-download", *sub_args, "--sub-format", "json3", "-o", str(out_dir / "captions.%(ext)s"),
                f"https://www.youtube.com/watch?v={video_id}"], 180)
        found = sorted(out_dir.glob("captions*.json3"))
        if found:
            return found[0]
    return None


def comments(video_id: str, limit: int = 40) -> list[dict]:
    full = _run(["--dump-json", "--skip-download", "--write-comments", "--extractor-args",
                 f"youtube:max_comments={limit},{limit},0,0;comment_sort=top",
                 f"https://www.youtube.com/watch?v={video_id}"], timeout=300)
    return [{"text": c.get("text"), "likes": c.get("like_count"), "author_is_uploader": c.get("author_is_uploader")}
            for c in (full.get("comments") or [])][:limit]


def thumbnail(video_id: str, out: Path) -> Path | None:
    out.parent.mkdir(parents=True, exist_ok=True)
    for name in ("maxresdefault", "sddefault", "hqdefault"):
        try:
            with urllib.request.urlopen(f"https://i.ytimg.com/vi/{video_id}/{name}.jpg", timeout=30) as r:
                data = r.read()
            if len(data) > 2000:   # YouTube serves a tiny placeholder for missing sizes
                out.write_bytes(data)
                return out
        except Exception:
            continue
    return None


def pick_captions(out_dir: Path, manual_langs: list[str]) -> Path | None:
    """Of the caption files written, prefer the creator's own subtitles, then YouTube's original-audio track."""
    files = sorted(out_dir.glob("captions.*.json3"))
    lang = lambda f: f.name[len("captions."):-len(".json3")]
    for good in (lambda f: lang(f) in manual_langs, lambda f: lang(f).endswith("-orig"), lambda f: True):
        hit = next((f for f in files if good(f)), None)
        if hit:
            return hit
    return None


def ranged_get(url: str, n_bytes: int, path: Path, timeout: int = 120) -> int:
    """The first `n_bytes` of a media stream (an HTTP range request to the stream server, not a YouTube page)."""
    req = urllib.request.Request(url, headers={"Range": f"bytes=0-{n_bytes - 1}", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r, path.open("wb") as f:
        shutil.copyfileobj(r, f, 1 << 16)
    return path.stat().st_size


def opening_bytes(kbps: float | None, seconds: int) -> int:
    """How much of a stream to fetch for its first `seconds`: bitrate x (seconds + 30 s) x 1.5, at least 1.5 MB."""
    return max(1_500_000, int((kbps or 800) * 1000 / 8 * (seconds + 30) * 1.5))


def opening(video_id: str, out_dir: Path, ffmpeg: str, seconds: int, height: int = 360, subs: list[str] | None = None) -> Path:
    """The first `seconds` of a long video without downloading all of it. One yt-dlp call resolves the video and
    audio stream URLs (captions in the same call); range requests fetch only their first megabytes; ffmpeg cuts
    the opening. A 3-hour ambient video costs ~6 MB instead of ~500 MB."""
    p = _ytdlp(["-f", f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/bv*[height<={height}]+ba", "--no-simulate", "--skip-download",
                *(subs or []), "-O", "%(requested_formats.0.tbr)s %(requested_formats.0.url)s",
                "-O", "%(requested_formats.1.tbr)s %(requested_formats.1.url)s", f"https://www.youtube.com/watch?v={video_id}"], 300)
    streams = [l.split(" ", 1) for l in p.stdout.splitlines() if " http" in l]
    if len(streams) < 2:
        raise YtDlpError(clean_stderr(p.stderr) or "no stream URLs")
    parts = []
    for i, (kbps, url) in enumerate(streams[:2]):
        part = out_dir / f"opening.{i}.part"
        ranged_get(url, opening_bytes(float(kbps) if kbps not in ("NA", "None") else None, seconds), part)
        parts.append(part)
    media = out_dir / "media.mp4"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(parts[0]), "-i", str(parts[1]),
                    "-map", "0:v:0", "-map", "1:a:0", "-t", str(seconds), "-c", "copy", str(media)], capture_output=True, timeout=300)
    for part in parts:
        part.unlink(missing_ok=True)
    if not media.exists() or media.stat().st_size < 10_000:
        media.unlink(missing_ok=True)
        raise YtDlpError("could not cut the opening from the partial streams")
    return media


MAX_FULL_DOWNLOAD_SECONDS = 1200   # a long video's full (<= 360p) file is fetched only as a fallback, and only up to 20 min


def download(video_id: str, out_dir: Path, ffmpeg_dir: str, first_seconds: int | None = None,
             sub_langs: str | None = None, duration: float | None = None) -> Path | None:
    """Media for measurement only, plus captions in the same call when `sub_langs` is given (each yt-dlp call
    pays YouTube's JS challenge, so one call per video). Shorts: whole (<= 480p). Long-form: only the opening,
    by range requests on the stream URLs (`opening`); if that fails, a small full file (<= 360p) cut locally —
    only for videos up to 20 minutes (yt-dlp's --download-sections streams through ffmpeg, which timed out with
    the 2023 ffmpeg build)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(f for f in out_dir.glob("media.*") if f.suffix in (".mp4", ".webm", ".mkv"))
    if existing:
        return existing[0]
    height = 360 if first_seconds else 480
    stem = "full" if first_seconds else "media"
    # Captions are optional: a 429 on YouTube's caption endpoint must not cost the media (Whisper covers it).
    subs = ["--write-subs", "--write-auto-subs", "--sub-langs", sub_langs, "--sub-format", "json3", "--ignore-errors",
            "--sleep-subtitles", "2", "-o", "subtitle:" + str(out_dir / "captions.%(ext)s")] if sub_langs else []
    ffmpeg = str(Path(ffmpeg_dir) / "ffmpeg.exe") if (Path(ffmpeg_dir) / "ffmpeg.exe").exists() else "ffmpeg"
    if first_seconds:   # long videos (here up to 10 hours): only the opening, by range requests
        try:
            return opening(video_id, out_dir, ffmpeg, first_seconds, subs=subs)
        except Blocked:
            raise
        except Exception as e:   # the small full file below, but never for an hours-long video
            if not duration or duration > MAX_FULL_DOWNLOAD_SECONDS:
                raise YtDlpError(f"opening not available ({str(e)[:120]}); not downloading a {round((duration or 0) / 60)}-minute video")
    args = ["-f", f"bv*[height<={height}]+ba/b[height<={height}]/wv*+ba/w", "--merge-output-format", "mp4",
            "--ffmpeg-location", ffmpeg_dir, *subs, "-o", str(out_dir / f"{stem}.%(ext)s"),
            f"https://www.youtube.com/watch?v={video_id}"]
    media_saved = lambda _p: any(f.suffix in (".mp4", ".webm", ".mkv") for f in out_dir.glob(f"{stem}.*"))
    p = _ytdlp(args, 1800, done=media_saved)   # a refused caption track alone is not a bot block
    found = sorted(f for f in out_dir.glob(f"{stem}.*") if f.suffix in (".mp4", ".webm", ".mkv"))
    if not found:
        raise YtDlpError(clean_stderr(p.stderr) or "download failed")
    if not first_seconds:
        return found[0]
    media = out_dir / "media.mp4"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(found[0]), "-t", str(first_seconds),
                    "-c", "copy", str(media)], capture_output=True, timeout=600)
    for f in out_dir.glob("full.*"):
        f.unlink(missing_ok=True)
    if not media.exists():
        raise YtDlpError("could not cut the opening")
    return media


# ── independent check ────────────────────────────────────────────────────────

def watch_page_views(video_id: str) -> int | None:
    """viewCount straight from the watch page's ytInitialPlayerResponse (not via yt-dlp)."""
    req = urllib.request.Request(f"https://www.youtube.com/watch?v={video_id}&hl=en",
                                 headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en"})
    GATE.wait()
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8", "replace")
    m = re.search(r'"videoDetails":\{.*?"viewCount":"(\d+)"', html, re.S)
    if not m and BOT_CHECK.search(html):
        raise YtDlpError("the watch page shows YouTube's bot check")
    return int(m.group(1)) if m else None


def available() -> str | None:
    """None when usable, else what is missing."""
    if not Path(YTDLP).exists():
        return f"yt-dlp not found next to {sys.executable}"
    if not shutil.which("node"):
        return "Node.js not found (yt-dlp needs a JavaScript runtime for YouTube)"
    return None
