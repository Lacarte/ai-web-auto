/** YouTube Quick Save - popup: default playlist, local copy status, tab actions, recent activity. */
const $ = selector => document.querySelector(selector);
const send = (type, extra = {}) => chrome.runtime.sendMessage({ type, ...extra });

const LABEL = {
  saved: 'Saved', already: 'Already there', failed: 'Failed', 'no-playlist': 'No playlist',
  'signed-out': 'Signed out', 'wrong-account': 'Other account', repaired: 'Fixed duplicate',
  gone: 'Gone', closed: 'Closed tabs', 'no-video': 'No video',
};
let busy = false;

function ago(ts) {
  const s = Math.round((Date.now() - ts) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}

function listItem(outcome, text, detail) {
  const li = document.createElement('li');
  const what = document.createElement('span');
  what.className = outcome;
  what.textContent = LABEL[outcome] || outcome;
  const desc = document.createElement('span');
  desc.className = 'what';
  desc.textContent = text;
  desc.title = [text, detail].filter(Boolean).join('\n');
  li.append(what, desc);
  return li;
}

function message(text, bad = false) {
  const el = $('#message');
  el.hidden = !text;
  el.textContent = text || '';
  el.classList.toggle('bad', bad);
}

async function render() {
  const st = await send('state');
  if (!st || !st.settings) return;
  const s = st.settings;

  const select = $('#playlist');
  const options = (s.playlists || []).map(p => {
    const o = document.createElement('option');
    o.value = p.id;
    o.textContent = `${p.title}${p.privacy && p.privacy !== 'PRIVATE' ? ` (${p.privacy.toLowerCase()})` : ''}`;
    return o;
  });
  const first = document.createElement('option');
  first.value = '';
  first.textContent = options.length ? 'Choose a playlist' : 'Refresh the list first';
  if (document.activeElement !== select) {
    select.replaceChildren(first, ...options);
    select.value = s.defaultId || '';
  }

  $('#account').textContent = s.listedAt ? `${(s.playlists || []).length} playlists, listed ${ago(s.listedAt)}` : '';
  const c = st.copy;
  const copy = $('#copy');
  copy.classList.toggle('bad', !!c.error);
  copy.textContent = !s.defaultId ? ''
    : c.refreshing ? 'Reading the playlist… (large playlists take ~20 s)'
    : c.error ? `Local copy not current: ${c.error}`
    : c.readAt ? `Local copy: ${c.distinct} videos, read ${ago(c.readAt)}`
    : 'Local copy: not read yet';
  $('#refresh-copy').hidden = !s.defaultId || c.refreshing;

  const ready = !!s.defaultId && !busy;
  $('#save-tabs').textContent = `Save all open YouTube tabs (${st.videoTabs})`;
  $('#save-tabs').disabled = !ready || !st.videoTabs;
  $('#close-tabs').disabled = !ready || !st.videoTabs;
  $('#refresh-list').disabled = busy;

  const activity = $('#activity');
  const items = (st.activity || []).map(a => listItem(a.outcome, a.title || a.videoId || a.reason || '', a.reason));
  if (items.length) activity.replaceChildren(...items);
}

async function run(label, fn) {
  busy = true;
  message('');
  await render();
  try {
    await fn();
  } catch (e) {
    message(e.message, true);
  } finally {
    busy = false;
    await render();
  }
}

function check(res) {
  if (res && res.error) throw new Error(res.error);
  return res;
}

$('#refresh-list').addEventListener('click', () => run('list', async () => {
  const s = check(await send('listPlaylists'));
  message(`${s.playlists.length} playlists found`);
}));

$('#playlist').addEventListener('change', e => {
  const playlistId = e.target.value;  // read before run(): its first render resets the select to the stored value
  if (!playlistId) return;
  run('default', async () => { check(await send('setDefault', { playlistId })); });
});

$('#refresh-copy').addEventListener('click', () => run('copy', async () => {
  check(await send('refreshCopy'));
}));

$('#save-tabs').addEventListener('click', () => run('save', async () => {
  const res = check(await send('saveAllTabs'));
  $('#results').replaceChildren(...res.results.map(r => listItem(r.outcome, r.title || r.videoId, r.reason)));
  const videos = [...new Map(res.results.filter(r => r.videoId).map(r => [r.videoId, r.outcome])).values()];
  const count = outcome => videos.filter(o => o === outcome).length;
  const failed = videos.length - count('saved') - count('already');
  const noVideo = res.results.filter(r => r.outcome === 'no-video').length;
  message([`${count('saved')} video(s) saved`, `${count('already')} already there`,
    failed ? `${failed} not saved` : '', noVideo ? `${noVideo} tab(s) without a video` : ''].filter(Boolean).join(', '));
}));

$('#close-tabs').addEventListener('click', () => run('close', async () => {
  const preview = check(await send('closeSavedTabs', { dryRun: true }));
  if (!preview.count) { message('No open tab shows a video that is in the playlist'); return; }
  const list = preview.titles.slice(0, 12).map(t => `- ${t}`).join('\n');
  if (!confirm(`Close ${preview.count} tab(s) whose video is in the playlist?\n\n${list}`)) return;
  const res = check(await send('closeSavedTabs'));
  message(`Closed ${res.closed} tab(s)`);
}));

render();
setInterval(() => { if (!busy) render(); }, 1500);
