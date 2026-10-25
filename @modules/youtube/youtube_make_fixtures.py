"""Anonymise the recorded YouTube replies (@modules/youtube/@downloads/discovery/raw, from youtube_probe.py record-fixtures) into the extension's test fixtures.

Keeps the real structure; replaces every video / playlist / entry / channel ID with stable fakes, all
visible text with neutral text, URLs and tokens with placeholders; drops tracking data; trims long lists.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = Path(__file__).parent / "@downloads" / "discovery" / "raw"
DEST = ROOT / "@created-extensions" / "youtube-quick-save" / "tests" / "fixtures" / "youtubei"
DROP = {"responseContext", "trackingParams", "clickTrackingParams", "loggingDirectives", "microformat", "sidebar",
        "loggingContext", "frameworkUpdates", "accessibility", "accessibilityData",
        "header", "topbar", "alerts", "metadata"}  # page chrome (title, avatar, account) - not used by the extension
TEXT_KEYS = {"simpleText", "text", "content", "label", "title", "pageTitle", "description", "tooltip", "placeholder"}
KEEP_TEXT = re.compile(r"[A-Z_]+|\d[\d,:. ]*|Watch later|zz quick-save test|Text")
fakes: dict[str, str] = {}
counters: dict[str, int] = {}
FAKE_VALUES: set[str] = set()


def fake(kind: str, real: str) -> str:
    if real not in fakes:
        counters[kind] = counters.get(kind, 0) + 1
        n = counters[kind]
        fakes[real] = {"video": f"vid{n:08d}",      # 11 chars like a real video ID
                       "playlist": f"PLfake{n:07d}",
                       "set": f"SET{n:013d}",
                       "channel": f"UCfake{n:018d}",
                       "token": f"TOKEN_{n}"}[kind]
    return fakes[real]


def collect(o, out):
    if isinstance(o, dict):
        for k, v in o.items():
            if isinstance(v, str):
                if k in ("videoId", "addedVideoId", "removedVideoId"):
                    out.setdefault("video", set()).add(v)
                elif k in ("playlistId", "listId") and v not in ("WL", "LL"):
                    out.setdefault("playlist", set()).add(v)
                elif k == "setVideoId":
                    out.setdefault("set", set()).add(v)
                elif k in ("browseId", "channelId", "externalChannelId") and v.startswith("UC"):
                    out.setdefault("channel", set()).add(v)
                elif k == "token":
                    out.setdefault("token", set()).add(v)
            collect(v, out)
    elif isinstance(o, list):
        for v in o:
            collect(v, out)


def scrub(o, key=None):
    if isinstance(o, dict):
        return {k: scrub(v, k) for k, v in o.items() if k not in DROP}
    if isinstance(o, list):
        return [scrub(v, key) for v in o]
    if isinstance(o, str):
        for real, f in sorted(fakes.items(), key=lambda kv: -len(kv[0])):
            o = o.replace(real, f)
        # Whitelist: IDs (now fake), enums, numbers, API paths. Every other string becomes neutral text.
        if o in FAKE_VALUES or KEEP_TEXT.fullmatch(o) or o.startswith("/youtubei/"):
            return o
        return "https://example.invalid/" if o.startswith("http") else "Text"
    return o


def human_text(o, out):
    """Every human-readable string in the raw replies (to prove none survives)."""
    if isinstance(o, dict):
        for k, v in o.items():
            if isinstance(v, str) and len(v) > 3 and " " in v.strip() or (isinstance(v, str) and k in TEXT_KEYS and len(v) > 3):
                if not KEEP_TEXT.fullmatch(v):
                    out.add(v)
            human_text(v, out)
    elif isinstance(o, list):
        for v in o:
            human_text(v, out)


def trim_video_lists(o, keep=3):
    """Keep the first `keep` playlistVideoRenderer entries of each video list, and its continuation item."""
    if isinstance(o, list):
        if any(isinstance(v, dict) and "playlistVideoRenderer" in v for v in o):
            videos = [v for v in o if isinstance(v, dict) and "playlistVideoRenderer" in v][:keep]
            return videos + [v for v in o if isinstance(v, dict) and "continuationItemRenderer" in v]
        return [trim_video_lists(v, keep) for v in o]
    if isinstance(o, dict):
        return {k: trim_video_lists(v, keep) for k, v in o.items()}
    return o


def main():
    raw = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in RAW.glob("*.json")}
    texts: set[str] = set()
    for doc in raw.values():
        human_text(doc, texts)
    # Playlist list: keep Watch later, two ordinary playlists and the test playlist.
    lst = raw["get_add_to_playlist"]
    pls = lst["contents"][0]["addToPlaylistRenderer"]["playlists"]
    title = lambda p: p["playlistAddToOptionRenderer"]["title"].get("simpleText", "")
    keep = [p for p in pls if title(p) in ("Watch later", "zz quick-save test")]
    keep[1:1] = [p for p in pls if title(p) not in ("Watch later", "zz quick-save test")][:2]
    lst["contents"][0]["addToPlaylistRenderer"]["playlists"] = keep
    raw["browse_page1"] = trim_video_lists(raw["browse_page1"])
    raw["browse_page2"] = trim_video_lists(raw["browse_page2"])
    ids: dict[str, set] = {}
    for doc in raw.values():
        collect(doc, ids)
    for kind in ("video", "playlist", "set", "channel", "token"):
        for real in sorted(ids.get(kind, ())):
            fake(kind, real)
    FAKE_VALUES.update(fakes.values(), {"WL", "LL"})
    DEST.mkdir(parents=True, exist_ok=True)
    for name, doc in raw.items():
        (DEST / f"{name}.json").write_text(json.dumps(scrub(doc), indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {name}.json")
    # The fixtures must not contain any real ID.
    blob = "".join(p.read_text(encoding="utf-8") for p in DEST.glob("*.json"))
    leaks = [r for r in fakes if r in blob]
    text_leaks = [t for t in texts if json.dumps(t, ensure_ascii=False) in blob]  # whole string values
    print(f"{len(fakes)} IDs replaced; ID leaks: {leaks or 'none'}; "
          f"{len(texts)} raw texts checked, text leaks: {text_leaks[:10] or 'none'}")
    if leaks or text_leaks:
        raise SystemExit("LEAK - fixtures must not contain real IDs or text")


if __name__ == "__main__":
    main()
