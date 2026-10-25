"""A local stand-in for the parts of YouTube the extension depends on (http://127.0.0.1:8898).

Reproduces what was measured on the real site (PLAN.md): the same tile elements, Shorts tiles, the hover
preview that covers the thumbnail, the watch page (player + sidebar), single-page navigation, and a
playlist service that ALLOWS DUPLICATES, whose "contains" flag LAGS behind changes, and whose playlist
pages carry a DECOY section continuation next to the real video-list one. Requests without the session's
SAPISIDHASH signature are refused.

Test controls: GET /__state, POST /__reset {account, lag}, POST /__inject {fail_next_add | elsewhere:
{playlistId, videoId}}.      python fake_youtube.py [port]
"""
import json
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).parent
PAGE_SIZE = 3
LOCK = threading.Lock()


def default_state():
    big = [{"videoId": f"bigVideo{n:03d}", "setVideoId": f"SETBIG{n:010d}"} for n in range(1, 8)]
    return {
        "account": "acct-test||",
        "lag": 3.0,
        "playlists": {
            "WL": {"title": "Watch later", "privacy": "PRIVATE", "entries": []},
            "PLbigList00001": {"title": "Big list", "privacy": "PRIVATE", "entries": big},
            "PLpublic00001": {"title": "Public picks", "privacy": "PUBLIC",
                              "entries": [{"videoId": "homeVideo06", "setVideoId": "SETPUB0000000001"}]},
            "PLtestQuick001": {"title": "zz quick-save test", "privacy": "PRIVATE", "entries": []},
        },
        "events": [],   # (time, playlistId, videoId, +1/-1) - drives the lagging flag
        "log": [],
        "inject": {},
    }


STATE = default_state()


def membership_at(pid, vid, t):
    """Copies of vid in pid as of time t (initial entries count from the beginning)."""
    pl = STATE["playlists"][pid]
    count = sum(1 for e in pl["entries"] if e["videoId"] == vid)
    for when, p, v, delta in STATE["events"]:
        if p == pid and v == vid and when > t:
            count -= delta
    return count


def video_entry(e):
    return {"playlistVideoRenderer": {"videoId": e["videoId"], "setVideoId": e["setVideoId"],
                                      "title": {"runs": [{"text": f"Video {e['videoId']}"}]}}}


def list_continuation(token):
    return {"continuationItemRenderer": {"continuationEndpoint": {"commandExecutorCommand": {"commands": [
        {"continuationCommand": {"token": token, "request": "CONTINUATION_REQUEST_TYPE_BROWSE"}}]}}}}


def page_items(pid, offset):
    entries = STATE["playlists"][pid]["entries"]
    items = [video_entry(e) for e in entries[offset:offset + PAGE_SIZE]]
    if offset + PAGE_SIZE < len(entries):
        items.append(list_continuation(f"plpage_{pid}_{offset + PAGE_SIZE}"))
    return items


def browse(body):
    token = body.get("continuation")
    if token:
        if token.startswith("decoy_"):
            return 200, {"responseContext": {}}  # what the real section continuation returned
        _, pid, offset = token.split("_", 2)
        return 200, {"onResponseReceivedActions": [{"appendContinuationItemsAction": {
            "continuationItems": page_items(pid, int(offset))}}]}
    pid = (body.get("browseId") or "")[2:]
    if pid not in STATE["playlists"]:
        return 400, {"error": "unknown playlist"}
    section = {"itemSectionRenderer": {"contents": [{"playlistVideoListRenderer": {"contents": page_items(pid, 0)}}]}}
    decoy = {"continuationItemRenderer": {"continuationEndpoint": {"continuationCommand": {
        "token": f"decoy_{pid}", "request": "CONTINUATION_REQUEST_TYPE_BROWSE"}}}}
    return 200, {"contents": {"twoColumnBrowseResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
        "sectionListRenderer": {"contents": [section, decoy]}}}}]}}}


def list_playlists(body):
    vid = (body.get("videoIds") or [""])[0]
    seen = time.time() - STATE["lag"]   # the flag shows the state as it was `lag` seconds ago
    out = []
    for pid, pl in STATE["playlists"].items():
        out.append({"playlistAddToOptionRenderer": {
            "playlistId": pid, "title": {"simpleText": pl["title"]}, "privacy": pl["privacy"],
            "containsSelectedVideos": "ALL" if membership_at(pid, vid, seen) > 0 else "NONE"}})
    return 200, {"contents": [{"addToPlaylistRenderer": {"playlists": out, "actions": []}}]}


def add_entry(pid, vid, set_id=None):
    entry = {"videoId": vid, "setVideoId": set_id or secrets.token_hex(8).upper()}
    STATE["playlists"][pid]["entries"].append(entry)
    STATE["events"].append((time.time(), pid, vid, +1))
    return entry


def edit_playlist(body):
    pid = body.get("playlistId")
    if pid not in STATE["playlists"]:
        return 400, {"error": "unknown playlist"}
    results = []
    for action in body.get("actions", []):
        kind = action.get("action")
        if kind == "ACTION_ADD_VIDEO":
            vid = action["addedVideoId"]
            if STATE["inject"].pop("fail_next_add", False):
                return 500, {"error": "injected failure"}
            elsewhere = STATE["inject"].get("elsewhere")
            if elsewhere and elsewhere["videoId"] == vid and elsewhere["playlistId"] == pid:
                STATE["inject"].pop("elsewhere")
                add_entry(pid, vid, "ELSEWHERE0000001")      # the same video added on another device
            entry = add_entry(pid, vid)                       # no deduplication, like YouTube
            results.append({"playlistEditVideoAddedResultData": {"videoId": vid, "setVideoId": entry["setVideoId"]}})
        elif kind == "ACTION_REMOVE_VIDEO":
            entries = STATE["playlists"][pid]["entries"]
            hit = next((e for e in entries if e["setVideoId"] == action.get("setVideoId")), None)
            if not hit:
                return 200, {"status": "STATUS_FAILED"}
            entries.remove(hit)
            STATE["events"].append((time.time(), pid, hit["videoId"], -1))
        elif kind == "ACTION_REMOVE_VIDEO_BY_VIDEO_ID":
            entries = STATE["playlists"][pid]["entries"]
            for e in [e for e in entries if e["videoId"] == action.get("removedVideoId")]:
                entries.remove(e)
                STATE["events"].append((time.time(), pid, e["videoId"], -1))
        else:
            return 400, {"error": f"unsupported action {kind}"}
    return 200, {"status": "STATUS_SUCCEEDED", "playlistEditResults": results, "actions": []}


ENDPOINTS = {"/youtubei/v1/browse": browse, "/youtubei/v1/playlist/get_add_to_playlist": list_playlists,
             "/youtubei/v1/browse/edit_playlist": edit_playlist}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/__state":
            with LOCK:
                return self.send_json(200, {k: STATE[k] for k in ("account", "lag", "playlists", "log", "inject")})
        if path in ("/app.js", "/app.css"):
            data = (HERE / path[1:]).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript" if path.endswith(".js") else "text/css")
            self.end_headers()
            return self.wfile.write(data)
        if path == "/favicon.ico":
            self.send_response(204)
            return self.end_headers()
        # Every page is the single-page app shell; app.js renders by URL (like YouTube).
        shell = (HERE / "shell.html").read_text(encoding="utf-8").replace("__ACCOUNT__", STATE["account"])
        data = shell.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Set-Cookie", "SAPISID=fake-sapisid-value; Path=/")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        path = urlsplit(self.path).path
        body = self.body()
        with LOCK:
            if path == "/__reset":
                fresh = default_state()
                STATE.clear()
                STATE.update(fresh)
                STATE.update({k: v for k, v in body.items() if k in ("account", "lag")})
                return self.send_json(200, {"ok": True})
            if path == "/__account":
                STATE["account"] = body["account"]
                return self.send_json(200, {"ok": True})
            if path == "/__inject":
                STATE["inject"].update(body)
                return self.send_json(200, {"ok": True})
            if path == "/__add_elsewhere":   # another device adds a video right now
                add_entry(body["playlistId"], body["videoId"], body.get("setVideoId", "ELSEWHERE0000002"))
                return self.send_json(200, {"ok": True})
            handler = ENDPOINTS.get(path)
            if not handler:
                return self.send_json(404, {"error": "not found"})
            auth = self.headers.get("Authorization", "")
            signed = auth.startswith("SAPISIDHASH ") and self.headers.get("X-Origin") and "context" in body
            STATE["log"].append({"path": path, "signed": bool(signed), "body": {k: v for k, v in body.items() if k != "context"}})
            if not signed:
                return self.send_json(401, {"error": "not signed"})
            code, obj = handler(body)
        self.send_json(code, obj)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8898
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"fake YouTube on http://127.0.0.1:{port}", flush=True)
    server.serve_forever()
