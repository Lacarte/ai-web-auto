// Real-browser tests: Playwright Chromium + a test build of the extension (its YouTube origin swapped
// for the local fake YouTube in tests/fixtures/site). Every check reads the fake server's real state.
//   node run-e2e.mjs            (HEADED=1 to watch)
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
// YQS_EXT_SRC / YQS_RESULTS let the planted-bug runner test a mutated copy without touching the real results.
const EXT_SRC = process.env.YQS_EXT_SRC || path.join(ROOT, 'youtube-quick-save-extension');
const BUILD = path.join(process.env.YQS_RESULTS || HERE, '.build', 'extension');
const RESULTS = process.env.YQS_RESULTS || path.join(ROOT, 'test-results');
const SHOTS = path.join(RESULTS, 'screenshots');
const PORT = 8898;
const SITE = `http://127.0.0.1:${PORT}`;
const PY = process.env.PYTHON || path.resolve(ROOT, '..', '..', 'venv', 'Scripts', 'python.exe');
const TEST_PL = 'PLtestQuick001';
const BIG_PL = 'PLbigList00001';

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function until(fn, { timeout = 10000, interval = 100, desc = 'condition' } = {}) {
  const end = Date.now() + timeout;
  let last;
  while (Date.now() < end) {
    try { last = await fn(); if (last) return last; } catch (e) { last = e; }
    await sleep(interval);
  }
  throw new Error(`timed out waiting for ${desc} (last: ${last instanceof Error ? last.message : JSON.stringify(last)})`);
}

function assert(cond, msg) { if (!cond) throw new Error(msg); }

// ── fake YouTube controls ───────────────────────────────────────────────────

async function api(p, body) {
  const res = await fetch(SITE + p, body === undefined ? {} :
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  return res.json();
}
const state = () => api('/__state');
const copies = async (vid, pl = TEST_PL) => (await state()).playlists[pl].entries.filter(e => e.videoId === vid);
const addRequests = async vid => (await state()).log.filter(l => l.path.endsWith('edit_playlist') &&
  (l.body.actions || []).some(a => a.action === 'ACTION_ADD_VIDEO' && a.addedVideoId === vid)).length;

// ── extension build, browser, helpers ───────────────────────────────────────

function buildTestExtension() {
  fs.rmSync(BUILD, { recursive: true, force: true });
  fs.cpSync(EXT_SRC, BUILD, { recursive: true });
  const manifest = path.join(BUILD, 'manifest.json');
  const text = fs.readFileSync(manifest, 'utf8');
  assert(text.includes('https://www.youtube.com/*'), 'manifest origin not found');
  fs.writeFileSync(manifest, text.replaceAll('https://www.youtube.com/*', `${SITE}/*`));
}

let ctx;
let extId;
let server;

async function overlay(page) {
  return page.evaluate(() => {
    const h = document.getElementById('yqs-overlay');
    if (!h) return null;
    const box = h.shadowRoot.querySelector('.box').getBoundingClientRect();
    return { ...h.dataset, box: [box.left, box.top, box.width, box.height].map(Math.round),
      badge: h.shadowRoot.querySelector('.badge').textContent };
  });
}

async function thumbBox(page, id) {
  const box = await page.locator(`[data-testid="tile-${id}"] a.thumb`).first().boundingBox();
  assert(box, `no thumbnail for ${id}`);
  return box;
}

async function hover(page, x, y) {
  await page.bringToFront();
  await page.mouse.move(x - 5, y - 5);
  await page.mouse.move(x, y, { steps: 3 });
}

async function hoverTile(page, id) {
  const b = await thumbBox(page, id);
  await hover(page, b.x + b.width / 2, b.y + b.height / 2);
  return b;
}

async function ctrlCtrl(page) {
  await page.keyboard.press('Control');
  await sleep(120);
  await page.keyboard.press('Control');
}

const overlayIs = (page, want, desc) => until(async () => {
  const o = await overlay(page);
  return o && Object.entries(want).every(([k, v]) => o[k] === v) && o;
}, { desc: `${desc}: overlay ${JSON.stringify(want)}` });

async function popupPage() {
  const page = await ctx.newPage();
  await page.goto(`chrome-extension://${extId}/html/popup.html`);
  return page;
}

const shot = (page, name) => page.screenshot({ path: path.join(SHOTS, `${name}.png`) });

// ── tests ───────────────────────────────────────────────────────────────────

const results = [];
async function test(name, fn) {
  const t0 = Date.now();
  try {
    const evidence = await fn();
    results.push({ name, ok: true, ms: Date.now() - t0, evidence: evidence || null });
    console.log(`PASS ${name} (${Date.now() - t0} ms)`);
  } catch (e) {
    results.push({ name, ok: false, ms: Date.now() - t0, error: e.message });
    console.log(`FAIL ${name}: ${e.message}`);
  }
}

async function main() {
  fs.rmSync(SHOTS, { recursive: true, force: true });   // only this suite's evidence; other results stay
  fs.mkdirSync(SHOTS, { recursive: true });
  buildTestExtension();
  server = spawn(PY, [path.join(ROOT, 'tests', 'fixtures', 'site', 'fake_youtube.py'), String(PORT)], { stdio: 'ignore' });
  await until(async () => (await fetch(`${SITE}/__state`)).ok, { desc: 'fake YouTube' });
  await api('/__reset', {});
  ctx = await chromium.launchPersistentContext(fs.mkdtempSync(path.join(os.tmpdir(), 'yqs-e2e-')), {
    channel: 'chromium', headless: !process.env.HEADED, viewport: { width: 1360, height: 900 },
    args: [`--disable-extensions-except=${BUILD}`, `--load-extension=${BUILD}`],
  });
  const sw = ctx.serviceWorkers()[0] || await ctx.waitForEvent('serviceworker');
  extId = new URL(sw.url()).host;

  const home = ctx.pages()[0] || await ctx.newPage();
  await home.goto(`${SITE}/`);
  const popup = await popupPage();
  const storage = () => popup.evaluate(() => chrome.storage.local.get(null));

  await test('popup lists the playlists and keeps the chosen default', async () => {
    await popup.click('#refresh-list');
    await until(async () => (await popup.locator('#playlist option').count()) === 5, { desc: 'playlist options' });
    await popup.selectOption('#playlist', TEST_PL);
    await until(async () => (await storage()).settings?.defaultId === TEST_PL, { desc: 'default saved' });
    await popup.reload();
    await until(async () => (await popup.inputValue('#playlist')) === TEST_PL, { desc: 'default shown after reopening' });
    const s = (await storage()).settings;
    return { playlists: s.playlists.map(p => p.title), account: s.account };
  });

  await test('local copy reads every page of a playlist and ignores the decoy continuation', async () => {
    await popup.selectOption('#playlist', BIG_PL);
    const text = await until(async () => { const t = await popup.textContent('#copy'); return /Local copy: 7 videos/.test(t) && t; },
      { desc: '7 videos in the local copy', timeout: 15000 });
    const browse = (await state()).log.filter(l => l.path === '/youtubei/v1/browse');
    assert(!browse.some(l => String(l.body.continuation || '').startsWith('decoy_')), 'followed the decoy continuation');
    await popup.selectOption('#playlist', TEST_PL);
    await until(async () => /Local copy: 0 videos/.test(await popup.textContent('#copy')), { desc: 'empty test playlist read' });
    return { copy: text, browseRequests: browse.length };
  });

  await test('Home: the highlight sits on the thumbnail under the cursor', async () => {
    const b = await hoverTile(home, 'homeVideo02');
    const o = await overlayIs(home, { videoId: 'homeVideo02', kind: 'tile', state: 'target' }, 'tile');
    assert([o.box[0] - b.x, o.box[1] - b.y, o.box[2] - b.width, o.box[3] - b.height].every(d => Math.abs(d) <= 2),
      `box ${o.box} vs thumbnail ${JSON.stringify(b)}`);
    assert(o.badge.includes('zz quick-save test'), `badge: ${o.badge}`);
    await shot(home, '01-home-highlight');
    return { box: o.box, badge: o.badge };
  });

  await test('Home: between tiles the nearest one is highlighted', async () => {
    const tile = await home.locator('[data-testid="tile-homeVideo01"]').boundingBox();
    await hover(home, tile.x + tile.width + 7, tile.y + 60);   // 7 px right of tile 1, 17 px left of tile 2
    const o = await overlayIs(home, { videoId: 'homeVideo01', kind: 'near' }, 'nearest');
    return { videoId: o.videoId };
  });

  await test('Home: the hover preview covering the thumbnail is seen through', async () => {
    const b = await hoverTile(home, 'homeVideo03');
    await sleep(900);   // the fake preview appears after 600 ms
    const under = await home.evaluate(([x, y]) => !!document.elementFromPoint(x, y).closest('ytd-video-preview'),
      [b.x + b.width / 2, b.y + b.height / 2]);
    assert(under, 'the preview did not cover the thumbnail - scenario not reproduced');
    await home.mouse.move(b.x + b.width / 2 + 3, b.y + b.height / 2 + 3);
    const o = await overlayIs(home, { videoId: 'homeVideo03', kind: 'preview' }, 'preview');
    await shot(home, '02-home-preview');
    return { kind: o.kind };
  });

  await test('Ctrl, Ctrl saves the video under the cursor once', async () => {
    await ctrlCtrl(home);
    const o = await overlayIs(home, { videoId: 'homeVideo03', state: 'saved' }, 'saved');
    assert((await copies('homeVideo03')).length === 1, 'not exactly one copy');
    assert(o.toast === 'Saved to zz quick-save test', `toast: ${o.toast}`);
    await shot(home, '03-saved');
    return { toast: o.toast };
  });

  await test('pressing again adds no second copy', async () => {
    await ctrlCtrl(home);
    const o = await overlayIs(home, { videoId: 'homeVideo03', state: 'already' }, 'already');
    assert((await copies('homeVideo03')).length === 1, 'a second copy appeared');
    assert(await addRequests('homeVideo03') === 1, 'a second add request was sent');
    return { toast: o.toast };
  });

  await test('Ctrl shortcuts, slow taps and typing never save', async () => {
    await hoverTile(home, 'homeVideo04');
    await overlayIs(home, { videoId: 'homeVideo04' }, 'target');
    await home.keyboard.press('Control'); await sleep(600); await home.keyboard.press('Control');   // too slow
    await home.keyboard.down('Control'); await home.keyboard.press('c'); await home.keyboard.up('Control');
    await home.keyboard.press('Control');                                                            // Ctrl+C, Ctrl
    await home.focus('#search');
    await ctrlCtrl(home);                                                                            // typing
    await sleep(1500);
    await home.evaluate(() => document.activeElement.blur());
    assert(await addRequests('homeVideo04') === 0, 'something was saved');
    return { addRequests: 0 };
  });

  await test('watch page: the main player and the sidebar tiles', async () => {
    await home.click('[data-testid="tile-homeVideo05"] h3 a');   // single-page navigation, like YouTube
    await home.waitForSelector('ytd-watch-flexy[video-id="homeVideo05"]');
    const player = await home.locator('#movie_player').boundingBox();
    await hover(home, player.x + player.width / 2, player.y + player.height / 2);
    await overlayIs(home, { videoId: 'homeVideo05', kind: 'player' }, 'player');
    await shot(home, '04-player');
    await hoverTile(home, 'sideVideo02');
    await overlayIs(home, { videoId: 'sideVideo02', kind: 'tile' }, 'sidebar');
    await ctrlCtrl(home);
    await overlayIs(home, { videoId: 'sideVideo02', state: 'saved' }, 'sidebar saved');
    assert((await copies('sideVideo02')).length === 1, 'sidebar video not saved once');
    await shot(home, '05-sidebar-saved');
    return { player: 'homeVideo05', saved: 'sideVideo02' };
  });

  await test('channel page: a Short is highlighted and saved', async () => {
    await home.goto(`${SITE}/@RayCodingCorner`);
    await hoverTile(home, 'chanShort02');
    await overlayIs(home, { videoId: 'chanShort02', kind: 'tile' }, 'short');
    await ctrlCtrl(home);
    await overlayIs(home, { videoId: 'chanShort02', state: 'saved' }, 'short saved');
    assert((await copies('chanShort02')).length === 1, 'short not saved once');
    return { saved: 'chanShort02' };
  });

  await test('two tabs, same video, one after the other: one copy (the lagging flag is not trusted)', async () => {
    const other = await ctx.newPage();
    await other.goto(`${SITE}/`);
    await home.goto(`${SITE}/`);
    await hoverTile(other, 'homeVideo01');
    await ctrlCtrl(other);
    await overlayIs(other, { videoId: 'homeVideo01', state: 'saved' }, 'tab B saved');
    await hoverTile(home, 'homeVideo01');
    await ctrlCtrl(home);
    await overlayIs(home, { videoId: 'homeVideo01', state: 'already' }, 'tab A already');
    assert((await copies('homeVideo01')).length === 1, 'duplicate created');
    await other.close();
    return { copies: 1 };
  });

  await test('added elsewhere moments before: only the copy this extension made is removed', async () => {
    await api('/__add_elsewhere', { playlistId: TEST_PL, videoId: 'homeVideo02', setVideoId: 'ELSEWHERE0000002' });
    await hoverTile(home, 'homeVideo02');
    await ctrlCtrl(home);   // the flag still lags and the local copy doesn't know: a duplicate appears
    await overlayIs(home, { videoId: 'homeVideo02', state: 'saved' }, 'saved despite lag');
    assert((await copies('homeVideo02')).length === 2, 'expected the duplicate this scenario creates');
    await popup.evaluate(() => chrome.runtime.sendMessage({ type: 'checkNow' }));
    const left = await until(async () => { const c = await copies('homeVideo02'); return c.length === 1 && c; },
      { desc: 'one copy after the background check', timeout: 15000 });
    assert(left[0].setVideoId === 'ELSEWHERE0000002', `kept ${left[0].setVideoId}, not the other device's copy`);
    const activity = (await storage()).activity;
    assert(activity.some(a => a.outcome === 'repaired' && a.videoId === 'homeVideo02'), 'repair not logged');
    return { kept: left[0].setVideoId };
  });

  await test('a failed add is reported and never retried', async () => {
    await api('/__inject', { fail_next_add: true });
    await hoverTile(home, 'homeVideo06');
    await ctrlCtrl(home);
    const o = await overlayIs(home, { videoId: 'homeVideo06', state: 'failed' }, 'failed');
    await sleep(1000);
    assert(await addRequests('homeVideo06') === 1, 'the add was retried');
    assert((await copies('homeVideo06')).length === 0, 'video was added');
    return { toast: o.toast };
  });

  await test('signed in to a different account: nothing is saved', async () => {
    await api('/__account', { account: 'acct-other||' });
    try {
      await home.reload();
      await hoverTile(home, 'homeVideo04');
      await ctrlCtrl(home);
      const o = await overlayIs(home, { videoId: 'homeVideo04', state: 'wrong-account' }, 'wrong account');
      assert(await addRequests('homeVideo04') === 0, 'saved on the wrong account');
      return { toast: o.toast };
    } finally {
      await api('/__account', { account: 'acct-test||' });   // later tests must not inherit the other account
      await home.reload();
    }
  });

  await test('popup: save all open YouTube tabs', async () => {
    const tabs = [];
    for (const v of ['tabVideo001', 'tabVideo002', 'tabVideo001']) {
      const p = await ctx.newPage();
      await p.goto(`${SITE}/watch?v=${v}`);
      tabs.push(p);
    }
    await popup.bringToFront();
    await popup.reload();
    await until(async () => !(await popup.isDisabled('#save-tabs')), { desc: 'save button enabled' });
    await popup.click('#save-tabs');
    const msg = await until(async () => { const t = await popup.textContent('#message'); return /saved/.test(t) && t; },
      { desc: 'batch result', timeout: 20000 });
    const listed = (await popup.locator('#results li').allTextContents()).join(' | ');
    assert((await copies('tabVideo001')).length === 1 && (await copies('tabVideo002')).length === 1,
      `tab videos not saved once - popup: ${msg} / ${listed}`);
    await shot(popup, '06-popup-save-tabs');
    return { message: msg };
  });

  await test('popup: close saved tabs closes only tabs whose video is in the playlist', async () => {
    const unsaved = await ctx.newPage();
    await unsaved.goto(`${SITE}/watch?v=tabVideo003`);
    await popup.bringToFront();
    popup.once('dialog', d => d.accept());
    await popup.click('#close-tabs');
    await until(async () => /Closed/.test(await popup.textContent('#message')),
      { desc: `closed message (popup says: ${await popup.textContent('#message')})` });
    const urls = ctx.pages().map(p => p.url());
    assert(!urls.some(u => /tabVideo00[12]/.test(u)), `saved tabs still open: ${urls}`);
    assert(urls.some(u => u.includes('tabVideo003')), 'the unsaved tab was closed');
    assert(urls.some(u => u === `${SITE}/`), 'the home tab was closed');
    await shot(popup, '07-popup-closed');
    return { open: urls };
  });

  await test('every request was signed; remove-by-video was never used', async () => {
    const log = (await state()).log;
    assert(log.length > 0 && log.every(l => l.signed), 'unsigned request');
    const removeAll = log.filter(l => (l.body.actions || []).some(a => a.action === 'ACTION_REMOVE_VIDEO_BY_VIDEO_ID'));
    assert(removeAll.length === 0, 'remove-by-video used');
    const removes = log.filter(l => (l.body.actions || []).some(a => a.action === 'ACTION_REMOVE_VIDEO'));
    return { requests: log.length, removes: removes.length };
  });

  const final = await state();
  fs.writeFileSync(path.join(RESULTS, 'e2e-results.json'), JSON.stringify({
    date: new Date().toISOString(), passed: results.filter(r => r.ok).length, failed: results.filter(r => !r.ok).length,
    results, testPlaylist: final.playlists[TEST_PL].entries }, null, 2));
}

main()
  .catch(e => { console.error(e); results.push({ name: 'setup', ok: false, error: e.message }); })
  .finally(async () => {
    await ctx?.close().catch(() => {});
    server?.kill();
    const failed = results.filter(r => !r.ok).length;
    console.log(`\n${results.length - failed}/${results.length} passed`);
    process.exit(failed ? 1 : 0);
  });
