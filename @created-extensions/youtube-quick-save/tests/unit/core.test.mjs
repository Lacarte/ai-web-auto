// Unit tests for js/lib/core.js. Run: node --test tests/unit
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const core = require(process.env.YQS_CORE || '../../youtube-quick-save-extension/js/lib/core.js');  // YQS_CORE: planted-bug runs
const fixture = name => JSON.parse(fs.readFileSync(path.join(here, '..', 'fixtures', 'youtubei', `${name}.json`), 'utf8'));
const clone = o => JSON.parse(JSON.stringify(o));

// ── video identity ──────────────────────────────────────────────────────────

test('video IDs come from watch, shorts, live and embed links', () => {
  assert.equal(core.videoIdFromUrl('https://www.youtube.com/watch?v=xxf6D9jynFw'), 'xxf6D9jynFw');
  assert.equal(core.videoIdFromUrl('/watch?v=xxf6D9jynFw&list=PLx&index=3&t=42s'), 'xxf6D9jynFw');
  assert.equal(core.videoIdFromUrl('/shorts/0XQele28q0w'), '0XQele28q0w');
  assert.equal(core.videoIdFromUrl('https://www.youtube.com/live/abcdefghijk?si=1'), 'abcdefghijk');
  assert.equal(core.videoIdFromUrl('http://127.0.0.1:8898/watch?v=AAAAAAAAAA1'), 'AAAAAAAAAA1');
});

test('anything else has no video ID', () => {
  for (const href of ['/playlist?list=PL123', '/@RayCodingCorner', '/watch?v=short', '/watch', '/shorts/', '', null,
    '/watch?v=xxf6D9jynFwEXTRA', 'javascript:alert(1)']) {
    assert.equal(core.videoIdFromUrl(href), null, String(href));
  }
});

// ── target under / near the cursor ──────────────────────────────────────────

const r = (left, top, w, h) => ({ left, top, right: left + w, bottom: top + h });

test('the tile under the cursor wins; nested boxes pick the inner one', () => {
  const tiles = [
    { id: 'outer', rect: r(0, 0, 400, 300) },
    { id: 'thumb', rect: r(10, 10, 360, 200) },
    { id: 'other', rect: r(420, 0, 400, 300) },
  ];
  assert.equal(core.pickTarget(50, 50, tiles).id, 'thumb');
  assert.equal(core.pickTarget(380, 250, tiles).id, 'outer');
});

test('between tiles the nearest one within range is chosen; far away nothing', () => {
  const tiles = [{ id: 'left', rect: r(0, 0, 100, 100) }, { id: 'right', rect: r(130, 0, 100, 100) }];
  assert.equal(core.pickTarget(110, 50, tiles).id, 'left');
  assert.equal(core.pickTarget(122, 50, tiles).id, 'right');
  assert.equal(core.pickTarget(500, 500, tiles), null);
  assert.equal(core.pickTarget(50, 50, [{ id: null, rect: r(0, 0, 100, 100) }, { id: 'z', rect: r(0, 0, 0, 0) }]), null);
});

// ── Ctrl, Ctrl ──────────────────────────────────────────────────────────────

const ctrl = (extra = {}) => ({ key: 'Control', repeat: false, altKey: false, shiftKey: false, metaKey: false, editable: false, ...extra });
const key = k => ({ key: k, repeat: false, altKey: false, shiftKey: false, metaKey: false, editable: false });

function tap(d, at, ev = ctrl()) {
  assert.equal(d.keydown(ev, at), false, 'never fires on a key press');
  return d.keyup(ev, at + 40);
}

test('two quick Ctrl taps fire once, when the second Ctrl is released', () => {
  const d = core.createDoubleCtrl(400);
  assert.equal(tap(d, 0), false);
  assert.equal(tap(d, 250), true);
  assert.equal(tap(d, 600), false, 'a third tap starts a new gesture');
  assert.equal(tap(d, 800), true);
});

test('a Ctrl tap followed quickly by Ctrl+C does not fire', () => {
  const d = core.createDoubleCtrl(400);
  tap(d, 0);
  d.keydown(ctrl(), 150);          // Ctrl of Ctrl+C, within the window
  d.keydown(key('c'), 180);
  assert.equal(d.keyup(ctrl(), 230), false);
  assert.equal(d.state, 'idle');
});

test('slow taps, Ctrl+key shortcuts, other modifiers and held Ctrl do not fire', () => {
  let d = core.createDoubleCtrl(400);
  tap(d, 0);
  assert.equal(tap(d, 450), false, 'too slow');

  d = core.createDoubleCtrl(400);
  d.keydown(ctrl(), 0);
  d.keydown(key('c'), 50);          // Ctrl+C
  d.keyup(ctrl(), 90);
  assert.equal(tap(d, 200), false, 'Ctrl+C then Ctrl');

  d = core.createDoubleCtrl(400);
  tap(d, 0);
  assert.equal(tap(d, 200, ctrl({ shiftKey: true })), false, 'Ctrl+Shift');

  d = core.createDoubleCtrl(400);
  d.keydown(ctrl(), 0);
  assert.equal(d.keydown(ctrl({ repeat: true }), 300), false, 'auto-repeat while held');
});

test('typing in a text field never fires', () => {
  const d = core.createDoubleCtrl(400);
  tap(d, 0, ctrl({ editable: true }));
  assert.equal(tap(d, 200, ctrl({ editable: true })), false);
  tap(d, 1000, ctrl({ editable: true }));
  assert.equal(tap(d, 1200), false, 'first tap was in a text field');
});

// ── YouTube replies (anonymised recordings) ─────────────────────────────────

test('playlist list: IDs, titles, privacy and the contains flag', () => {
  const pls = core.parsePlaylists(fixture('get_add_to_playlist'));
  assert.equal(pls.length, 4);
  assert.deepEqual(pls[0], { id: 'WL', title: 'Watch later', privacy: 'PRIVATE', contains: 'NONE' });
  assert.equal(pls[3].title, 'zz quick-save test');
  assert.deepEqual(pls.map(p => p.contains), ['NONE', 'ALL', 'ALL', 'NONE']);
  assert.throws(() => core.parsePlaylists({ contents: [] }), core.UnexpectedReply);
});

test('playlist page: entries and the continuation at the end of the video list, not the section one', () => {
  const page1 = fixture('browse_page1');
  const p = core.parsePlaylistPage(page1);
  assert.equal(p.found, true);
  assert.equal(p.entries.length, 3);
  assert.match(p.entries[0].videoId, /^vid\d{8}$/);
  assert.match(p.entries[0].setVideoId, /^SET\d+$/);
  // The fixture keeps both tokens YouTube sends; only the one inside the video list pages the videos.
  const list = JSON.stringify(page1).match(/"playlistVideoListRenderer".*/s)[0];
  const tokens = [...JSON.stringify(page1).matchAll(/"token":\s*"(TOKEN_\d+)"/g)].map(m => m[1]);
  assert.equal(new Set(tokens).size, 2, 'the recording has the section token too');
  assert.ok(p.continuation && list.includes(p.continuation));
  const sectionToken = tokens.find(t => t !== p.continuation);
  const listJson = JSON.stringify(page1.contents.twoColumnBrowseResultsRenderer.tabs[0].tabRenderer.content
    .sectionListRenderer.contents[0]);
  assert.ok(listJson.includes(p.continuation) && !listJson.includes(sectionToken));
});

test('continuation page: entries from appendContinuationItemsAction; last page has no token', () => {
  const page2 = fixture('browse_page2');
  const p = core.parsePlaylistPage(page2);
  assert.equal(p.found, true);
  assert.equal(p.entries.length, 3);
  assert.ok(p.continuation);
  const last = clone(page2);
  const items = last.onResponseReceivedActions[0].appendContinuationItemsAction.continuationItems;
  last.onResponseReceivedActions[0].appendContinuationItemsAction.continuationItems = items.filter(i => !i.continuationItemRenderer);
  assert.equal(core.parsePlaylistPage(last).continuation, null);
});

test('an empty playlist is read as empty, not as an unexpected reply', async () => {
  const empty = { contents: { twoColumnBrowseResultsRenderer: { tabs: [{ tabRenderer: { content: { sectionListRenderer: {
    contents: [{ itemSectionRenderer: { contents: [{ playlistVideoListRenderer: { contents: [] } }] } }] } } } }] } } };
  assert.deepEqual(core.parsePlaylistPage(empty), { entries: [], continuation: null, found: true });
  assert.deepEqual(await core.readPlaylist(async () => empty), { entries: [], pages: 1 });
});

test('reading a whole playlist follows the video-list continuations', async () => {
  const page1 = fixture('browse_page1');
  const page2 = fixture('browse_page2');
  const last = clone(page2);
  last.onResponseReceivedActions[0].appendContinuationItemsAction.continuationItems =
    last.onResponseReceivedActions[0].appendContinuationItemsAction.continuationItems.filter(i => !i.continuationItemRenderer);
  const asked = [];
  const t1 = core.parsePlaylistPage(page1).continuation;
  const t2 = core.parsePlaylistPage(page2).continuation;
  const { entries, pages } = await core.readPlaylist(async token => {
    asked.push(token);
    return token === null ? page1 : token === t1 ? page2 : token === t2 ? last : {};
  });
  assert.deepEqual(asked, [null, t1, t2]);
  assert.equal(pages, 3);
  assert.equal(entries.length, 9);
  await assert.rejects(core.readPlaylist(async () => ({ contents: {} })), core.UnexpectedReply);
  await assert.rejects(core.readPlaylist(async () => page2, 2), /more than 2 pages/);
});

test('an add counts only when YouTube confirms this video and names the new entry', () => {
  const add = fixture('edit_add');
  const vid = add.playlistEditResults[0].playlistEditVideoAddedResultData.videoId;
  assert.deepEqual(core.parseAddResult(add, vid), { setVideoId: 'SET0000000000001' });
  assert.throws(() => core.parseAddResult(add, 'otherVideo1'), core.UnexpectedReply);
  assert.throws(() => core.parseAddResult({ ...add, status: 'STATUS_FAILED' }, vid), core.UnexpectedReply);
  assert.throws(() => core.parseAddResult({ status: 'STATUS_SUCCEEDED' }, vid), core.UnexpectedReply);
  assert.equal(core.parseEditStatus(fixture('edit_remove_entry')), true);
});

// ── saving ──────────────────────────────────────────────────────────────────

const VID = 'AAAAAAAAAA1';
const PL = 'PLfake0000003';

function deps(over = {}) {
  const calls = { flag: 0, add: 0 };
  const d = {
    calls,
    account: async () => ({ loggedIn: true, account: 'acct-1' }),
    recentlyAdded: () => false,
    inLocalCopy: () => false,
    flag: async () => { calls.flag += 1; return 'NONE'; },
    add: async () => { calls.add += 1; return { status: 'STATUS_SUCCEEDED', playlistEditResults: [{ playlistEditVideoAddedResultData: { videoId: VID, setVideoId: 'S1' } }] }; },
    ...over,
  };
  if (over.flag) d.flag = async (...a) => { calls.flag += 1; return over.flag(...a); };
  if (over.add) d.add = async (...a) => { calls.add += 1; return over.add(...a); };
  return d;
}

const save = (d, extra = {}) => core.saveVideo({ videoId: VID, playlistId: PL, account: 'acct-1', ...extra }, d);

test('absent video: one add, confirmed by YouTube', async () => {
  const d = deps();
  assert.deepEqual(await save(d), { outcome: 'saved', setVideoId: 'S1' });
  assert.deepEqual(d.calls, { flag: 1, add: 1 });
});

test('already there (recent add, local copy, or YouTube flag): nothing is sent', async () => {
  for (const [over, via] of [[{ recentlyAdded: () => true }, 'recent'], [{ inLocalCopy: () => true }, 'local copy'],
    [{ flag: () => 'ALL' }, 'youtube']]) {
    const d = deps(over);
    assert.deepEqual(await save(d), { outcome: 'already', via });
    assert.equal(d.calls.add, 0);
  }
});

test('signed out, other account, unknown playlist: stop before any write', async () => {
  let d = deps({ account: async () => ({ loggedIn: false }) });
  assert.equal((await save(d)).outcome, 'signed-out');
  d = deps({ account: async () => ({ loggedIn: true, account: 'acct-2' }) });
  assert.equal((await save(d)).outcome, 'wrong-account');
  d = deps({ flag: () => null });
  assert.equal((await save(d)).outcome, 'no-playlist');
  for (const x of [d]) assert.equal(x.calls.add, 0);
  assert.equal((await save(deps(), { playlistId: '' })).outcome, 'no-playlist');
  assert.equal((await save(deps(), { videoId: 'nope' })).outcome, 'failed');
});

test('a failed or unconfirmed add is reported, never retried', async () => {
  let d = deps({ add: () => { throw new Error('HTTP 500'); } });
  let res = await save(d);
  assert.equal(res.outcome, 'failed');
  assert.equal(res.unknown, true);
  assert.equal(d.calls.add, 1);
  d = deps({ add: () => ({ status: 'STATUS_SUCCEEDED' }) });
  res = await save(d);
  assert.equal(res.outcome, 'failed');
  assert.equal(d.calls.add, 1);
});

test('a fake YouTube that lags and allows duplicates: two quick saves leave one copy', async () => {
  // The flag only catches up after 3 calls - like the real one, which lagged 5-10 s.
  const entries = [];
  let lag = 0;
  const recent = new Set();
  const yt = {
    account: async () => ({ loggedIn: true, account: 'acct-1' }),
    recentlyAdded: id => recent.has(id),
    inLocalCopy: () => false,
    flag: async id => (lag++ >= 3 && entries.some(e => e.videoId === id) ? 'ALL' : 'NONE'),
    add: async id => {
      const setVideoId = `S${entries.length + 1}`;
      entries.push({ videoId: id, setVideoId });  // no deduplication, like YouTube
      return { status: 'STATUS_SUCCEEDED', playlistEditResults: [{ playlistEditVideoAddedResultData: { videoId: id, setVideoId } }] };
    },
  };
  const first = await save(yt);
  if (first.outcome === 'saved') recent.add(VID);  // what the background does with a saved result
  const second = await save(yt);
  assert.equal(first.outcome, 'saved');
  assert.deepEqual(second, { outcome: 'already', via: 'recent' });
  assert.equal(entries.length, 1);
});

// ── duplicate repair ────────────────────────────────────────────────────────

test('repair removes only the entry the extension created; never other copies', () => {
  const entries = [
    { videoId: 'A', setVideoId: 'userA' }, { videoId: 'A', setVideoId: 'oursA' },   // added elsewhere + ours
    { videoId: 'B', setVideoId: 'oursB' },                                            // fine
    { videoId: 'C', setVideoId: 'userC1' }, { videoId: 'C', setVideoId: 'userC2' },  // user's own duplicate
  ];
  const plan = core.planRepairs(entries, [
    { videoId: 'A', setVideoId: 'oursA' }, { videoId: 'B', setVideoId: 'oursB' }, { videoId: 'D', setVideoId: 'oursD' },
  ]);
  assert.deepEqual(plan.remove, [{ videoId: 'A', setVideoId: 'oursA' }]);
  assert.deepEqual(plan.missing, ['D']);
  assert.deepEqual(core.planRepairs(entries, [{ videoId: 'C', setVideoId: 'gone' }]).remove, []);
});
