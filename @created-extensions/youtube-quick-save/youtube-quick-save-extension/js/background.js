/**
 * YouTube Quick Save - background coordinator (service worker).
 *
 * Owns the settings, one save queue for all tabs, the local copy of the default playlist's video list,
 * the background check that repairs a duplicate the extension created, and the popup's tab actions.
 * It reaches YouTube only through a YouTube tab's page script -> bridge.js (the page's own session).
 */
importScripts('lib/core.js');
const C = self.YQSCore;

const MATCHES = chrome.runtime.getManifest().host_permissions;  // the YouTube origin (a test build swaps it)
const COPY_MAX_AGE_MS = 30 * 60 * 1000;
const RECENT_MS = 10 * 60 * 1000;
const CHECK_DELAY_MIN = 0.5;       // background check this long after the last save
const PROBE_VIDEO = 'jNQXAC9IVRw';  // any video lists the playlists; this is YouTube's first upload

const store = {
  async get(key, fallback) {
    const value = (await chrome.storage.local.get(key))[key];
    return value === undefined ? fallback : value;
  },
  set: (key, value) => chrome.storage.local.set({ [key]: value }),
};
const settings = () => store.get('settings', {});

// ── Reaching YouTube through a tab ──────────────────────────────────────────

async function ensureScripts(tabId) {
  try {
    const pong = await chrome.tabs.sendMessage(tabId, { type: 'ping' });
    if (pong && pong.ok) return;
  } catch (e) { /* not injected yet (tab opened before install / extension reloaded) */ }
  await chrome.scripting.executeScript({ target: { tabId }, files: ['js/page/bridge.js'], world: 'MAIN' });
  await chrome.scripting.executeScript({ target: { tabId }, files: ['js/lib/core.js', 'js/page/content.js'] });
}

async function youTubeTabs() {
  return chrome.tabs.query({ url: MATCHES });
}

async function workerTab() {
  const tabs = (await youTubeTabs()).filter(t => !t.discarded);
  if (!tabs.length) throw new Error('open youtube.com in a tab first');
  const tab = tabs.find(t => t.active && t.status === 'complete') || tabs.find(t => t.status === 'complete') || tabs[0];
  await ensureScripts(tab.id);
  return tab.id;
}

async function bridge(tabId, op, args = {}) {
  let reply;
  try {
    reply = await chrome.tabs.sendMessage(tabId, { type: 'bridge', op, args });
  } catch (e) {
    await ensureScripts(tabId);
    reply = await chrome.tabs.sendMessage(tabId, { type: 'bridge', op, args });
  }
  if (!reply || !reply.ok) throw new Error((reply && reply.error) || 'no answer from the YouTube tab');
  return reply.result;
}

// ── One queue: saves from every tab and the popup run one at a time ─────────

let chain = Promise.resolve();
function serial(fn) {
  const run = chain.then(fn, fn);
  chain = run.catch(() => {});
  return run;
}

// ── Activity log ────────────────────────────────────────────────────────────

async function log(entry) {
  const activity = await store.get('activity', []);
  activity.unshift({ at: Date.now(), ...entry });
  await store.set('activity', activity.slice(0, 40));
}

// ── Local copy of the default playlist's video list ─────────────────────────

let copyCache = null;   // {playlistId, ids: Set, entries, pages, readAt, ms, error}

async function loadCopy() {
  if (!copyCache) {
    const saved = await store.get('localCopy', null);
    copyCache = saved ? { ...saved, ids: new Set(saved.ids || []) } : null;
  }
  return copyCache;
}

async function saveCopy(copy) {
  copyCache = copy;
  await store.set('localCopy', { ...copy, ids: [...copy.ids] });
}

const copyHas = (copy, playlistId, videoId) => !!copy && copy.playlistId === playlistId && copy.ids.has(videoId);

let refreshing = null;
function refreshCopy() {
  if (refreshing) return refreshing;
  refreshing = (async () => {
    const s = await settings();
    if (!s.defaultId) return null;
    const tabId = await workerTab();
    const started = Date.now();
    try {
      const { entries, pages } = await C.readPlaylist(
        token => bridge(tabId, 'readPage', { playlistId: s.defaultId, token }), 100);
      const copy = { playlistId: s.defaultId, ids: new Set(entries.map(e => e.videoId)), entries: entries.length,
        pages, readAt: Date.now(), ms: Date.now() - started, error: null };
      await saveCopy(copy);
      await repair(s, entries, tabId, started);
      return copy;
    } catch (e) {
      const prev = await loadCopy();
      const base = prev && prev.playlistId === s.defaultId ? prev : { playlistId: s.defaultId, ids: new Set(), entries: 0, readAt: 0 };
      await saveCopy({ ...base, error: e.message, errorAt: Date.now() });
      throw e;
    }
  })().finally(() => { refreshing = null; });
  return refreshing;
}

/** After a full read: remove a duplicate only if the extension created it; report saves that vanished. */
async function repair(s, entries, tabId, readStarted) {
  const adds = await store.get('ourAdds', []);
  const settled = adds.filter(a => a.playlistId === s.defaultId && a.at < readStarted);
  const plan = C.planRepairs(entries, settled);
  for (const a of plan.remove) {
    try {
      C.parseEditStatus(await bridge(tabId, 'removeEntry', { playlistId: s.defaultId, setVideoId: a.setVideoId }));
      await log({ videoId: a.videoId, title: a.title, outcome: 'repaired', playlistTitle: s.defaultTitle,
        reason: 'it was also added elsewhere at the same moment; removed the extra copy this extension created' });
    } catch (e) {
      await log({ videoId: a.videoId, title: a.title, outcome: 'failed', playlistTitle: s.defaultTitle,
        reason: `could not remove the duplicate this extension created: ${e.message}` });
    }
  }
  for (const videoId of plan.missing) {
    const a = settled.find(x => x.videoId === videoId);
    await log({ videoId, title: a && a.title, outcome: 'gone', playlistTitle: s.defaultTitle,
      reason: 'saved earlier, no longer in the playlist (removed elsewhere?)' });
  }
  const keep = (await store.get('ourAdds', [])).filter(a => !settled.some(x => x.setVideoId === a.setVideoId));
  await store.set('ourAdds', keep);
}

function scheduleCheck() {
  chrome.alarms.create('yqs-check', { delayInMinutes: CHECK_DELAY_MIN });
}

// ── Saving ──────────────────────────────────────────────────────────────────

async function doSave(tabId, videoId, title) {
  const s = await settings();
  const copy = await loadCopy();
  const fresh = copy && copy.playlistId === s.defaultId && !copy.error && Date.now() - copy.readAt < COPY_MAX_AGE_MS;
  if (s.defaultId && !fresh && !refreshing) refreshCopy().catch(() => {});  // saves never wait for a full read
  const adds = await store.get('ourAdds', []);
  let res;
  try {
    res = await C.saveVideo({ videoId, playlistId: s.defaultId, account: s.account }, {
      account: () => bridge(tabId, 'account'),
      recentlyAdded: id => adds.some(a => a.videoId === id && a.playlistId === s.defaultId && Date.now() - a.at < RECENT_MS),
      inLocalCopy: id => copyHas(copy, s.defaultId, id),
      flag: async id => {
        const found = C.parsePlaylists(await bridge(tabId, 'listPlaylists', { videoId: id })).find(p => p.id === s.defaultId);
        return found ? found.contains : null;
      },
      add: id => bridge(tabId, 'addVideo', { playlistId: s.defaultId, videoId: id }),
    });
  } catch (e) {
    res = { outcome: 'failed', reason: e.message };
  }
  if (res.outcome === 'saved') {
    adds.push({ videoId, title, setVideoId: res.setVideoId, playlistId: s.defaultId, at: Date.now() });
    await store.set('ourAdds', adds);
    if (copy && copy.playlistId === s.defaultId) { copy.ids.add(videoId); await saveCopy(copy); }
    scheduleCheck();
  } else if (res.outcome === 'already' && res.via === 'youtube' && copy && copy.playlistId === s.defaultId) {
    copy.ids.add(videoId);
    await saveCopy(copy);
  }
  if (res.unknown) scheduleCheck();  // the add may or may not have landed: read, never re-send
  await log({ videoId, title, playlistTitle: s.defaultTitle, outcome: res.outcome, via: res.via, reason: res.reason });
  return { ...res, playlistTitle: s.defaultTitle };
}

// ── Popup actions ───────────────────────────────────────────────────────────

async function listPlaylists() {
  const tabId = await workerTab();
  const account = await bridge(tabId, 'account');
  if (!account.loggedIn) throw new Error('not signed in to YouTube');
  const tab = await chrome.tabs.get(tabId);
  const probe = C.videoIdFromUrl(tab.url) || PROBE_VIDEO;
  const playlists = C.parsePlaylists(await bridge(tabId, 'listPlaylists', { videoId: probe }))
    .map(({ id, title, privacy }) => ({ id, title, privacy }));
  const s = await settings();
  const next = { ...s, account: account.account, playlists, listedAt: Date.now() };
  const stillThere = s.account === account.account && playlists.find(p => p.id === s.defaultId);
  next.defaultId = stillThere ? stillThere.id : null;      // playlists belong to an account
  next.defaultTitle = stillThere ? stillThere.title : null;
  await store.set('settings', next);
  return next;
}

async function setDefault(playlistId) {
  const s = await settings();
  const chosen = (s.playlists || []).find(p => p.id === playlistId);
  if (!chosen) throw new Error('unknown playlist - refresh the list');
  await store.set('settings', { ...s, defaultId: chosen.id, defaultTitle: chosen.title });
  const copy = await loadCopy();
  if (!copy || copy.playlistId !== chosen.id) await saveCopy({ playlistId: chosen.id, ids: new Set(), entries: 0, readAt: 0, error: null });
  refreshCopy().catch(() => {});
  return { defaultId: chosen.id, defaultTitle: chosen.title };
}

function tabVideos(tabs) {
  return tabs.map(tab => ({ tab, videoId: C.videoIdFromUrl(tab.url) }));
}

async function saveAllTabs() {
  const s = await settings();
  if (!s.defaultId) throw new Error('pick a default playlist first');
  const tabs = tabVideos(await youTubeTabs());
  const worker = await workerTab();
  const byVideo = new Map();
  const results = [];
  for (const { tab, videoId } of tabs) {
    const title = (tab.title || '').replace(/ - YouTube$/, '');
    if (!videoId) { results.push({ tabId: tab.id, title, outcome: 'no-video' }); continue; }
    if (!byVideo.has(videoId)) byVideo.set(videoId, await doSave(worker, videoId, title));
    results.push({ tabId: tab.id, title, videoId, ...byVideo.get(videoId) });
  }
  return { results };
}

async function closeSavedTabs(dryRun) {
  const s = await settings();
  if (!s.defaultId) throw new Error('pick a default playlist first');
  const tabs = tabVideos(await youTubeTabs()).filter(t => t.videoId);
  const copy = await loadCopy();
  const adds = await store.get('ourAdds', []);
  const worker = tabs.length ? await workerTab() : null;
  const confirmed = new Map();
  for (const { videoId } of tabs) {
    if (confirmed.has(videoId)) continue;
    let inPlaylist = copyHas(copy, s.defaultId, videoId) || adds.some(a => a.videoId === videoId && a.playlistId === s.defaultId);
    if (!inPlaylist) {
      const found = C.parsePlaylists(await bridge(worker, 'listPlaylists', { videoId })).find(p => p.id === s.defaultId);
      inPlaylist = !!found && found.contains === 'ALL';
    }
    confirmed.set(videoId, inPlaylist);
  }
  const toClose = tabs.filter(t => confirmed.get(t.videoId)).map(t => t.tab);
  if (dryRun) return { count: toClose.length, titles: toClose.map(t => (t.title || '').replace(/ - YouTube$/, '')) };
  if (toClose.length) await chrome.tabs.remove(toClose.map(t => t.id));
  await log({ outcome: 'closed', reason: `closed ${toClose.length} tab(s) whose video is in ${s.defaultTitle}` });
  return { closed: toClose.length };
}

async function getState() {
  const s = await settings();
  const copy = await loadCopy();
  const tabs = tabVideos(await youTubeTabs());
  return {
    settings: s,
    copy: copy && copy.playlistId === s.defaultId
      ? { entries: copy.entries, distinct: copy.ids.size, readAt: copy.readAt, ms: copy.ms, error: copy.error || null, refreshing: !!refreshing }
      : { entries: 0, distinct: 0, readAt: 0, error: null, refreshing: !!refreshing },
    activity: (await store.get('activity', [])).slice(0, 15),
    youtubeTabs: tabs.length,
    videoTabs: tabs.filter(t => t.videoId).length,
  };
}

// ── Messages, alarms, install ───────────────────────────────────────────────

const POPUP_ACTIONS = {
  state: () => getState(),
  listPlaylists: () => serial(listPlaylists),
  setDefault: msg => setDefault(msg.playlistId),
  refreshCopy: () => refreshCopy(),
  checkNow: () => refreshCopy(),
  saveAllTabs: () => serial(saveAllTabs),
  closeSavedTabs: msg => serial(() => closeSavedTabs(!!msg.dryRun)),
};

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || sender.id !== chrome.runtime.id) return false;
  const fromExtensionPage = (sender.url || '').startsWith(chrome.runtime.getURL(''));
  let work;
  if (fromExtensionPage && POPUP_ACTIONS[msg.type]) {
    work = POPUP_ACTIONS[msg.type](msg);
  } else if (!fromExtensionPage && sender.tab) {
    // Page scripts may only ask to save the video under the cursor in their own tab.
    if (msg.type !== 'save' || !C.VIDEO_ID.test(msg.videoId || '')) return false;
    work = serial(() => doSave(sender.tab.id, msg.videoId, String(msg.title || '').slice(0, 200)));
  } else {
    return false;
  }
  Promise.resolve(work).then(sendResponse, e => sendResponse({ outcome: 'failed', error: e.message, reason: e.message }));
  return true;
});

chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === 'yqs-check' || alarm.name === 'yqs-refresh') {
    youTubeTabs().then(tabs => { if (tabs.length) refreshCopy().catch(() => {}); });
  }
});

chrome.runtime.onInstalled.addListener(async () => {
  chrome.alarms.create('yqs-refresh', { periodInMinutes: COPY_MAX_AGE_MS / 60000 });
  for (const tab of await youTubeTabs()) ensureScripts(tab.id).catch(() => {});  // works without reloading tabs
});
