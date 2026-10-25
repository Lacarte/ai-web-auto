/**
 * YouTube Quick Save - page-context bridge (MAIN world).
 *
 * The only part that talks to YouTube. It signs requests the way YouTube's web client does (session
 * cookies + SAPISID hash headers, ytcfg context) and allows exactly five operations; anything else is
 * refused. Requests arrive from the page script (isolated world) by window.postMessage.
 */
(() => {
  if (window.__yqsBridge) return;
  window.__yqsBridge = true;

  const VIDEO_ID = /^[\w-]{11}$/;
  const PLAYLIST_ID = /^(WL|LL|[\w-]{10,64})$/;
  const SET_VIDEO_ID = /^[\w-]{4,64}$/;
  const TOKEN = /^[\w%=+/-]{8,8192}$/;

  const cfg = () => (window.ytcfg && window.ytcfg.data_) || {};
  const cookie = name => {
    const hit = document.cookie.split('; ').find(c => c.startsWith(name + '='));
    return hit ? hit.slice(name.length + 1) : '';
  };
  const sha1 = async text => [...new Uint8Array(await crypto.subtle.digest('SHA-1', new TextEncoder().encode(text)))]
    .map(b => b.toString(16).padStart(2, '0')).join('');

  async function headers(c) {
    const ts = Math.floor(Date.now() / 1000);
    const origin = location.origin;
    const auth = [];
    for (const [name, scheme] of [['SAPISID', 'SAPISIDHASH'], ['__Secure-1PAPISID', 'SAPISID1PHASH'], ['__Secure-3PAPISID', 'SAPISID3PHASH']]) {
      const value = cookie(name);
      if (value) auth.push(`${scheme} ${ts}_${await sha1(`${ts} ${value} ${origin}`)}`);
    }
    const h = {
      'Content-Type': 'application/json',
      'X-Origin': origin,
      'X-Goog-AuthUser': String(c.SESSION_INDEX ?? 0),
      'X-Youtube-Client-Name': String(c.INNERTUBE_CONTEXT_CLIENT_NAME ?? 1),
      'X-Youtube-Client-Version': String(c.INNERTUBE_CLIENT_VERSION || ''),
    };
    if (auth.length) h.Authorization = auth.join(' ');
    if (c.DELEGATED_SESSION_ID) h['X-Goog-PageId'] = c.DELEGATED_SESSION_ID;
    return h;
  }

  async function post(path, payload) {
    const c = cfg();
    if (!c.INNERTUBE_CONTEXT) throw new Error('YouTube page is not ready (no ytcfg)');
    const res = await fetch(`/youtubei/v1/${path}?prettyPrint=false`, {
      method: 'POST',
      credentials: 'include',
      headers: await headers(c),
      body: JSON.stringify({ context: c.INNERTUBE_CONTEXT, ...payload }),
    });
    if (!res.ok) throw new Error(`YouTube answered HTTP ${res.status}`);
    return res.json();
  }

  const need = (ok, what) => { if (!ok) throw new Error(`invalid ${what}`); };

  const OPS = {
    account: async () => {
      const c = cfg();
      return { loggedIn: !!c.LOGGED_IN, account: c.DATASYNC_ID || c.DELEGATED_SESSION_ID || null };
    },
    listPlaylists: async ({ videoId }) => {
      need(VIDEO_ID.test(videoId || ''), 'video ID');
      return post('playlist/get_add_to_playlist', { videoIds: [videoId] });
    },
    readPage: async ({ playlistId, token }) => {
      if (token) {
        need(TOKEN.test(token), 'continuation');
        return post('browse', { continuation: token });
      }
      need(PLAYLIST_ID.test(playlistId || ''), 'playlist ID');
      return post('browse', { browseId: 'VL' + playlistId });
    },
    addVideo: async ({ playlistId, videoId }) => {
      need(PLAYLIST_ID.test(playlistId || '') && VIDEO_ID.test(videoId || ''), 'add');
      return post('browse/edit_playlist', { playlistId, actions: [{ addedVideoId: videoId, action: 'ACTION_ADD_VIDEO' }] });
    },
    // Duplicate repair only: removes exactly one entry. Remove-by-video (every copy) is deliberately absent.
    removeEntry: async ({ playlistId, setVideoId }) => {
      need(PLAYLIST_ID.test(playlistId || '') && SET_VIDEO_ID.test(setVideoId || ''), 'remove');
      return post('browse/edit_playlist', { playlistId, actions: [{ action: 'ACTION_REMOVE_VIDEO', setVideoId }] });
    },
  };

  window.addEventListener('message', async event => {
    const msg = event.data;
    if (event.source !== window || !msg || msg.yqs !== 'request' || typeof msg.id !== 'string') return;
    let reply;
    try {
      if (!Object.prototype.hasOwnProperty.call(OPS, msg.op)) throw new Error(`unknown operation ${msg.op}`);
      reply = { ok: true, result: await OPS[msg.op](msg.args || {}) };
    } catch (err) {
      reply = { ok: false, error: String((err && err.message) || err) };
    }
    window.postMessage({ yqs: 'reply', id: msg.id, ...reply }, location.origin);
  });
})();
