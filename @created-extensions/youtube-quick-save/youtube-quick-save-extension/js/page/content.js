/**
 * YouTube Quick Save - page script (isolated world).
 *
 * Follows the cursor and highlights the video under or nearest it (tiles, Shorts, the hover preview, the
 * main player); Ctrl, Ctrl (or a click on the highlight's badge) asks the background to save it and
 * shows the result. Also relays the
 * background's YouTube requests to the page-context bridge (bridge.js).
 */
(() => {
  // A previous copy (extension reloaded, or injected twice) steps aside for this one.
  document.dispatchEvent(new CustomEvent('yqs-teardown'));
  const C = self.YQSCore;
  const cleanups = [];
  const on = (target, type, fn, opts) => {
    target.addEventListener(type, fn, opts);
    cleanups.push(() => target.removeEventListener(type, fn, opts));
  };

  // ── Bridge client (requests from the background, answered by bridge.js) ──

  const pending = new Map();
  on(window, 'message', event => {
    const msg = event.data;
    if (event.source !== window || !msg || msg.yqs !== 'reply' || !pending.has(msg.id)) return;
    const { resolve, timer } = pending.get(msg.id);
    pending.delete(msg.id);
    clearTimeout(timer);
    resolve(msg);
  });

  function callBridge(op, args, timeoutMs = 30000) {
    return new Promise(resolve => {
      const id = crypto.randomUUID();
      const timer = setTimeout(() => {
        pending.delete(id);
        resolve({ ok: false, error: 'the YouTube page did not answer (reload it)' });
      }, timeoutMs);
      pending.set(id, { resolve, timer });
      window.postMessage({ yqs: 'request', id, op, args }, location.origin);
    });
  }

  const onRuntimeMessage = (msg, sender, sendResponse) => {
    if (msg && msg.type === 'bridge') {
      callBridge(msg.op, msg.args).then(sendResponse);
      return true;
    }
    if (msg && msg.type === 'ping') sendResponse({ ok: true, url: location.href });
    return false;
  };
  chrome.runtime.onMessage.addListener(onRuntimeMessage);
  cleanups.push(() => chrome.runtime.onMessage.removeListener(onRuntimeMessage));

  // ── Settings shown in the badge ──

  let playlistTitle = null;
  const readSettings = s => { playlistTitle = (s && s.defaultId && s.defaultTitle) || null; };
  chrome.storage.local.get('settings').then(r => { readSettings(r.settings); schedule(); }).catch(() => {});
  const onStorage = (changes, area) => {
    if (area === 'local' && changes.settings) { readSettings(changes.settings.newValue); schedule(); }
  };
  chrome.storage.onChanged.addListener(onStorage);
  cleanups.push(() => chrome.storage.onChanged.removeListener(onStorage));

  // ── Overlay: drawn in the top layer, never blocks the page ──

  const host = document.createElement('div');
  host.id = 'yqs-overlay';
  host.setAttribute('popover', 'manual');
  host.style.cssText = 'position:fixed;inset:0;width:auto;height:auto;margin:0;padding:0;border:0;' +
    'background:transparent;overflow:visible;pointer-events:none;color-scheme:normal;';
  const shadow = host.attachShadow({ mode: 'open' });
  shadow.innerHTML = `<style>
    /* Hairline frame + corner brackets; colour changes fade, position never animates. */
    .box { position: fixed; box-sizing: border-box; display: none; border-radius: 12px;
           border: 1px solid color-mix(in srgb, var(--c) 30%, transparent);
           box-shadow: 0 0 22px -8px var(--c); transition: border-color .2s, box-shadow .2s; }
    .box::before { content: ''; position: absolute; inset: -1px; border: 1.5px solid var(--c); border-radius: inherit;
           --m: linear-gradient(#000 0 0) no-repeat;
           mask: var(--m) 0 0 / 16px 16px, var(--m) 100% 0 / 16px 16px,
                 var(--m) 0 100% / 16px 16px, var(--m) 100% 100% / 16px 16px; transition: border-color .2s; }
    .box[data-tone="busy"]::before { animation: yqs-pulse 1.1s ease-in-out infinite; }
    @keyframes yqs-pulse { 50% { opacity: .35; } }
    .badge { position: fixed; display: none; max-width: 320px; margin: 0; padding: 3px 9px 3px 20px; border-radius: 6px;
             font: 500 11px/1.4 Roboto, Arial, sans-serif; letter-spacing: .03em; color: rgba(255,255,255,.9);
             background: rgba(10,10,12,.62); backdrop-filter: blur(8px); appearance: none; text-align: left;
             border: 1px solid color-mix(in srgb, var(--c) 28%, transparent); pointer-events: none;
             white-space: nowrap; overflow: hidden; text-overflow: ellipsis; transition: background .15s, border-color .15s; }
    .badge::before { content: ''; position: absolute; left: 8px; top: 50%; width: 5px; height: 5px; margin-top: -2.5px;
             border-radius: 50%; background: var(--c); box-shadow: 0 0 6px var(--c); }
    /* Ready: the badge is a button - click it instead of Ctrl, Ctrl. */
    .badge.ready { pointer-events: auto; cursor: pointer; }
    .badge.ready:hover { background: rgba(10,10,12,.82); border-color: color-mix(in srgb, var(--c) 70%, transparent); }
    .key { display: inline-block; margin-right: 6px; padding: 0 5px; border-radius: 3px;
           font: 600 10px/1.5 ui-monospace, Consolas, monospace; letter-spacing: .04em; color: var(--c);
           border: 1px solid color-mix(in srgb, var(--c) 45%, transparent); transition: background .15s, color .15s; }
    .key[hidden] { display: none; }
    .badge.ready:hover .key { background: var(--c); color: #0a0a0c; }
    .toast { position: fixed; left: 50%; bottom: 28px; transform: translateX(-50%); display: none; max-width: 70vw;
             padding: 10px 16px; border-radius: 10px; border-left: 4px solid var(--c);
             font: 500 14px/1.4 Roboto, Arial, sans-serif; color: #fff; background: rgba(28,28,28,.96);
             box-shadow: 0 4px 16px rgba(0,0,0,.5); }
    [data-tone="info"] { --c: #3ea6ff; } [data-tone="busy"] { --c: #aaaaaa; } [data-tone="ok"] { --c: #2ba640; }
    [data-tone="warn"] { --c: #f2b600; } [data-tone="bad"] { --c: #ff4e45; }
  </style><div class="box" data-tone="info"></div><button type="button" class="badge" data-tone="info" tabindex="-1"
    title="Click to save (or press Ctrl twice)"><span class="key">Ctrl Ctrl</span><span class="label"></span></button>
    <div class="toast" data-tone="info"></div>`;
  const box = shadow.querySelector('.box');
  const badge = shadow.querySelector('.badge');
  const badgeKey = badge.querySelector('.key');
  const badgeLabel = badge.querySelector('.label');
  const toastEl = shadow.querySelector('.toast');

  function showHost() {
    if (!host.isConnected) document.documentElement.appendChild(host);
    try {
      if (host.matches(':popover-open')) host.hidePopover();
      host.showPopover();
    } catch (e) {
      host.style.zIndex = '2147483647';  // no popover support: plain fixed layer
    }
  }
  showHost();
  on(document, 'fullscreenchange', showHost);  // stay above a fullscreen player
  cleanups.push(() => host.remove());

  let toastTimer = 0;
  function toast(text, tone) {
    toastEl.textContent = text;
    toastEl.dataset.tone = tone;
    toastEl.style.display = 'block';
    host.dataset.toast = text;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.style.display = 'none'; delete host.dataset.toast; }, 2800);
  }

  // ── Finding the video under / near the cursor ──

  const rectOf = el => { const r = el.getBoundingClientRect(); return { left: r.left, top: r.top, right: r.right, bottom: r.bottom }; };
  const area = r => Math.max(0, r.right - r.left) * Math.max(0, r.bottom - r.top);
  const onScreen = r => r.right - r.left > 4 && r.bottom - r.top > 4 && r.bottom > 0 && r.right > 0 &&
    r.top < innerHeight && r.left < innerWidth;

  function linkIn(root) {
    for (const a of root.querySelectorAll(C.VIDEO_LINK_SELECTOR)) {
      const id = C.videoIdFromUrl(a.getAttribute('href'), location.href);
      if (id) return { id, a };
    }
    return null;
  }

  function thumbRect(tile) {
    let best = null;
    for (const a of tile.querySelectorAll(C.VIDEO_LINK_SELECTOR)) {
      if (!a.querySelector('img, yt-thumbnail-view-model, yt-image, video')) continue;
      const r = rectOf(a);
      if (onScreen(r) && (!best || area(r) > area(best))) best = r;
    }
    return best || rectOf(tile);
  }

  function titleOf(root) {
    const el = root.querySelector('h3, #video-title, a[title], [aria-label]');
    const text = el ? (el.getAttribute('title') || el.textContent || el.getAttribute('aria-label') || '') : '';
    return text.trim().replace(/\s+/g, ' ').slice(0, 120);
  }

  function playingVideoId() {
    const flexy = document.querySelector('ytd-watch-flexy[video-id]');
    return (location.pathname === '/watch' && flexy && flexy.getAttribute('video-id')) || C.videoIdFromUrl(location.href);
  }

  function resolveAt(x, y) {
    for (const el of document.elementsFromPoint(x, y)) {
      if (el === host) continue;
      const preview = el.closest('ytd-video-preview');
      if (preview) {
        const hit = linkIn(preview);
        if (hit) return { id: hit.id, rect: rectOf(preview), kind: 'preview', title: titleOf(preview) };
      }
      const tile = el.closest(C.TILE_SELECTOR);
      if (tile) {
        const hit = linkIn(tile);
        if (hit) return { id: hit.id, rect: thumbRect(tile), kind: 'tile', title: titleOf(tile) };
      }
      const player = el.closest('#movie_player, ytd-reel-video-renderer, #shorts-player');
      if (player) {
        const id = playingVideoId();
        if (id) return { id, rect: rectOf(player), kind: 'player', title: document.title.replace(/ - YouTube$/, '') };
      }
    }
    // Not over a video: the nearest tile within reach.
    const near = [];
    for (const tile of document.querySelectorAll(C.TILE_SELECTOR)) {
      const r = rectOf(tile);
      if (!onScreen(r) || C.distanceToRect(x, y, r) > 32) continue;
      const hit = linkIn(tile);
      if (hit) near.push({ id: hit.id, rect: r, tile });
    }
    const pick = C.pickTarget(x, y, near, 32);
    return pick ? { id: pick.id, rect: thumbRect(pick.tile), kind: 'near', title: titleOf(pick.tile) } : null;
  }

  // ── Drawing ──

  let mx = -1;
  let my = -1;
  let frame = 0;
  let target = null;
  let overBadge = false;
  const flashes = new Map();  // videoId -> {text, tone, state, until}

  function schedule() {
    if (!frame) frame = requestAnimationFrame(update);
  }

  // On the badge the target stays put, even where the badge overhangs something else (clamped to the edge).
  function resolveCurrent() {
    const t = mx < 0 ? null : resolveAt(mx, my);
    return overBadge && target && (!t || t.id !== target.id) ? target : t;
  }

  function update() {
    frame = 0;
    target = resolveCurrent();
    draw();
  }

  function draw() {
    if (!target) {
      box.style.display = 'none';
      badge.style.display = 'none';
      host.dataset.state = 'none';
      delete host.dataset.videoId;
      return;
    }
    const r = target.rect;
    const flash = flashes.get(target.id);
    const live = flash && (flash.until === 0 || flash.until > Date.now()) ? flash : null;
    const tone = live ? live.tone : 'info';
    const ready = !live && !!playlistTitle;
    const text = live ? live.text : ready ? `→ ${playlistTitle}` : 'Pick a default playlist in the extension';
    Object.assign(box.style, { display: 'block', left: `${r.left}px`, top: `${r.top}px`,
      width: `${r.right - r.left}px`, height: `${r.bottom - r.top}px` });
    box.dataset.tone = tone;
    badge.dataset.tone = tone;
    badge.classList.toggle('ready', ready);
    badgeKey.hidden = !ready;
    badgeLabel.textContent = text;
    Object.assign(badge.style, { display: 'block', left: `${Math.max(4, r.left + 8)}px`,
      top: `${Math.min(innerHeight - 28, Math.max(4, r.top + 8))}px` });
    host.dataset.state = live ? live.state : 'target';
    host.dataset.videoId = target.id;
    host.dataset.kind = target.kind;
  }

  function flash(videoId, text, tone, state, ms) {
    flashes.set(videoId, { text, tone, state, until: ms ? Date.now() + ms : 0 });
    draw();
    if (ms) setTimeout(() => { if (flashes.get(videoId) && flashes.get(videoId).until <= Date.now()) { flashes.delete(videoId); draw(); } }, ms + 20);
  }

  on(window, 'mousemove', e => {
    mx = e.clientX; my = e.clientY;
    overBadge = e.composedPath().includes(badge);
    schedule();
  }, { capture: true, passive: true });

  // The badge button: its presses never reach YouTube and never take focus from the page.
  const swallow = e => { e.stopPropagation(); e.preventDefault(); };
  for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'dblclick']) on(badge, type, swallow);
  on(badge, 'click', e => { swallow(e); if (badge.classList.contains('ready')) trigger(); });
  on(document, 'mouseout', e => { if (!e.relatedTarget) { mx = -1; schedule(); } }, true);
  on(window, 'scroll', schedule, { capture: true, passive: true });
  on(window, 'resize', schedule, { passive: true });
  on(document, 'yt-navigate-finish', schedule);

  // ── Ctrl, Ctrl ──

  const detector = C.createDoubleCtrl(400);
  function typing() {
    let el = document.activeElement;
    while (el && el.shadowRoot && el.shadowRoot.activeElement) el = el.shadowRoot.activeElement;
    return !!el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
  }
  const keyInfo = e => ({ key: e.key, repeat: e.repeat, altKey: e.altKey, shiftKey: e.shiftKey, metaKey: e.metaKey, editable: typing() });
  on(window, 'keydown', e => detector.keydown(keyInfo(e), performance.now()), true);
  on(window, 'keyup', e => { if (detector.keyup(keyInfo(e), performance.now())) trigger(); }, true);
  on(window, 'blur', () => detector.reset());

  const inFlight = new Set();
  const MESSAGES = {
    saved: (r, pl) => [`Saved to ${pl}`, 'ok'],
    already: (r, pl) => [`Already in ${pl}`, 'warn'],
    'no-playlist': r => [`Not saved: ${r.reason || 'pick a default playlist in the extension'}`, 'bad'],
    'signed-out': r => [`Not saved: ${r.reason}`, 'bad'],
    'wrong-account': r => [`Not saved: ${r.reason}`, 'bad'],
    failed: r => [`Not saved: ${r.reason || r.error || 'unknown error'}`, 'bad'],
  };

  async function trigger() {
    if (mx >= 0) target = resolveCurrent();
    const t = target;
    if (!t) { toast('Point at a video, then press Ctrl, Ctrl', 'warn'); return; }
    if (inFlight.has(t.id)) return;
    inFlight.add(t.id);
    flash(t.id, 'Saving…', 'busy', 'saving', 0);
    let res;
    try {
      res = await chrome.runtime.sendMessage({ type: 'save', videoId: t.id, title: t.title });
    } catch (e) {
      res = { outcome: 'failed', reason: /context invalidated/i.test(e.message) ? 'the extension was updated - reload this page' : e.message };
    } finally {
      inFlight.delete(t.id);
    }
    res = res || { outcome: 'failed', reason: 'no answer from the extension' };
    const [text, tone] = (MESSAGES[res.outcome] || MESSAGES.failed)(res, res.playlistTitle || playlistTitle || 'the playlist');
    flash(t.id, text, tone, res.outcome, 2600);
    toast(text, tone);
  }

  on(document, 'yqs-teardown', () => { cleanups.splice(0).forEach(fn => { try { fn(); } catch (e) {} }); });
})();
