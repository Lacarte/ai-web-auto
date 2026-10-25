// Planted-bug check: put one realistic bug at a time into a COPY of the code and confirm the tests fail.
// A bug that survives is a missing test.      node tests/mutation/run-mutations.mjs [--unit-only]
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const EXT = path.join(ROOT, 'youtube-quick-save-extension');

// [file, what the bug does, original text, mutated text]
const CORE = [
  ['pickTarget prefers the outer box', 'key[0] === bestKey[0] && key[1] < bestKey[1]', 'false'],
  ['Ctrl, Ctrl fires while typing', 'ev.metaKey || ev.editable', 'ev.metaKey'],
  ['Ctrl, Ctrl ignores the time window', "state === 'released' && now - firstAt <= windowMs", "state === 'released'"],
  ['Ctrl, Ctrl fires on the second press', "state = 'second';", "reset(); return true;"],
  ['follows the last continuation anywhere (the decoy)', 'if (o.playlistVideoListRenderer) out.found = true;',
    'if (o.playlistVideoListRenderer) out.found = true; if (o.continuationCommand && o.continuationCommand.token) out.continuation = o.continuationCommand.token;'],
  ['an unreadable reply is taken as an empty playlist', "if (!first.found) throw new UnexpectedReply('playlist reply has no video list');", ''],
  ['an add counts without YouTube naming this video', 'a => a.videoId === videoId', 'a => true'],
  ['the account is not checked', 'if (account && acct.account !== account)', 'if (false)'],
  ['the recent-adds memory is ignored', 'if (deps.recentlyAdded(videoId))', 'if (false)'],
  ['the local copy is ignored', 'if (deps.inLocalCopy(videoId))', 'if (false)'],
  ["YouTube's flag is ignored", "if (flag !== 'NONE')", "if (flag === 'never')"],
  ['a failed add is retried', "return { outcome: 'failed', reason: `add request failed: ${e.message}`, unknown: true };",
    'reply = await deps.add(videoId);'],
  ["repair removes the other device's copy", 'else if (copies.length > 1 && copies.some(c => c.setVideoId === add.setVideoId)) remove.push(add);',
    'else if (copies.length > 1) remove.push(copies.find(c => c.setVideoId !== add.setVideoId));'],
  ['repair removes copies the extension did not create', 'copies.length > 1 && copies.some(c => c.setVideoId === add.setVideoId)', 'copies.length > 1'],
].map(([what, from, to]) => ['js/lib/core.js', what, from, to]);

const EXTENSION = [
  ['js/page/content.js', 'Ctrl, Ctrl counts while typing in the page', 'editable: typing()', 'editable: false'],
  ['js/page/content.js', 'the highlight covers the whole tile, not the thumbnail', 'return best || rectOf(tile);', 'return rectOf(tile);'],
  ['js/page/bridge.js', 'requests are not signed', 'if (auth.length) h.Authorization', 'if (false) h.Authorization'],
  ['js/background.js', 'the duplicate repair never runs', 'for (const a of plan.remove) {', 'for (const a of []) {'],
  ['js/background.js', 'close saved tabs closes every video tab', 'tabs.filter(t => confirmed.get(t.videoId)).map(t => t.tab)', 'tabs.map(t => t.tab)'],
];

function mutatedCopy(file, from, to) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'yqs-mut-'));
  const ext = path.join(dir, 'extension');
  fs.cpSync(EXT, ext, { recursive: true });
  const target = path.join(ext, file);
  const text = fs.readFileSync(target, 'utf8');
  const count = text.split(from).length - 1;
  if (count !== 1) throw new Error(`${file}: pattern found ${count} times: ${from}`);
  fs.writeFileSync(target, text.replace(from, to));
  return { dir, ext };
}

function run(cmd, args, env) {
  const r = spawnSync(cmd, args, { cwd: ROOT, env: { ...process.env, ...env }, encoding: 'utf8', timeout: 600000 });
  return r.status;
}

const rows = [];
const unitOnly = process.argv.includes('--unit-only');
for (const [file, what, from, to] of [...CORE, ...(unitOnly ? [] : EXTENSION)]) {
  const { dir, ext } = mutatedCopy(file, from, to);
  const unit = file === 'js/lib/core.js';
  const status = unit
    ? run(process.execPath, ['--test', 'tests/unit/*.test.mjs'], { YQS_CORE: path.join(ext, 'js', 'lib', 'core.js') })
    : run(process.execPath, ['tests/e2e/run-e2e.mjs'], { YQS_EXT_SRC: ext, YQS_RESULTS: path.join(dir, 'results') });
  const caught = status !== 0;
  rows.push({ file, bug: what, suite: unit ? 'unit' : 'browser', caught });
  console.log(`${caught ? 'caught  ' : 'MISSED  '} [${unit ? 'unit   ' : 'browser'}] ${file}: ${what}`);
  fs.rmSync(dir, { recursive: true, force: true });
}
const caught = rows.filter(r => r.caught).length;
console.log(`\n${caught}/${rows.length} planted bugs caught`);
fs.mkdirSync(path.join(ROOT, 'test-results'), { recursive: true });
fs.writeFileSync(path.join(ROOT, 'test-results', 'mutations.json'), JSON.stringify({ date: new Date().toISOString(), caught, total: rows.length, rows }, null, 2));
process.exit(caught === rows.length ? 0 : 1);
