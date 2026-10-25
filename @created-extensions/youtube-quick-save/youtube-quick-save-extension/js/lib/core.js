/**
 * YouTube Quick Save - core logic shared by the page script, the background and the unit tests.
 * Pure: no DOM, no chrome.* APIs. Loaded as a classic script (sets self.YQSCore) or required by Node.
 *
 * What it encodes was measured on YouTube (see PLAN.md and @modules/youtube/knowledge/lessons.md):
 * YouTube allows duplicate playlist entries, the "contains" flag lags behind changes, and a playlist's
 * real contents must be paged through the continuation at the end of its video list.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.YQSCore = api;
})(typeof self !== 'undefined' ? self : globalThis, function () {
  'use strict';

  // ── Video identity ──────────────────────────────────────────────────────

  const VIDEO_ID = /^[\w-]{11}$/;
  const PLAYLIST_ID = /^(WL|LL|[\w-]{10,64})$/;

  // Tile elements, newest first. Identity always comes from the tile's link, never its position.
  const TILE_SELECTOR = [
    'yt-lockup-view-model', 'ytm-shorts-lockup-view-model', 'ytm-shorts-lockup-view-model-v2',
    'ytd-rich-item-renderer', 'ytd-video-renderer', 'ytd-compact-video-renderer', 'ytd-grid-video-renderer',
    'ytd-playlist-video-renderer', 'ytd-playlist-panel-video-renderer', 'ytd-reel-item-renderer',
  ].join(', ');
  const VIDEO_LINK_SELECTOR = 'a[href*="/watch?v="], a[href*="/shorts/"]';

  /** The 11-character video ID in a watch / shorts / live / embed URL (absolute or relative), else null. */
  function videoIdFromUrl(href, base) {
    if (!href) return null;
    let u;
    try { u = new URL(href, base || 'https://www.youtube.com/'); } catch (e) { return null; }
    if (u.pathname === '/watch') {
      const v = u.searchParams.get('v');
      return v && VIDEO_ID.test(v) ? v : null;
    }
    const m = u.pathname.match(/^\/(?:shorts|live|embed)\/([\w-]{11})(?:[/?#]|$)/);
    return m ? m[1] : null;
  }

  // ── Choosing the target under / near the cursor ─────────────────────────

  function area(r) { return Math.max(0, r.right - r.left) * Math.max(0, r.bottom - r.top); }

  function distanceToRect(x, y, r) {
    const dx = x < r.left ? r.left - x : x > r.right ? x - r.right : 0;
    const dy = y < r.top ? r.top - y : y > r.bottom ? y - r.bottom : 0;
    return Math.hypot(dx, dy);
  }

  /**
   * candidates: [{id, rect: {left, top, right, bottom}, ...}]. The innermost candidate containing the point,
   * else the nearest one within maxDistance px, else null.
   */
  function pickTarget(x, y, candidates, maxDistance = 32) {
    let best = null;
    let bestKey = null;
    for (const c of candidates) {
      if (!c || !c.id || !c.rect || area(c.rect) === 0) continue;
      const d = distanceToRect(x, y, c.rect);
      if (d > maxDistance) continue;
      const key = [d, area(c.rect)];  // inside (0) beats near; then the smaller (inner) box
      if (!bestKey || key[0] < bestKey[0] || (key[0] === bestKey[0] && key[1] < bestKey[1])) {
        best = c;
        bestKey = key;
      }
    }
    return best;
  }

  // ── Ctrl, Ctrl ──────────────────────────────────────────────────────────

  /**
   * Two taps of Ctrl alone: the second press starts within windowMs of the first, and the gesture completes
   * when the second Ctrl is RELEASED with no other key pressed - so a Ctrl tap followed by Ctrl+C never
   * counts. Shift/Alt/Meta held, auto-repeat or focus in an editable field cancels.
   * ev: {key, repeat, altKey, shiftKey, metaKey, editable}. keyup() returns true when the gesture completes.
   */
  function createDoubleCtrl(windowMs = 400) {
    let state = 'idle';   // idle -> first -> released -> second -> (release) fire
    let firstAt = 0;
    const reset = () => { state = 'idle'; firstAt = 0; };
    return {
      keydown(ev, now) {
        if (ev.key !== 'Control' || ev.altKey || ev.shiftKey || ev.metaKey || ev.editable) { reset(); return false; }
        if (ev.repeat) return false;
        if (state === 'released' && now - firstAt <= windowMs) {
          state = 'second';
        } else {
          state = 'first';
          firstAt = now;
        }
        return false;
      },
      keyup(ev) {
        if (ev.key !== 'Control') return false;
        if (state === 'first') { state = 'released'; return false; }
        if (state === 'second') { reset(); return true; }
        return false;
      },
      reset,
      get state() { return state; },
    };
  }

  // ── YouTube replies ─────────────────────────────────────────────────────

  class UnexpectedReply extends Error {}

  function textOf(t) {
    if (!t) return '';
    if (typeof t === 'string') return t;
    return t.simpleText || t.content || (t.runs || []).map(r => r.text || '').join('');
  }

  /** playlist/get_add_to_playlist -> [{id, title, privacy, contains}] */
  function parsePlaylists(json) {
    const renderer = json && json.contents && json.contents[0] && json.contents[0].addToPlaylistRenderer;
    if (!renderer || !Array.isArray(renderer.playlists)) throw new UnexpectedReply('no addToPlaylistRenderer.playlists');
    return renderer.playlists.map(p => p && p.playlistAddToOptionRenderer).filter(Boolean).map(o => ({
      id: o.playlistId,
      title: textOf(o.title),
      privacy: o.privacy || null,
      contains: o.containsSelectedVideos || null,
    })).filter(p => p.id && PLAYLIST_ID.test(p.id));
  }

  function tokenIn(o) {
    if (Array.isArray(o)) { for (const v of o) { const t = tokenIn(v); if (t) return t; } return null; }
    if (o && typeof o === 'object') {
      if (o.continuationCommand && o.continuationCommand.token) return o.continuationCommand.token;
      for (const v of Object.values(o)) { const t = tokenIn(v); if (t) return t; }
    }
    return null;
  }

  /**
   * browse (VL<id> or a continuation) -> {entries: [{videoId, setVideoId}], continuation, found}.
   * The next-page token is the continuationItemRenderer in the same list as the videos; a page carries
   * another continuation for other sections, which must not be followed.
   */
  function parsePlaylistPage(json) {
    const out = { entries: [], continuation: null, found: false };
    const walk = (o) => {
      if (Array.isArray(o)) {
        if (o.some(v => v && v.playlistVideoRenderer)) {
          out.found = true;
          for (const v of o) {
            const r = v && v.playlistVideoRenderer;
            if (r && r.videoId && r.setVideoId) out.entries.push({ videoId: r.videoId, setVideoId: r.setVideoId });
            if (v && v.continuationItemRenderer) out.continuation = tokenIn(v.continuationItemRenderer) || out.continuation;
          }
          return;
        }
        o.forEach(walk);
      } else if (o && typeof o === 'object') {
        if (o.playlistVideoListRenderer) out.found = true;  // the list itself, even when the playlist is empty
        Object.values(o).forEach(walk);
      }
    };
    walk(json);
    return out;
  }

  /** Every entry of a playlist. fetchPage(token|null) -> reply. Throws if the first page has no video list. */
  async function readPlaylist(fetchPage, maxPages = 100) {
    const first = parsePlaylistPage(await fetchPage(null));
    if (!first.found) throw new UnexpectedReply('playlist reply has no video list');
    const entries = first.entries.slice();
    let token = first.continuation;
    let pages = 1;
    while (token) {
      if (pages >= maxPages) throw new UnexpectedReply(`more than ${maxPages} pages`);
      const page = parsePlaylistPage(await fetchPage(token));
      entries.push(...page.entries);
      token = page.continuation;
      pages += 1;
    }
    return { entries, pages };
  }

  /** browse/edit_playlist add reply -> {setVideoId}; throws unless YouTube confirms this video was added. */
  function parseAddResult(json, videoId) {
    if (!json || json.status !== 'STATUS_SUCCEEDED') throw new UnexpectedReply(`add status ${json && json.status}`);
    const added = (json.playlistEditResults || []).map(r => r && r.playlistEditVideoAddedResultData).filter(Boolean);
    const mine = added.find(a => a.videoId === videoId);
    if (!mine || !mine.setVideoId) throw new UnexpectedReply('add reply does not confirm this video');
    return { setVideoId: mine.setVideoId };
  }

  function parseEditStatus(json) {
    if (!json || json.status !== 'STATUS_SUCCEEDED') throw new UnexpectedReply(`edit status ${json && json.status}`);
    return true;
  }

  // ── Saving ──────────────────────────────────────────────────────────────

  /**
   * Add videoId to playlistId at most once. deps:
   *   account()            -> {loggedIn, account}
   *   recentlyAdded(id)    -> bool   (the extension added it moments ago)
   *   inLocalCopy(id)      -> bool   (local copy of the playlist's video list)
   *   flag(id)             -> 'ALL' | 'NONE' | 'SOME' | null (null: playlist not found)
   *   add(id)              -> raw add reply
   * Never retries an add: if its outcome is unknown the caller reads the playlist instead.
   */
  async function saveVideo({ videoId, playlistId, account }, deps) {
    if (!VIDEO_ID.test(videoId || '')) return { outcome: 'failed', reason: 'no video here' };
    if (!PLAYLIST_ID.test(playlistId || '')) return { outcome: 'no-playlist', reason: 'no default playlist set' };
    const acct = await deps.account();
    if (!acct || !acct.loggedIn) return { outcome: 'signed-out', reason: 'not signed in to YouTube' };
    if (account && acct.account !== account) return { outcome: 'wrong-account', reason: 'signed in to a different account than the default playlist belongs to' };
    if (deps.recentlyAdded(videoId)) return { outcome: 'already', via: 'recent' };
    if (deps.inLocalCopy(videoId)) return { outcome: 'already', via: 'local copy' };
    const flag = await deps.flag(videoId);
    if (flag === null) return { outcome: 'no-playlist', reason: 'the default playlist was not found on this account' };
    if (flag !== 'NONE') return { outcome: 'already', via: 'youtube' };
    let reply;
    try {
      reply = await deps.add(videoId);
    } catch (e) {
      return { outcome: 'failed', reason: `add request failed: ${e.message}`, unknown: true };
    }
    try {
      const { setVideoId } = parseAddResult(reply, videoId);
      return { outcome: 'saved', setVideoId };
    } catch (e) {
      return { outcome: 'failed', reason: `YouTube did not confirm the add: ${e.message}`, unknown: true };
    }
  }

  /**
   * After a full read: which of the extension's own recent adds now appear twice (remove ours - the entry
   * we created - never the other copy) and which are gone. Duplicates the extension didn't create are left alone.
   * ourAdds: [{videoId, setVideoId}]
   */
  function planRepairs(entries, ourAdds) {
    const remove = [];
    const missing = [];
    for (const add of ourAdds) {
      const copies = entries.filter(e => e.videoId === add.videoId);
      if (copies.length === 0) missing.push(add.videoId);
      else if (copies.length > 1 && copies.some(c => c.setVideoId === add.setVideoId)) remove.push(add);
    }
    return { remove, missing };
  }

  return {
    VIDEO_ID, PLAYLIST_ID, TILE_SELECTOR, VIDEO_LINK_SELECTOR, UnexpectedReply,
    videoIdFromUrl, distanceToRect, pickTarget, createDoubleCtrl,
    textOf, parsePlaylists, parsePlaylistPage, readPlaylist, parseAddResult, parseEditStatus,
    saveVideo, planRepairs,
  };
});
