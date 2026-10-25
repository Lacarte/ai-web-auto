"""YouTube discovery and live checks for the quick-save extension.

    venv\\Scripts\\python @modules\\youtube\\youtube_probe.py discover | time-read | save-test | inspect-clean | record-fixtures

Opens its own tab (bound: no other tab is touched), probes the channel / home / watch pages, tests the
hover preview, tries YouTube's own "which playlists contain this video" request, then opens and closes
the Save dialog once to see which request YouTube itself makes. Results: @modules/youtube/@downloads/discovery/ (gitignored).
Only save-test, inspect-clean and record-fixtures write, and only to the private 'zz quick-save test' playlist.
"""
import asyncio
import json
import re
import sys
import time
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "@modules")]
sys.stdout.reconfigure(encoding="utf-8")
warnings.filterwarnings("ignore")
from loguru import logger  # noqa: E402
logger.remove()
logger.add(sys.stderr, level="WARNING", format="  [server] {message}")
import module_kit as mk  # noqa: E402
from ai_web_auto_backend.automation_controller import BrowserAgent  # noqa: E402

OUT = Path(__file__).parent / "@downloads" / "discovery"
CHANNEL = "https://www.youtube.com/@RayCodingCorner"
HOME = "https://www.youtube.com/"
WATCH = "https://www.youtube.com/watch?v=xxf6D9jynFw"

PAGE_PROBE_JS = r"""
(() => {
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const idOf = href => { const m = (href || '').match(/[?&]v=([\w-]{11})|\/shorts\/([\w-]{11})/); return m ? (m[1] || m[2]) : null; };
  const isItem = t => /^(ytd-(rich-item|grid-video|compact-video|video|reel-item|playlist-video|playlist-panel-video|compact-autoplay)-renderer|yt-lockup-view-model|ytm-shorts-lockup-view-model(-v2)?)$/.test(t);
  const itemOf = a => { for (let el = a; el && el !== document.body; el = el.parentElement) if (isItem(el.tagName.toLowerCase())) return el; return null; };
  const chain = a => { const out = []; for (let el = a; el && el !== document.body && out.length < 16; el = el.parentElement) out.push(el.tagName.toLowerCase() + (el.id ? '#' + el.id : '')); return out; };
  const rect = el => { const r = el.getBoundingClientRect(); return [r.x | 0, r.y | 0, r.width | 0, r.height | 0]; };
  const links = [...document.querySelectorAll('a[href*="/watch?v="], a[href*="/shorts/"]')].filter(vis);
  const items = {}, samples = {};
  for (const a of links) {
    const it = itemOf(a), k = it ? it.tagName.toLowerCase() : 'NONE';
    items[k] = (items[k] || 0) + 1;
    if (!samples[k]) samples[k] = { chain: chain(a), link_id: a.id || null, link_class: String(a.className || '').slice(0, 80),
      video_id: idOf(a.href), link_rect: rect(a), item_rect: it ? rect(it) : null, in_secondary: !!a.closest('#secondary') };
  }
  const cfg = (window.ytcfg && window.ytcfg.data_) || {};
  const flexy = document.querySelector('ytd-watch-flexy');
  const count = sel => [...document.querySelectorAll(sel)].filter(vis).length;
  const p = document.querySelector('ytd-video-preview');
  return JSON.stringify({
    url: location.href, title: document.title, video_links: links.length,
    unique_video_ids: new Set(links.map(a => idOf(a.href)).filter(Boolean)).size, items, samples,
    watch: flexy ? { video_id: flexy.getAttribute('video-id'), player: count('#movie_player'), video: count('#movie_player video'),
                     player_rect: document.querySelector('#movie_player') ? rect(document.querySelector('#movie_player')) : null,
                     secondary_links: document.querySelectorAll('#secondary a[href*="/watch?v="]').length } : null,
    preview: p ? { visible: vis(p) && !p.hidden, rect: rect(p) } : null,
    save_controls: [...document.querySelectorAll('button, [role="button"]')].filter(vis)
      .map(b => (b.getAttribute('aria-label') || b.textContent || '').trim().replace(/\s+/g, ' ')).filter(t => /save|playlist/i.test(t)).slice(0, 8),
    cfg: { logged_in: cfg.LOGGED_IN ?? null, has_api_key: !!cfg.INNERTUBE_API_KEY, client: cfg.INNERTUBE_CONTEXT?.client?.clientName ?? null,
           client_version: cfg.INNERTUBE_CLIENT_VERSION ?? null, client_name_id: cfg.INNERTUBE_CONTEXT_CLIENT_NAME ?? null,
           session_index: cfg.SESSION_INDEX ?? null, delegated_session: !!cfg.DELEGATED_SESSION_ID, datasync: !!cfg.DATASYNC_ID },
    auth_cookies_readable: ['SAPISID', '__Secure-1PAPISID', '__Secure-3PAPISID'].filter(n => document.cookie.split('; ').some(c => c.startsWith(n + '='))),
  });
})()
"""

THUMB_POINT_JS = r"""
(() => {
  const a = [...document.querySelectorAll('a[href*="/watch?v="]')].find(a => {
    const r = a.getBoundingClientRect(); return r.width > 150 && r.height > 80 && r.top > 80 && r.bottom < innerHeight - 10; });
  if (!a) return JSON.stringify(null);
  const r = a.getBoundingClientRect();
  return JSON.stringify({ x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), href: a.href });
})()
"""

AT_POINT_JS = r"""
((x, y) => {
  const el = document.elementFromPoint(x, y);
  const chain = []; for (let e = el; e && e !== document.body && chain.length < 16; e = e.parentElement) chain.push(e.tagName.toLowerCase() + (e.id ? '#' + e.id : ''));
  const p = document.querySelector('ytd-video-preview');
  const pr = p && p.getBoundingClientRect();
  const pl = p && p.querySelector('a[href*="/watch?v="], a[href*="/shorts/"]');
  return JSON.stringify({ chain, preview_visible: !!(pr && pr.width > 0 && pr.height > 0 && !p.hidden),
    preview_rect: pr ? [pr.x | 0, pr.y | 0, pr.width | 0, pr.height | 0] : null, preview_link: pl ? pl.href : null,
    point_inside_preview: !!(pr && x >= pr.left && x <= pr.right && y >= pr.top && y <= pr.bottom) });
})(__X__, __Y__)
"""

# YouTube's own request, made the way its web client makes it (session cookies + SAPISID hash). READ ONLY.
INNERTUBE_JS = r"""
(async (path, payload) => {
  const cfg = window.ytcfg.data_;
  const cookie = n => (document.cookie.split('; ').find(c => c.startsWith(n + '=')) || '').slice(n.length + 1);
  const sha1 = async s => [...new Uint8Array(await crypto.subtle.digest('SHA-1', new TextEncoder().encode(s)))]
    .map(b => b.toString(16).padStart(2, '0')).join('');
  const ts = Math.floor(Date.now() / 1000), origin = location.origin, auth = [];
  for (const [name, scheme] of [['SAPISID', 'SAPISIDHASH'], ['__Secure-1PAPISID', 'SAPISID1PHASH'], ['__Secure-3PAPISID', 'SAPISID3PHASH']]) {
    const v = cookie(name); if (v) auth.push(`${scheme} ${ts}_${await sha1(`${ts} ${v} ${origin}`)}`);
  }
  const headers = { 'Content-Type': 'application/json', 'X-Origin': origin, 'X-Goog-AuthUser': String(cfg.SESSION_INDEX ?? 0),
    'X-Youtube-Client-Name': String(cfg.INNERTUBE_CONTEXT_CLIENT_NAME ?? 1), 'X-Youtube-Client-Version': String(cfg.INNERTUBE_CLIENT_VERSION) };
  if (auth.length) headers['Authorization'] = auth.join(' ');
  if (cfg.DELEGATED_SESSION_ID) headers['X-Goog-PageId'] = cfg.DELEGATED_SESSION_ID;
  const res = await fetch(`/youtubei/v1/${path}?prettyPrint=false`, { method: 'POST', credentials: 'include', headers,
    body: JSON.stringify({ context: cfg.INNERTUBE_CONTEXT, ...payload }) });
  const text = await res.text();
  let json = null; try { json = JSON.parse(text); } catch (e) {}
  return JSON.stringify({ status: res.status, auth_schemes: auth.map(a => a.split(' ')[0]), json, text: json ? null : text.slice(0, 300) });
})(__PATH__, __PAYLOAD__)
"""

SAVE_BUTTON_JS = r"""
(() => {
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const btns = [...document.querySelectorAll('ytd-watch-metadata button')].filter(vis);
  const b = btns.find(b => /^save to playlist$/i.test(b.getAttribute('aria-label') || '')) ||
            btns.find(b => /^save$/i.test((b.textContent || '').trim()));
  if (!b) return JSON.stringify(null);
  const r = b.getBoundingClientRect();
  return JSON.stringify({ x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
    label: b.getAttribute('aria-label'), text: (b.textContent || '').trim() });
})()
"""

DIALOG_JS = r"""
(() => {
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const heading = [...document.querySelectorAll('*')].find(e => e.children.length === 0 && /^save to/i.test((e.textContent || '').trim()) && vis(e));
  let box = heading; for (let i = 0; box && i < 12; i++) { if (box.matches && box.matches('tp-yt-paper-dialog, ytd-popup-container *[role="dialog"], [role="dialog"], yt-sheet-view-model, ytd-add-to-playlist-renderer')) break; box = box.parentElement; }
  const chain = []; for (let e = heading; e && e !== document.body && chain.length < 16; e = e.parentElement) chain.push(e.tagName.toLowerCase() + (e.id ? '#' + e.id : ''));
  const rows = box ? [...box.querySelectorAll('[role="checkbox"], [aria-pressed], tp-yt-paper-checkbox, yt-list-item-view-model, ytd-playlist-add-to-option-renderer, toggleable-list-item-view-model, button')]
    .filter(vis).map(r => ({ tag: r.tagName.toLowerCase(), text: (r.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 60),
      label: r.getAttribute('aria-label'), checked: r.getAttribute('aria-checked'), pressed: r.getAttribute('aria-pressed') })).slice(0, 30) : [];
  const calls = performance.getEntriesByType('resource').map(e => e.name).filter(n => n.includes('/youtubei/v1/'))
    .map(n => n.split('?')[0].replace(location.origin, ''));
  return JSON.stringify({ open: !!heading, heading_chain: chain, dialog_tag: box ? box.tagName.toLowerCase() : null, rows,
    youtubei_calls: [...new Set(calls)] });
})()
"""


def save(name, data):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def playlist_entries(obj, path="$"):
    """Every dict that carries a playlistId, with where it sits in the response."""
    found = []
    if isinstance(obj, dict):
        if "playlistId" in obj:
            found.append((re.sub(r"\[\d+\]", "[*]", path), obj))
        for k, v in obj.items():
            found += playlist_entries(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found += playlist_entries(v, f"{path}[{i}]")
    return found


def text_of(t):
    if isinstance(t, dict):
        return t.get("simpleText") or t.get("content") or "".join(r.get("text", "") for r in t.get("runs", []))
    return t if isinstance(t, str) else None


async def poll(fn, ok, timeout=20.0, interval=0.5):
    deadline = time.monotonic() + timeout
    value = await fn()
    while not ok(value) and time.monotonic() < deadline:
        await asyncio.sleep(interval)
        value = await fn()
    return value


async def discover():
    agent = BrowserAgent()
    print("Waiting for AI Web-Auto in Chrome to connect on ws://localhost:8765 ...", flush=True)
    await agent.connect(timeout=90)
    s = agent.server
    try:
        if "tab_id" not in s.capabilities:
            print("STOP: the AI Web-Auto extension in Chrome is the old version (no tab pinning).\n"
                  "Reload it in chrome://extensions, then run this again.")
            return 2
        print("connected; tab pinning supported", flush=True)
        resp = await mk._command(s, "navigate", {"url": CHANNEL, "new_tab": True}, timeout_ms=60000)
        if resp.status != "success" or resp.data.tabId is None:
            print(f"STOP: could not open a tab: {resp.error}")
            return 1
        s.bind(resp.data.tabId)
        print(f"opened its own tab {resp.data.tabId} (bound)", flush=True)
        probe = lambda: mk._run_script(s, PAGE_PROBE_JS)
        report = {}

        for name, url in (("channel", CHANNEL), ("home", HOME)):
            if name != "channel":
                await s.navigate(url)
            page = await poll(probe, lambda p: p["video_links"] > 0)
            report[name] = page
            print(f"\n[{name}] {page['url']}\n  video links {page['video_links']} ({page['unique_video_ids']} videos); "
                  f"items {page['items']}\n  logged in {page['cfg']['logged_in']}, auth cookies {page['auth_cookies_readable']}, "
                  f"save controls {page['save_controls']}", flush=True)

        # Hover a home thumbnail and see what is under the cursor once the inline preview starts.
        pt = await mk._run_script(s, THUMB_POINT_JS)
        if pt:
            before = await mk._run_script(s, AT_POINT_JS.replace("__X__", str(pt["x"])).replace("__Y__", str(pt["y"])))
            await mk._command(s, "hover", {"x": pt["x"], "y": pt["y"], "delay_after": 2500})
            after = await mk._run_script(s, AT_POINT_JS.replace("__X__", str(pt["x"])).replace("__Y__", str(pt["y"])))
            await mk._command(s, "hover", {"x": 2, "y": 2, "delay_after": 200})
            report["home_hover"] = {"point": pt, "before": before, "after_2_5s": after}
            print(f"\n[home hover] {pt['href']}\n  before: {before['chain'][:5]}\n  after:  {after['chain'][:5]} "
                  f"preview visible={after['preview_visible']} covers point={after['point_inside_preview']} "
                  f"link={after['preview_link']}", flush=True)

        await s.navigate(WATCH)
        page = await poll(probe, lambda p: p["video_links"] > 0 and p["watch"] and p["watch"]["video_id"])
        report["watch"] = page
        vid = page["watch"]["video_id"]
        print(f"\n[watch] {page['url']}\n  video {vid}, player {page['watch']['player_rect']}, sidebar links "
              f"{page['watch']['secondary_links']}; items {page['items']}\n  save controls {page['save_controls']}", flush=True)
        save("pages.json", report)

        # 1) YouTube's own request: which playlists exist, and is this video in each. Read-only.
        call = INNERTUBE_JS.replace("__PATH__", json.dumps("playlist/get_add_to_playlist")) \
                           .replace("__PAYLOAD__", json.dumps({"videoIds": [vid]}))
        res = await mk._run_script(s, call)
        save("get_add_to_playlist.json", res)
        entries = playlist_entries(res.get("json"))
        print(f"\n[request] get_add_to_playlist -> HTTP {res['status']} (auth {res['auth_schemes']}); "
              f"{len(entries)} playlist entries" + (f"; body: {res['text']}" if res.get("text") else ""), flush=True)
        for where in sorted({p for p, _ in entries}):
            print(f"  at {where}")
        for _, e in entries[:12]:
            title = text_of(e.get("title")) or text_of(e.get("playlistTitle"))
            print(f"  - {title!r:40} {e.get('playlistId')}  contains={e.get('containsSelectedVideos')}  keys={sorted(e)[:8]}")

        # 2) The Save dialog: open it once, read its rows and which request it made, close it. Read-only.
        btn = await mk._run_script(s, SAVE_BUTTON_JS)
        if not btn:
            print("\n[dialog] no visible Save button on the watch page (maybe in the overflow menu) - skipped")
        else:
            await s.click_cdp(btn["x"], btn["y"])
            dialog = await poll(lambda: mk._run_script(s, DIALOG_JS), lambda d: d["open"] and d["rows"], timeout=10)
            save("save_dialog.json", {"button": btn, **dialog})
            print(f"\n[dialog] button {btn['label']!r}/{btn['text']!r} -> open={dialog['open']} tag={dialog['dialog_tag']}\n"
                  f"  heading chain {dialog['heading_chain'][:8]}\n  youtubei calls seen: {dialog['youtubei_calls']}", flush=True)
            for r in dialog["rows"][:12]:
                print(f"  row {r['tag']:<36} {r['text']!r:42} label={r['label']!r} checked={r['checked']} pressed={r['pressed']}")
            await s.send_key("Escape")
            await asyncio.sleep(1)
            closed = await mk._run_script(s, DIALOG_JS)
            print(f"  closed after Escape: {not closed['open']}", flush=True)
        print(f"\nSaved to {OUT}")
        return 0
    finally:
        s.unbind()
        await agent.stop()


TEST_TITLE = "zz quick-save test"
TEST_VIDEO = "xxf6D9jynFw"

PLAYLIST_PAGE_JS = r"""
((vid) => {
  const items = [...document.querySelectorAll('ytd-playlist-video-renderer')];
  const text = document.body.innerText || '';
  return JSON.stringify({ url: location.href, items: items.length,
    matching: items.filter(it => it.querySelector(`a[href*="v=${vid}"]`)).length,
    empty_message: /no videos in this playlist/i.test(text) });
})(__VID__)
"""


async def innertube(s, path, payload):
    return await mk._run_script(s, INNERTUBE_JS.replace("__PATH__", json.dumps(path))
                                               .replace("__PAYLOAD__", json.dumps(payload)))


async def save_test():
    """Write test, ONLY on the private 'zz quick-save test' playlist: add-if-absent, guard, duplicate probe, cleanup."""
    agent = BrowserAgent()
    print("Waiting for AI Web-Auto in Chrome to connect on ws://localhost:8765 ...", flush=True)
    await agent.connect(timeout=90)
    s = agent.server
    log = []
    try:
        if "tab_id" not in s.capabilities:
            print("STOP: reload AI Web-Auto in chrome://extensions first.")
            return 2
        resp = await mk._command(s, "navigate", {"url": HOME, "new_tab": True}, timeout_ms=60000)
        s.bind(resp.data.tabId)
        print(f"opened its own tab {resp.data.tabId} (bound)", flush=True)

        async def state():
            res = await innertube(s, "playlist/get_add_to_playlist", {"videoIds": [TEST_VIDEO]})
            if res["status"] != 200:
                raise RuntimeError(f"get_add_to_playlist HTTP {res['status']}: {res.get('text')}")
            for p in res["json"]["contents"][0]["addToPlaylistRenderer"]["playlists"]:
                o = p["playlistAddToOptionRenderer"]
                if text_of(o["title"]) == TEST_TITLE:
                    return o
            raise RuntimeError(f"no playlist titled {TEST_TITLE!r}")

        async def send(endpoint, label):
            edit = endpoint["playlistEditEndpoint"]
            assert endpoint["commandMetadata"]["webCommandMetadata"]["apiUrl"] == "/youtubei/v1/browse/edit_playlist"
            res = await innertube(s, "browse/edit_playlist", {"playlistId": edit["playlistId"], "actions": edit["actions"]})
            status = (res.get("json") or {}).get("status")
            log.append({"request": label, "http": res["status"], "status": status})
            print(f"  -> {label}: HTTP {res['status']} {status}", flush=True)
            return res

        async def page_count():
            await s.navigate(f"https://www.youtube.com/playlist?list={pl['playlistId']}")
            js = PLAYLIST_PAGE_JS.replace("__VID__", json.dumps(TEST_VIDEO))
            got = await poll(lambda: mk._run_script(s, js), lambda r: r["items"] > 0 or r["empty_message"], timeout=20)
            return got

        pl = await state()
        if pl.get("privacy") != "PRIVATE":
            print(f"STOP: {TEST_TITLE!r} is {pl.get('privacy')}, not PRIVATE")
            return 1
        print(f"test playlist {pl['playlistId']} ({pl['privacy']}); video {TEST_VIDEO} contains={pl['containsSelectedVideos']}")
        if pl["containsSelectedVideos"] != "NONE":
            print("STOP: the test playlist already holds the test video; empty it first")
            return 1

        print("\n1) first save: check -> add -> re-check", flush=True)
        await send(pl["addToPlaylistServiceEndpoint"], "ACTION_ADD_VIDEO")
        pl = await state()
        print(f"  re-check contains={pl['containsSelectedVideos']}")
        first_ok = pl["containsSelectedVideos"] == "ALL"

        print("\n2) second save (Ctrl,Ctrl again): check finds it -> no request", flush=True)
        sent_before = len(log)
        pl = await state()
        if pl["containsSelectedVideos"] == "NONE":
            await send(pl["addToPlaylistServiceEndpoint"], "ACTION_ADD_VIDEO")
        guard_ok = len(log) == sent_before and pl["containsSelectedVideos"] == "ALL"
        print(f"  contains={pl['containsSelectedVideos']}, requests sent: {len(log) - sent_before}")

        print("\n3) YouTube's playlist page (independent check)", flush=True)
        page = await page_count()
        print(f"  {page['url']}: {page['items']} item(s), {page['matching']} of them the test video")
        page_ok = page["matching"] == 1

        print("\n4) raw duplicate add, bypassing the check (what the guard prevents)", flush=True)
        await send(pl["addToPlaylistServiceEndpoint"], "ACTION_ADD_VIDEO (duplicate)")
        dup = await page_count()
        print(f"  playlist page now: {dup['matching']} copies of the test video")

        print("\n5) cleanup: remove the test video until the test playlist is empty", flush=True)
        for _ in range(4):
            pl = await state()
            if pl["containsSelectedVideos"] == "NONE":
                break
            await send(pl["removeFromPlaylistServiceEndpoint"], "ACTION_REMOVE_VIDEO_BY_VIDEO_ID")
        final = await page_count()
        print(f"  contains={pl['containsSelectedVideos']}; playlist page: {final['items']} item(s), empty={final['empty_message']}")

        result = {"first_save_ok": first_ok, "guard_ok": guard_ok, "page_shows_one": page_ok,
                  "raw_duplicate_copies": dup["matching"], "cleaned": final["matching"] == 0, "requests": log}
        save("save_test.json", result)
        print(f"\n{json.dumps({k: v for k, v in result.items() if k != 'requests'})}")
        return 0 if first_ok and guard_ok and page_ok and result["cleaned"] else 1
    finally:
        s.unbind()
        await agent.stop()


def playlist_items(obj):
    """(videoId, setVideoId) of every playlist entry in a browse response, plus the token for the next
    page of *videos*: the continuationItemRenderer that sits in the same list as the videos (a page has
    another continuation for other sections, which returns nothing useful)."""
    items, token = [], None

    def token_in(o):
        if isinstance(o, dict):
            if isinstance(o.get("continuationCommand"), dict) and o["continuationCommand"].get("token"):
                return o["continuationCommand"]["token"]
            o = list(o.values())
        if isinstance(o, list):
            for v in o:
                t = token_in(v)
                if t:
                    return t
        return None

    def walk(o):
        nonlocal token
        if isinstance(o, list):
            if any(isinstance(v, dict) and "playlistVideoRenderer" in v for v in o):
                for v in o:
                    if not isinstance(v, dict):
                        continue
                    r = v.get("playlistVideoRenderer")
                    if r and "setVideoId" in r:
                        items.append((r["videoId"], r["setVideoId"]))
                    if "continuationItemRenderer" in v:
                        token = token_in(v["continuationItemRenderer"]) or token
                return
            for v in o:
                walk(v)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)

    walk(obj)
    return items, token


async def contents(s, playlist_id, max_pages=50):
    """The playlist's actual entries, read the way the playlist page reads them (all pages)."""
    res = await innertube(s, "browse", {"browseId": "VL" + playlist_id})
    items, token = playlist_items(res.get("json"))
    pages = 1
    while token and pages < max_pages:
        res = await innertube(s, "browse", {"continuation": token})
        more, token = playlist_items(res.get("json"))
        items += more
        pages += 1
    return items, pages


async def inspect_and_clean():
    """Compare the 'contains' flag with the real contents, then remove only the test video's copies."""
    agent = BrowserAgent()
    print("Waiting for AI Web-Auto in Chrome to connect on ws://localhost:8765 ...", flush=True)
    await agent.connect(timeout=90)
    s = agent.server
    try:
        if "tab_id" not in s.capabilities:
            print("STOP: reload AI Web-Auto in chrome://extensions first.")
            return 2
        resp = await mk._command(s, "navigate", {"url": HOME, "new_tab": True}, timeout_ms=60000)
        s.bind(resp.data.tabId)

        async def flag():
            res = await innertube(s, "playlist/get_add_to_playlist", {"videoIds": [TEST_VIDEO]})
            return next(p["playlistAddToOptionRenderer"] for p in res["json"]["contents"][0]["addToPlaylistRenderer"]["playlists"]
                        if text_of(p["playlistAddToOptionRenderer"]["title"]) == TEST_TITLE)

        pl = await flag()
        assert pl["privacy"] == "PRIVATE"
        pid = pl["playlistId"]
        items, pages = await contents(s, pid)
        copies = [sv for v, sv in items if v == TEST_VIDEO]
        print(f"now: flag contains={pl['containsSelectedVideos']}; real contents {len(items)} entries "
              f"({pages} page(s)), test video x{len(copies)}; others: {[v for v, _ in items if v != TEST_VIDEO]}", flush=True)

        print("\nremove the test video's copies (one request), then re-read the real contents", flush=True)
        res = await innertube(s, "browse/edit_playlist", {"playlistId": pid, "actions": [
            {"action": "ACTION_REMOVE_VIDEO_BY_VIDEO_ID", "removedVideoId": TEST_VIDEO}]})
        print(f"  -> remove by video id: HTTP {res['status']} {(res.get('json') or {}).get('status')}")
        items, _ = await contents(s, pid)
        left = [sv for v, sv in items if v == TEST_VIDEO]
        print(f"  copies left: {len(left)}")
        for sv in left:  # anything left: remove each entry by its own setVideoId
            res = await innertube(s, "browse/edit_playlist", {"playlistId": pid, "actions": [
                {"action": "ACTION_REMOVE_VIDEO", "setVideoId": sv}]})
            print(f"  -> remove entry {sv[:8]}...: HTTP {res['status']} {(res.get('json') or {}).get('status')}")
        items, _ = await contents(s, pid)
        print(f"after cleanup: real contents {len(items)} entries, test video x{sum(v == TEST_VIDEO for v, _ in items)}")

        print("\nflag over time after the change (does it catch up?)", flush=True)
        t0 = time.monotonic()
        seen = []
        while time.monotonic() - t0 < 45:
            seen.append((round(time.monotonic() - t0), (await flag())["containsSelectedVideos"]))
            await asyncio.sleep(5)
        print(f"  {seen}")
        return 0
    finally:
        s.unbind()
        await agent.stop()


async def record_fixtures():
    """Raw responses for the extension's parser tests (anonymised later by make_fixtures.py).

    Reads two pages of 'Watch Later 3' (read-only); on the test playlist only: add the test video,
    then remove that one entry by its setVideoId (the duplicate-repair path), and confirm it is clean.
    """
    agent = BrowserAgent()
    print("Waiting for AI Web-Auto in Chrome to connect on ws://localhost:8765 ...", flush=True)
    await agent.connect(timeout=90)
    s = agent.server
    raw = OUT / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    put = lambda name, data: (raw / name).write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    try:
        if "tab_id" not in s.capabilities:
            print("STOP: reload AI Web-Auto in chrome://extensions first.")
            return 2
        resp = await mk._command(s, "navigate", {"url": HOME, "new_tab": True}, timeout_ms=60000)
        s.bind(resp.data.tabId)
        lst = await innertube(s, "playlist/get_add_to_playlist", {"videoIds": [TEST_VIDEO]})
        put("get_add_to_playlist.json", lst["json"])
        opts = {text_of(p["playlistAddToOptionRenderer"]["title"]): p["playlistAddToOptionRenderer"]
                for p in lst["json"]["contents"][0]["addToPlaylistRenderer"]["playlists"]}
        big, test = opts["Watch Later 3"]["playlistId"], opts[TEST_TITLE]
        assert test["privacy"] == "PRIVATE"

        page1 = await innertube(s, "browse", {"browseId": "VL" + big})
        put("browse_page1.json", page1["json"])
        items, token = playlist_items(page1["json"])
        print(f"browse page 1: HTTP {page1['status']}, {len(items)} entries, continuation={'yes' if token else 'no'}")
        if token:
            page2 = await innertube(s, "browse", {"continuation": token})
            put("browse_page2.json", page2["json"])
            items2, token2 = playlist_items(page2["json"])
            print(f"browse page 2: HTTP {page2['status']}, {len(items2)} entries, continuation={'yes' if token2 else 'no'}")

        pid = test["playlistId"]
        before, _ = await contents(s, pid)
        if any(v == TEST_VIDEO for v, _ in before):
            print("STOP: test video already in the test playlist")
            return 1
        add = await innertube(s, "browse/edit_playlist", {"playlistId": pid, "actions": [
            {"addedVideoId": TEST_VIDEO, "action": "ACTION_ADD_VIDEO"}]})
        put("edit_add.json", add["json"])
        after, _ = await contents(s, pid)
        mine = [sv for v, sv in after if v == TEST_VIDEO]
        print(f"add: HTTP {add['status']} {add['json'].get('status')}; copies now {len(mine)}")
        rem = await innertube(s, "browse/edit_playlist", {"playlistId": pid, "actions": [
            {"action": "ACTION_REMOVE_VIDEO", "setVideoId": mine[0]}]})
        put("edit_remove_entry.json", rem["json"])
        final, _ = await contents(s, pid)
        print(f"remove entry by setVideoId: HTTP {rem['status']} {rem['json'].get('status')}; "
              f"copies now {sum(v == TEST_VIDEO for v, _ in final)}; others kept: {[v for v, _ in final]}")
        print(f"raw responses in {raw}")
        return 0
    finally:
        s.unbind()
        await agent.stop()


async def time_read():
    """How long a full read of a large playlist takes (read-only). Also records page 2 for the fixtures."""
    agent = BrowserAgent()
    print("Waiting for AI Web-Auto in Chrome to connect on ws://localhost:8765 ...", flush=True)
    await agent.connect(timeout=90)
    s = agent.server
    try:
        if "tab_id" not in s.capabilities:
            print("STOP: reload AI Web-Auto in chrome://extensions first.")
            return 2
        resp = await mk._command(s, "navigate", {"url": HOME, "new_tab": True}, timeout_ms=60000)
        s.bind(resp.data.tabId)
        lst = await innertube(s, "playlist/get_add_to_playlist", {"videoIds": [TEST_VIDEO]})
        big = next(p["playlistAddToOptionRenderer"]["playlistId"] for p in lst["json"]["contents"][0]["addToPlaylistRenderer"]["playlists"]
                   if text_of(p["playlistAddToOptionRenderer"]["title"]) == "Watch Later 3")
        for attempt in (1, 2):
            t0 = time.monotonic()
            res = await innertube(s, "browse", {"browseId": "VL" + big})
            items, token = playlist_items(res["json"])
            times, pages = [round(time.monotonic() - t0, 2)], 1
            while token:
                t1 = time.monotonic()
                res = await innertube(s, "browse", {"continuation": token})
                if pages == 1 and attempt == 1:
                    (OUT / "raw" / "browse_page2.json").write_text(json.dumps(res["json"], indent=1, ensure_ascii=False), encoding="utf-8")
                more, token = playlist_items(res["json"])
                items += more
                pages += 1
                times.append(round(time.monotonic() - t1, 2))
            total = time.monotonic() - t0
            print(f"read {attempt}: {len(items)} entries ({len({v for v, _ in items})} distinct) in {pages} pages, "
                  f"{total:.1f} s; per page {min(times)}-{max(times)} s", flush=True)
        return 0
    finally:
        s.unbind()
        await agent.stop()


OVERLAY_JS = r"""JSON.stringify((() => {
  const h = document.getElementById('yqs-overlay');
  if (!h) return null;
  const badge = h.shadowRoot && h.shadowRoot.querySelector('.badge');
  return { ...h.dataset, badge: badge ? badge.textContent : null };
})())"""

TILES_JS = r"""JSON.stringify((() => {
  const out = [];
  for (const tile of document.querySelectorAll('yt-lockup-view-model, ytm-shorts-lockup-view-model')) {
    const a = [...tile.querySelectorAll('a[href*="/watch?v="], a[href*="/shorts/"]')].find(x => x.querySelector('img, yt-thumbnail-view-model, yt-image'));
    if (!a) continue;
    const r = a.getBoundingClientRect();
    if (r.width < 80 || r.height < 60 || r.top < 70 || r.bottom > innerHeight - 10 || r.left < 0 || r.right > innerWidth) continue;
    const m = a.getAttribute('href').match(/[?&]v=([\w-]{11})|\/shorts\/([\w-]{11})/);
    if (m) out.push({ id: m[1] || m[2], kind: tile.tagName.toLowerCase(), x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
                      in_sidebar: !!tile.closest('#secondary') });
  }
  return out;
})())"""


async def live_check():
    """Step E: the installed extension, driven in its own bound tab; results checked on the test playlist."""
    agent = BrowserAgent()
    print("Waiting for AI Web-Auto in Chrome to connect on ws://localhost:8765 ...", flush=True)
    await agent.connect(timeout=90)
    s = agent.server
    rows = []
    try:
        if "tab_id" not in s.capabilities:
            print("STOP: reload AI Web-Auto in chrome://extensions first.")
            return 2
        resp = await mk._command(s, "navigate", {"url": HOME, "new_tab": True}, timeout_ms=60000)
        s.bind(resp.data.tabId)
        installed = await poll(lambda: mk._run_script(s, OVERLAY_JS), lambda o: o is not None, timeout=15)
        if installed is None:
            print("STOP: YouTube Quick Save is not running on this page (load it unpacked, then run again).")
            return 2
        lst = await innertube(s, "playlist/get_add_to_playlist", {"videoIds": [TEST_VIDEO]})
        test = next(p["playlistAddToOptionRenderer"] for p in lst["json"]["contents"][0]["addToPlaylistRenderer"]["playlists"]
                    if text_of(p["playlistAddToOptionRenderer"]["title"]) == TEST_TITLE)
        assert test["privacy"] == "PRIVATE"
        pid = test["playlistId"]
        before, _ = await contents(s, pid)
        before_sets = {sv for _, sv in before}
        print(f"test playlist {pid}: {len(before)} entr(ies) before", flush=True)

        async def hover(x, y, wait=0.4):
            await mk._command(s, "hover", {"x": x, "y": y, "delay_after": int(wait * 1000)})

        async def ctrl_ctrl():
            await mk._command(s, "key", {"key": "Control"})
            await asyncio.sleep(0.12)
            await mk._command(s, "key", {"key": "Control"})

        async def copies(vid):
            items, _ = await contents(s, pid)
            return sum(v == vid for v, _ in items)

        async def scenario(name, x, y, expect_id, expect_kind, expect_state, wait=0.4):
            await hover(x - 3, y - 3, 0.1)
            await hover(x, y, wait)
            o = await poll(lambda: mk._run_script(s, OVERLAY_JS), lambda o: o and o.get("videoId") == expect_id, timeout=5)
            seen = (o or {}).get("videoId"), (o or {}).get("kind")
            await ctrl_ctrl()
            done = await poll(lambda: mk._run_script(s, OVERLAY_JS),
                              lambda o: o and o.get("state") not in (None, "target", "saving"), timeout=20)
            n = await copies(expect_id)
            ok = seen[0] == expect_id and (expect_kind is None or seen[1] == expect_kind) and \
                (done or {}).get("state") == expect_state and n == 1
            rows.append({"scenario": name, "video": expect_id, "highlight": seen, "result": (done or {}).get("state"),
                         "toast": (done or {}).get("toast"), "copies": n, "ok": ok})
            print(f"  {'PASS' if ok else 'FAIL'} {name}: highlight {seen} -> {(done or {}).get('state')} "
                  f"({(done or {}).get('toast')}); copies in playlist: {n}", flush=True)

        async def tiles(kind=None, sidebar=None):
            found = await poll(lambda: mk._run_script(s, TILES_JS), lambda t: len(t) > 0, timeout=20)
            have = {v for v, _ in await contents(s, pid)}
            return [t for t in found or [] if t["id"] not in have and (kind is None or t["kind"] == kind)
                    and (sidebar is None or t["in_sidebar"] == sidebar)]

        print("\nHome", flush=True)
        home = await tiles("yt-lockup-view-model")
        await scenario("home tile", home[0]["x"], home[0]["y"], home[0]["id"], "tile", "saved")
        t2 = home[1]
        await hover(t2["x"], t2["y"], 2.8)  # YouTube's preview starts after ~2 s
        covered = await mk._run_script(s, f"JSON.stringify(!!document.elementFromPoint({t2['x']}, {t2['y']}).closest('ytd-video-preview'))")
        print(f"  (preview covering the thumbnail: {covered})")
        await scenario("home tile while its preview plays", t2["x"], t2["y"], t2["id"], "preview" if covered else None, "saved", wait=2.8)
        await scenario("same video again (double press)", home[0]["x"], home[0]["y"], home[0]["id"], None, "already")

        print("\nChannel page", flush=True)
        await s.navigate(CHANNEL)
        ch = await tiles("yt-lockup-view-model")
        await scenario("channel tile", ch[0]["x"], ch[0]["y"], ch[0]["id"], "tile", "saved")
        shorts = await tiles("ytm-shorts-lockup-view-model")
        if shorts:
            await scenario("channel Short", shorts[0]["x"], shorts[0]["y"], shorts[0]["id"], "tile", "saved")
        else:
            rows.append({"scenario": "channel Short", "ok": None, "note": "no Short visible without scrolling"})
            print("  SKIP channel Short: none visible without scrolling")

        print("\nWatch page", flush=True)
        await s.navigate(WATCH)
        page = await poll(lambda: mk._run_script(s, PAGE_PROBE_JS), lambda p: p["watch"] and p["watch"]["player_rect"], timeout=20)
        px, py, pw, ph = page["watch"]["player_rect"]
        if TEST_VIDEO not in {v for v, _ in await contents(s, pid)}:
            await scenario("main player", px + pw // 2, py + ph // 3, TEST_VIDEO, "player", "saved")
        side = await tiles(sidebar=True)
        await scenario("sidebar tile", side[0]["x"], side[0]["y"], side[0]["id"], "tile", "saved")

        print("\nIndependent check: YouTube's playlist page", flush=True)
        saved = [r["video"] for r in rows if r.get("ok") and r.get("result") == "saved"]
        await s.navigate(f"https://www.youtube.com/playlist?list={pid}")
        js = r"""JSON.stringify((ids => { const items = [...document.querySelectorAll('ytd-playlist-video-renderer')];
          return Object.fromEntries(ids.map(id => [id, items.filter(it => it.querySelector(`a[href*="v=${id}"]`)).length])); })(__IDS__))"""
        counts = await poll(lambda: mk._run_script(s, js.replace("__IDS__", json.dumps(saved))),
                            lambda c: c and all(n >= 1 for n in c.values()), timeout=20)
        print(f"  copies on the playlist page: {counts}")
        page_ok = bool(counts) and all(n == 1 for n in counts.values())

        print("\nCleanup: remove only the entries this check created", flush=True)
        items, _ = await contents(s, pid)
        for vid, sv in items:
            if sv not in before_sets:
                res = await innertube(s, "browse/edit_playlist", {"playlistId": pid, "actions": [{"action": "ACTION_REMOVE_VIDEO", "setVideoId": sv}]})
                print(f"  removed {vid}: {(res.get('json') or {}).get('status')}")
        after, _ = await contents(s, pid)
        clean = {sv for _, sv in after} == before_sets
        print(f"  test playlist back to its {len(before)} original entr(ies): {clean}")

        result = {"date": time.strftime("%Y-%m-%d %H:%M"), "scenarios": rows, "playlist_page_counts": counts,
                  "playlist_page_ok": page_ok, "cleaned": clean}
        save("live_check.json", result)
        passed = sum(1 for r in rows if r.get("ok"))
        print(f"\n{passed}/{len([r for r in rows if r.get('ok') is not None])} scenarios passed; playlist page shows one copy each: {page_ok}")
        return 0 if all(r.get("ok") is not False for r in rows) and page_ok and clean else 1
    finally:
        s.unbind()
        await agent.stop()


if __name__ == "__main__":
    commands = {"discover": discover, "save-test": save_test, "inspect-clean": inspect_and_clean,
                "record-fixtures": record_fixtures, "time-read": time_read, "live-check": live_check}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(commands[sys.argv[1]]()))
