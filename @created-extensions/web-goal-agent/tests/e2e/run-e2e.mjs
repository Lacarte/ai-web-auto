/**
 * Real-browser end-to-end tests for the Web Goal Agent extension.
 *
 *   cd tests/e2e && npm install && npx playwright install chromium && node run-e2e.mjs
 *   (or run-tests.bat at the extension root)
 *
 * Loads the unpacked extension into Playwright's Chromium (branded Chrome 137+
 * ignores --load-extension), with WGA_CONFIG pointing at test-config.json so the
 * native host and backend use an isolated port, token, and state directory.
 * Requires install.bat (or this script's own registration step) for the native host.
 *
 * Env: HEADED=1 to watch, ONLY=<substring> to run matching tests, KEEP=1 to leave the browser open.
 */

import { chromium } from 'playwright';
import { spawn, execFileSync } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const EXT = path.join(ROOT, 'web-goal-agent-extension');
const BACKEND = path.join(ROOT, 'web-goal-agent-backend');
const PROJECT = path.resolve(ROOT, '..', '..');
const PY = path.join(PROJECT, 'venv', 'Scripts', 'python.exe');
const CONFIG_PATH = path.join(HERE, 'test-config.json');
const CONFIG = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
const RESULTS = path.join(ROOT, 'test-results');
const SHOTS = path.join(RESULTS, 'e2e');
const RUNS = path.join(RESULTS, 'runs');
const SITE = path.join(ROOT, 'tests', 'fixtures', 'site');
const SITE_PORT = 8777;
const BASE = `http://127.0.0.1:${SITE_PORT}`;
const BACKEND_URL = `http://127.0.0.1:${CONFIG.port}`;
const EXT_ID = 'lfejjmliceaahhcnbdlmafhklbehnpab';
const TERMINAL = new Set(['succeeded', 'failed', 'stopped']);

const results = [];
let context, sw, siteServer;

// ── Helpers ─────────────────────────────────────────────────────────────────

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function assert(cond, message) {
  if (!cond) throw new Error(`Assertion failed: ${message}`);
}

async function waitUntil(fn, { timeout = 20000, interval = 200, desc = 'condition' } = {}) {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    try { last = await fn(); if (last) return last; } catch (e) { last = e; }
    await sleep(interval);
  }
  throw new Error(`Timed out after ${timeout} ms waiting for ${desc} (last: ${last instanceof Error ? last.message : JSON.stringify(last)?.slice(0, 300)})`);
}

async function health() {
  try {
    const r = await fetch(`${BACKEND_URL}/health`, { signal: AbortSignal.timeout(1500) });
    return r.ok ? await r.json() : null;
  } catch { return null; }
}

async function shutdownBackend() {
  try { await fetch(`${BACKEND_URL}/shutdown`, { headers: { 'X-WGA-Token': CONFIG.token }, signal: AbortSignal.timeout(2000) }); } catch { /* not running */ }
  await waitUntil(async () => !(await health()), { timeout: 8000, desc: 'backend to stop' }).catch(() => {});
}

const swEval = (fn, arg) => sw.evaluate(fn, arg);
const linkState = () => swEval(() => globalThis.__wgaDebug.link.state);
const runState = () => swEval(() => globalThis.__wgaDebug.store.get().run);

async function getService() {
  sw = context.serviceWorkers().find((w) => w.url().includes(EXT_ID));
  if (!sw) sw = await context.waitForEvent('serviceworker', { predicate: (w) => w.url().includes(EXT_ID), timeout: 15000 });
  return sw;
}

let tabSeq = 0;

/** Opens a fresh fixture tab. The unique #fragment keeps tab lookup unambiguous (and doesn't trigger navigation). */
async function openSite(url) {
  const page = await context.newPage();
  await page.goto(`${url}#t${++tabSeq}`);
  const tabId = await waitUntil(() => swEval(async (u) => (await chrome.tabs.query({})).find((t) => t.url === u)?.id, page.url()), { desc: 'tab id' });
  return { page, tabId };
}

async function openPopup(tabId, goalId) {
  const url = `chrome-extension://${EXT_ID}/html/popup.html?tab=${tabId}${goalId ? `&goal=${goalId}` : ''}`;
  const pagePromise = context.waitForEvent('page', { predicate: (p) => p.url().startsWith(`chrome-extension://${EXT_ID}/html/popup.html`), timeout: 10000 });
  await swEval((u) => chrome.windows.create({ url: u, type: 'popup', width: 440, height: 900 }), url);
  const popup = await pagePromise;
  await popup.waitForLoadState('domcontentloaded');
  return popup;
}

async function startGoal(tabId, goalId, inputs = {}) {
  const popup = await openPopup(tabId, goalId);
  await popup.waitForFunction((g) => document.querySelector('#goalSelect')?.value === g, goalId, { timeout: 15000 });
  for (const [k, v] of Object.entries(inputs)) await popup.fill(`#in_${k}`, v);
  await popup.waitForFunction(() => !document.querySelector('#btnStart').disabled, null, { timeout: 15000 });
  const before = (await runState())?.run_id;
  await popup.click('#btnStart');
  const run = await waitUntil(async () => { const r = await runState(); return r && r.run_id && r.run_id !== before ? r : null; }, { desc: `run of ${goalId} to start` });
  return { popup, runId: run.run_id };
}

async function waitTerminal(runId, timeout = 90000) {
  return waitUntil(async () => { const r = await runState(); return r?.run_id === runId && TERMINAL.has(r.state) ? r : null; }, { timeout, desc: `run ${runId} to finish` });
}

function runLog(runId) {
  for (const day of fs.existsSync(RUNS) ? fs.readdirSync(RUNS) : []) {
    const f = path.join(RUNS, day, `${runId}.jsonl`);
    if (fs.existsSync(f)) return fs.readFileSync(f, 'utf8').trim().split('\n').map((l) => JSON.parse(l));
  }
  return [];
}

const fixture = (page, expr = 'window.__fixture') => page.evaluate(expr);

async function shot(page, name) {
  const file = path.join(SHOTS, `${name}.png`);
  try { await page.screenshot({ path: file }); } catch { /* page gone */ }
  return path.relative(ROOT, file);
}

async function closeExtraWindows() {
  for (const p of context.pages()) if (p.url().includes('popup.html')) await p.close().catch(() => {});
}

async function test(name, fn) {
  if (process.env.ONLY && !name.toLowerCase().includes(process.env.ONLY.toLowerCase())) return;
  const started = Date.now();
  const evidence = [];
  process.stdout.write(`\n▶ ${name}\n`);
  try {
    await fn(evidence);
    results.push({ name, ok: true, ms: Date.now() - started, evidence });
    process.stdout.write(`  ✓ passed (${Date.now() - started} ms)\n`);
  } catch (e) {
    const failShot = context?.pages().at(-1) ? await shot(context.pages().at(-1), `FAIL-${name.replace(/\W+/g, '_').slice(0, 50)}`) : null;
    results.push({ name, ok: false, ms: Date.now() - started, error: e.message, evidence: [...evidence, failShot].filter(Boolean) });
    process.stdout.write(`  ✗ FAILED: ${e.message}\n`);
  } finally {
    await closeExtraWindows();
  }
}

// ── Setup ───────────────────────────────────────────────────────────────────

async function setup() {
  fs.rmSync(RESULTS, { recursive: true, force: true });
  fs.mkdirSync(SHOTS, { recursive: true });
  execFileSync(PY, [path.join(BACKEND, 'native_host', 'install_host.py')], { stdio: 'inherit', env: { ...process.env, WGA_CONFIG: CONFIG_PATH } });
  await shutdownBackend();
  siteServer = spawn(PY, ['-m', 'http.server', String(SITE_PORT), '--bind', '127.0.0.1', '--directory', SITE], { stdio: 'ignore' });
  await waitUntil(async () => { try { return (await fetch(`${BASE}/crm.html`)).ok; } catch { return false; } }, { desc: 'fixture server' });
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), 'wga-e2e-'));
  context = await chromium.launchPersistentContext(userData, {
    channel: 'chromium',
    headless: !process.env.HEADED,
    viewport: { width: 1360, height: 860 },
    args: [`--disable-extensions-except=${EXT}`, `--load-extension=${EXT}`],
    env: { ...process.env, WGA_CONFIG: CONFIG_PATH },
  });
  await getService();
}

async function teardown() {
  if (!process.env.KEEP) {
    await context?.close().catch(() => {});
    await shutdownBackend();
  }
  siteServer?.kill();
}

// ── Tests ───────────────────────────────────────────────────────────────────

async function main() {
  await setup();
  const userAgent = await swEval(() => navigator.userAgent);

  await test('1. Extension installs and initializes (MV3 service worker, fixed ID, popup renders)', async (ev) => {
    assert(sw.url() === `chrome-extension://${EXT_ID}/js/service-worker.js`, `service worker url ${sw.url()}`);
    const manifest = await swEval(() => chrome.runtime.getManifest());
    assert(manifest.manifest_version === 3 && manifest.name === 'Web Goal Agent', 'manifest');
    const { tabId } = await openSite(`${BASE}/reports.html`);
    const popup = await openPopup(tabId);
    await popup.waitForSelector('h1:text("Web Goal Agent")');
    ev.push(`extension id ${EXT_ID}, version ${manifest.version}`);
    ev.push(await shot(popup, '01-popup'));
  });

  await test('2. Backend starts automatically through the native host', async (ev) => {
    const s = await waitUntil(async () => { const st = await linkState(); return st.status === 'connected' ? st : null; }, { timeout: 30000, desc: 'link connected' });
    const h = await health();
    assert(h && h.started_by === 'native-host', `backend started_by=${h?.started_by}`);
    assert(s.backend.pid === h.pid, 'connected to the started process');
    ev.push(`connected to backend v${s.backend.version} pid ${h.pid}, started_by=${h.started_by}; native: ${JSON.stringify(s.native.last)}`);
  });

  await test('3. Running backend is detected; concurrent ensures never start a duplicate', async (ev) => {
    const before = await health();
    const replies = await swEval(async () => Promise.all([1, 2, 3].map(() => chrome.runtime.sendNativeMessage('com.aiwebauto.web_goal_agent', { cmd: 'ensure' }))));
    for (const r of replies) assert(r.ok && r.status === 'already_running' && r.backend.pid === before.pid, `ensure reply ${JSON.stringify({ ...r, token: undefined })}`);
    const after = await health();
    assert(after.pid === before.pid, 'pid unchanged');
    const listeners = execFileSync('netstat', ['-ano', '-p', 'TCP']).toString().split('\n').filter((l) => l.includes(`:${CONFIG.port} `) && /LISTENING/.test(l));
    assert(listeners.length === 1, `exactly one listener on ${CONFIG.port}, got ${listeners.length}`);
    ev.push(`3 concurrent ensure calls -> already_running pid ${before.pid}; 1 listener`);
  });

  await test('4. Handshake, version negotiation, request/reply correlation', async (ev) => {
    const s = await linkState();
    assert(s.backend.protocol === 1 && s.backend.version, 'protocol/version exchanged');
    const goals = await swEval(() => globalThis.__wgaDebug.link.request('list_goals'));
    assert(goals.goals.length >= 10, `goals listed: ${goals.goals.length}`);
    assert(goals.errors.length === 0, `goal errors ${goals.errors}`);
    const replies = await swEval(() => Promise.all(['get_state', 'list_goals', 'get_state'].map((op) => globalThis.__wgaDebug.link.request(op))));
    assert(replies[0] && 'run' in replies[0] && replies[1].goals && 'run' in replies[2], 'parallel requests matched to their replies');
    // Wrong protocol / wrong token are refused by the backend.
    const probe = async (auth) => new Promise((resolve) => {
      const ws = new WebSocket(`ws://127.0.0.1:${CONFIG.port}`);
      ws.onopen = () => ws.send(JSON.stringify(auth));
      ws.onmessage = (m) => { resolve(JSON.parse(m.data)); ws.close(); };
      ws.onerror = () => resolve({ error: true });
    });
    const mismatch = await probe({ type: 'auth', token: CONFIG.token, protocol: 2 });
    const badToken = await probe({ type: 'auth', token: 'wrong', protocol: 1 });
    assert(mismatch.status === 'denied' && mismatch.reason === 'protocol_mismatch', `mismatch ${JSON.stringify(mismatch)}`);
    assert(badToken.status === 'denied' && badToken.reason === 'bad_token', `bad token ${JSON.stringify(badToken)}`);
    assert((await linkState()).status === 'connected', 'extension link unaffected by refused clients');
    ev.push(`backend v${s.backend.version}/protocol ${s.backend.protocol}; ${goals.goals.length} goals; protocol 2 -> denied(protocol_mismatch); bad token -> denied(bad_token)`);
  });

  await test('5. Reconnects after the connection drops', async (ev) => {
    const pid = (await health()).pid;
    const t0 = Date.now();
    await swEval(() => globalThis.__wgaDebug.link.ws.close(4100, 'test drop'));
    await waitUntil(async () => (await linkState()).status !== 'connected', { timeout: 3000, desc: 'disconnect noticed' });
    await waitUntil(async () => (await linkState()).status === 'connected', { timeout: 20000, desc: 'reconnected' });
    assert((await health()).pid === pid, 'same backend process');
    const manual = Date.now();
    await swEval(() => globalThis.__wgaDebug.link.reconnect());
    await waitUntil(async () => (await linkState()).status === 'connected', { timeout: 15000, desc: 'manual reconnect' });
    ev.push(`auto-reconnect in ${manual - t0} ms; manual Reconnect in ${Date.now() - manual} ms`);
  });

  await test('6. Unexpected backend termination is detected and the backend restarted', async (ev) => {
    const old = await health();
    const t0 = Date.now();
    execFileSync('taskkill', ['/PID', String(old.pid), '/F']);
    await waitUntil(async () => (await linkState()).status !== 'connected', { timeout: 5000, desc: 'loss detected' });
    const h = await waitUntil(async () => { const x = await health(); return x && x.pid !== old.pid ? x : null; }, { timeout: 30000, desc: 'new backend' });
    await waitUntil(async () => (await linkState()).status === 'connected', { timeout: 20000, desc: 'reconnected to new backend' });
    const hist = (await swEval(() => globalThis.__wgaDebug.store.get().history)).map((x) => x.text);
    assert(hist.some((t) => /connection lost/i.test(t)), 'loss recorded in history');
    ev.push(`killed pid ${old.pid}; restarted as pid ${h.pid}; connected again after ${Date.now() - t0} ms`);
  });

  await test('7. Manual restart from the extension (owned backend)', async (ev) => {
    const old = await health();
    const reply = await swEval(() => globalThis.__wgaDebug.link.restartBackend());
    assert(reply.ok && reply.restarted, `restart reply ${JSON.stringify({ ...reply, token: undefined })}`);
    await waitUntil(async () => (await linkState()).status === 'connected', { desc: 'connected after restart' });
    const h = await health();
    assert(h.pid !== old.pid, 'new pid after restart');
    ev.push(`restart: pid ${old.pid} -> ${h.pid}`);
  });

  await test('8. Goal: rename customer — cookie-wall interrupt, scoped Save among identical buttons, verified', async (ev) => {
    const { page, tabId } = await openSite(`${BASE}/crm.html`);
    const { runId } = await startGoal(tabId, 'demo-crm-rename-customer');
    let midShot = null;
    await waitUntil(async () => {
      const r = await runState();
      if (!midShot && r?.step_index === 4 && r.state === 'running') midShot = await shot(page, '08-rename-in-progress');
      return r?.run_id === runId && TERMINAL.has(r.state);
    }, { timeout: 90000, interval: 100, desc: 'rename run' });
    const r = await runState();
    assert(r.state === 'succeeded', `state ${r.state}: ${r.message} ${JSON.stringify(r.error)}`);
    const fx = await fixture(page);
    assert(fx.saves === 1 && fx.noteSaves === 0, `saves=${fx.saves} noteSaves=${fx.noteSaves} (must save the Edit dialog, not Quick note)`);
    assert(fx.consent === 'necessary', `cookie wall handled by interrupt (consent=${fx.consent})`);
    assert(await page.locator('tr', { hasText: 'Ada King' }).count() === 1, 'row renamed in DOM');
    const log = runLog(runId);
    assert(log.some((e) => e.kind === 'recover' && /cookie-wall/.test(e.text)), 'interrupt logged');
    const verified = log.filter((e) => e.kind === 'step' && e.status === 'verified').map((e) => e.step_id);
    assert(['wait-table', 'open-note-panel', 'open-edit', 'type-name', 'save'].every((s) => verified.includes(s)), `all steps verified: ${verified}`);
    // Overlay reflected the real actions: each dispatched action had an 'act' highlight with its label.
    const overlay = await swEval(() => globalThis.__wgaDebug.executor.overlayLog.map((o) => ({ op: o.op, state: o.state, text: o.text })));
    const acts = overlay.filter((o) => o.op === 'state' && o.state === 'act').map((o) => o.text);
    for (const label of ['Open the Quick note panel', 'Open the Edit dialog for Ada Lovelace', 'Enter the new name', 'Save inside the Edit customer dialog']) {
      assert(acts.some((t) => t.startsWith(label)), `overlay showed act "${label}" (got ${acts})`);
    }
    // Scope boxes are drawn by the page library when it resolves a container: read its journal.
    const journal = await swEval((t) => globalThis.__wgaDebug.callPage(t, 'overlay_journal', {}), tabId);
    const scopes = journal.journal.filter((j) => j.op === 'scope').map((j) => j.text);
    assert(scopes.some((t) => /Inspecting dialog "Edit customer"/.test(t)), `dialog scope highlighted (got ${[...new Set(scopes)]})`);
    assert(scopes.some((t) => /Inspecting row "Ada Lovelace/.test(t)), 'row scope highlighted');
    assert(midShot, 'captured an in-progress screenshot');
    ev.push(midShot, await shot(page, '08-rename-done'));
    ev.push(`verified steps: ${verified.join(', ')}; fixture: ${JSON.stringify(fx)}`);
    // Overlay is removed after the run (lingers ~6 s for the final status).
    await waitUntil(() => page.evaluate(() => !document.querySelector('wga-overlay')), { timeout: 12000, desc: 'overlay cleanup' });
  });

  await test('9. Sensitive action requires approval — approve', async (ev) => {
    const { page, tabId } = await openSite(`${BASE}/crm.html?consent=0`);
    const { popup, runId } = await startGoal(tabId, 'demo-crm-delete-customer');
    await waitUntil(async () => (await runState())?.state === 'waiting_human', { timeout: 30000, desc: 'approval request' });
    await popup.waitForSelector('#confirmCard:not([hidden])');
    const text = await popup.textContent('#confirmText');
    assert(/Delete/.test(text) && /Alan Turing/.test(text), `confirmation describes the action: ${text}`);
    assert((await fixture(page)).deletes === 0, 'nothing deleted before approval');
    ev.push(await shot(popup, '09-approval-popup'), await shot(page, '09-approval-page'));
    await popup.click('#btnApprove');
    const r = await waitTerminal(runId);
    assert(r.state === 'succeeded', `state ${r.state} ${r.message}`);
    assert((await fixture(page)).deletes === 1, 'deleted once');
    ev.push(`confirmation: "${text}" -> approved -> succeeded`);
  });

  await test('10. Sensitive action requires approval — reject stops safely', async (ev) => {
    const { page, tabId } = await openSite(`${BASE}/crm.html?consent=0`);
    const { popup, runId } = await startGoal(tabId, 'demo-crm-delete-customer', { customer: 'Grace Hopper' });
    await waitUntil(async () => (await runState())?.state === 'waiting_human', { timeout: 30000, desc: 'approval request' });
    await popup.waitForSelector('#confirmCard:not([hidden])');
    await popup.click('#btnReject');
    const r = await waitTerminal(runId);
    assert(r.state === 'stopped', `state ${r.state}`);
    assert((await fixture(page)).deletes === 0, 'nothing deleted');
    assert(await page.locator('tr', { hasText: 'Grace Hopper' }).count() === 1, 'row still present');
    ev.push(`rejected -> ${r.state}: ${r.message}`);
  });

  await test('11. Menu, native <dialog> in the top layer, native select, verified export', async (ev) => {
    const { page, tabId } = await openSite(`${BASE}/crm.html?consent=0`);
    const { runId } = await startGoal(tabId, 'demo-crm-export', { format: 'Excel' });
    let topLayer = null;
    await waitUntil(async () => {
      const r = await runState();
      if (topLayer === null && r?.step_index === 3) {
        topLayer = await page.evaluate(() => {
          const h = document.querySelector('wga-overlay');
          return { overlayOnTop: !!h && h.matches(':popover-open'), dialogModal: document.querySelector('#exportDialog').matches(':modal') };
        }).catch(() => null);
        if (topLayer) ev.push(await shot(page, '11-overlay-above-modal-dialog'));
      }
      return r?.run_id === runId && TERMINAL.has(r.state);
    }, { timeout: 60000, interval: 100, desc: 'export run' });
    const r = await runState();
    assert(r.state === 'succeeded', `state ${r.state} ${r.message} ${JSON.stringify(r.error)}`);
    assert(await page.textContent('#exportResult') === 'Exported 4 customers as Excel', 'export result');
    assert(topLayer?.overlayOnTop && topLayer?.dialogModal, `overlay rendered in the top layer above the modal dialog: ${JSON.stringify(topLayer)}`);
    ev.push(`result "${await page.textContent('#exportResult')}"; top layer: ${JSON.stringify(topLayer)}`);
  });

  await test('12. Edge cases: scoped Save, closed shadow DOM, iframe, lazy feed, late-enabled button', async (ev) => {
    const { page, tabId } = await openSite(`${BASE}/edge.html`);
    const { popup, runId } = await startGoal(tabId, 'demo-edge-cases');
    // "Subscribe" may be a paid subscription: the safety gate asks, and the test answers like a user would.
    let approved = null;
    await waitUntil(async () => {
      const s = await swEval(() => globalThis.__wgaDebug.store.get());
      if (!approved && s.run?.state === 'waiting_human' && s.confirmation) {
        approved = s.confirmation;
        await popup.click('#btnApprove');
      }
      return s.run?.run_id === runId && TERMINAL.has(s.run.state);
    }, { timeout: 120000, interval: 150, desc: 'edge-case run' });
    const r = await runState();
    assert(approved?.step_id === 'subscribe' && approved.category === 'financial', `approval requested for Subscribe (${JSON.stringify(approved)})`);
    assert(r.state === 'succeeded', `state ${r.state} ${r.message} ${JSON.stringify(r.error)}`);
    const fx = await fixture(page);
    assert(fx.shipSaves === 1 && fx.billSaves === 0, `ship=${fx.shipSaves} bill=${fx.billSaves}`);
    assert(fx.subscribes === 1 && fx.reportClicks === 1, `subscribes=${fx.subscribes} reportClicks=${fx.reportClicks}`);
    assert(fx.feedLoads > 4, `feed lazily loaded ${fx.feedLoads} pages`);
    const coupon = await page.frameLocator('iframe').locator('#out').textContent();
    assert(coupon === 'Coupon SAVE10 applied', `coupon "${coupon}"`);
    ev.push(await shot(page, '12-edge-done'), `fixture ${JSON.stringify(fx)}; iframe: ${coupon}`);
  });

  await test('13. Identical buttons without a scope are refused as ambiguous (no guess, no click)', async (ev) => {
    const { page, tabId } = await openSite(`${BASE}/edge.html`);
    const { runId } = await startGoal(tabId, 't-ambiguous-save');
    const r = await waitTerminal(runId);
    assert(r.state === 'failed' && r.error?.code === 'ambiguous', `expected ambiguous failure, got ${r.state} ${JSON.stringify(r.error)}`);
    assert(r.error.details?.count === 2, `two candidates reported (${r.error.details?.count})`);
    const fx = await fixture(page);
    assert(fx.shipSaves === 0 && fx.billSaves === 0, 'nothing clicked');
    ev.push(`error: ${r.error.message}; candidates: ${r.error.details.candidates.map((c) => c.dialog).join(' | ')}`);
    ev.push(r.error.screenshot ? path.relative(ROOT, r.error.screenshot) : 'no failure screenshot');
  });

  await test('14. Page-reported validation error stops the run; Save is not repeated', async (ev) => {
    const { page, tabId } = await openSite(`${BASE}/crm.html?consent=0`);
    const { runId } = await startGoal(tabId, 't-invalid-email');
    const r = await waitTerminal(runId);
    assert(r.state === 'failed' && r.error?.code === 'page_reported_error', `got ${r.state} ${JSON.stringify(r.error)}`);
    assert(/valid email/i.test(r.error.message), `message ${r.error.message}`);
    const fx = await fixture(page);
    assert(fx.saveAttempts === 1 && fx.saves === 0, `saveAttempts=${fx.saveAttempts} saves=${fx.saves}`);
    ev.push(`error: ${r.error.message}; saveAttempts=${fx.saveAttempts}`);
  });

  await test('15. Obstruction recovery: waits out a covering overlay, then clicks', async (ev) => {
    const { page, tabId } = await openSite(`${BASE}/crm.html?consent=0&tour=1`);
    const { runId } = await startGoal(tabId, 't-obstructed-tour');
    const r = await waitTerminal(runId);
    assert(r.state === 'succeeded', `state ${r.state} ${r.message} ${JSON.stringify(r.error)}`);
    const log = runLog(runId);
    const covered = log.find((e) => e.kind === 'phase' && /covered by/i.test(e.text));
    assert(covered, 'obstruction diagnosed in the run log');
    ev.push(`diagnosis: "${covered.text}"`);
  });

  await test('16. Stale elements: table re-renders every 700 ms during the flow', async (ev) => {
    const { page, tabId } = await openSite(`${BASE}/crm.html?consent=0&live=1`);
    const { runId } = await startGoal(tabId, 't-stale-live');
    const r = await waitTerminal(runId);
    assert(r.state === 'succeeded', `state ${r.state} ${r.message} ${JSON.stringify(r.error)}`);
    const fx = await fixture(page);
    assert(fx.saves === 1 && fx.renders >= 3, `saves=${fx.saves} renders=${fx.renders}`);
    ev.push(`table re-rendered ${fx.renders} times; saved once`);
  });

  await test('17. Full-page navigation, re-injection after navigation, data extraction', async (ev) => {
    const { tabId } = await openSite(`${BASE}/crm.html?consent=0`);
    const { runId } = await startGoal(tabId, 't-navigation');
    const r = await waitTerminal(runId);
    assert(r.state === 'succeeded', `state ${r.state} ${r.message} ${JSON.stringify(r.error)}`);
    assert(r.outputs?.read?.revenue === '$1,234,500' && r.outputs?.read?.active === '3', `outputs ${JSON.stringify(r.outputs)}`);
    ev.push(`extracted ${JSON.stringify(r.outputs.read)}`);
  });

  await test('18. Pause and resume: no action while paused', async (ev) => {
    const { page, tabId } = await openSite(`${BASE}/edge.html?slow=5000`);
    const t0 = Date.now();
    const { popup, runId } = await startGoal(tabId, 't-slow-pause');
    await waitUntil(async () => (await runState())?.state === 'running', { desc: 'running' });
    await popup.click('#btnPause');
    await waitUntil(async () => (await runState())?.state === 'paused', { timeout: 5000, desc: 'paused' });
    await sleep(Math.max(0, 7000 - (Date.now() - t0))); // the button became clickable at 5 s
    assert((await fixture(page)).reportClicks === 0, 'no click while paused');
    ev.push(await shot(popup, '18-paused-popup'));
    await popup.click('#btnResume');
    const r = await waitTerminal(runId);
    assert(r.state === 'succeeded', `state ${r.state}`);
    assert((await fixture(page)).reportClicks === 1, 'clicked exactly once after resume');
  });

  await test('19. Stop cancels the run promptly and clears the overlay', async (ev) => {
    const { page, tabId } = await openSite(`${BASE}/edge.html?slow=5000`);
    const { popup, runId } = await startGoal(tabId, 't-slow-pause');
    await waitUntil(async () => (await runState())?.state === 'running', { desc: 'running' });
    await sleep(800);
    const t0 = Date.now();
    await popup.click('#btnStop');
    const r = await waitTerminal(runId, 10000);
    assert(r.state === 'stopped', `state ${r.state}`);
    ev.push(`stopped ${Date.now() - t0} ms after Stop`);
    await sleep(6000);
    assert((await fixture(page)).reportClicks === 0, 'no click after stop');
    await waitUntil(() => page.evaluate(() => !document.querySelector('wga-overlay')), { timeout: 10000, desc: 'overlay removed' });
  });

  await test('20. Interrupted execution: connection drops mid-run, run resumes and is verified', async (ev) => {
    const { page, tabId } = await openSite(`${BASE}/edge.html?slow=5000`);
    const { runId } = await startGoal(tabId, 't-slow-pause');
    await waitUntil(async () => (await runState())?.state === 'running', { desc: 'running' });
    await sleep(1500);
    await swEval(() => globalThis.__wgaDebug.link.ws.close(4100, 'test drop mid-run'));
    const r = await waitTerminal(runId, 60000);
    assert(r.state === 'succeeded', `state ${r.state} ${r.message}`);
    const states = runLog(runId).filter((e) => e.kind === 'state').map((e) => e.text);
    assert(states.some((t) => /Lost the extension connection/.test(t)) && states.some((t) => /Reconnected/.test(t)), `interruption handled: ${states}`);
    assert((await fixture(page)).reportClicks === 1, 'clicked exactly once');
    ev.push(`state transitions: ${states.join(' | ')}`);
  });

  await test('21. ai-web-auto core API compatibility (WebSocketServer convenience methods drive this extension)', async (ev) => {
    const { tabId } = await openSite(`${BASE}/crm.html?consent=0`);
    await swEval(() => globalThis.__wgaDebug.link.stopBackend());
    await waitUntil(async () => !(await health()), { timeout: 10000, desc: 'backend stopped' });
    const out = path.join(RESULTS, 'compat.json');
    const proc = spawn(PY, [path.join(HERE, 'compat_check.py'), CONFIG_PATH, out, String(tabId)], { stdio: 'inherit' });
    // The core server has no /health; wait for its port instead.
    await waitUntil(() => new Promise((resolve) => {
      const s = net.connect(CONFIG.port, '127.0.0.1', () => { s.destroy(); resolve(true); });
      s.on('error', () => resolve(false));
      s.setTimeout(300, () => { s.destroy(); resolve(false); });
    }), { timeout: 10000, desc: 'core server listening' });
    await swEval(() => globalThis.__wgaDebug.link.start('test'));
    const code = await new Promise((resolve) => proc.on('exit', resolve));
    const report = JSON.parse(fs.readFileSync(out, 'utf8'));
    assert(code === 0 && report.ok, `compat check failed: ${JSON.stringify(report)}`);
    ev.push(`core methods: ${Object.keys(report.checks).join(', ')}`);
    // Restore the native-host backend for any later tests.
    await swEval(() => globalThis.__wgaDebug.link.start('test-restore'));
    await waitUntil(async () => (await linkState()).status === 'connected', { timeout: 30000, desc: 'restored' });
  });

  await teardown();
  const passed = results.filter((r) => r.ok).length;
  const summary = { date: new Date().toISOString(), userAgent, headless: !process.env.HEADED, passed, failed: results.length - passed, results };
  fs.writeFileSync(path.join(RESULTS, 'e2e-results.json'), JSON.stringify(summary, null, 2));
  process.stdout.write(`\n${passed}/${results.length} E2E tests passed. Results: ${path.relative(ROOT, path.join(RESULTS, 'e2e-results.json'))}\n`);
  process.exit(passed === results.length ? 0 : 1);
}

main().catch(async (e) => {
  console.error(e);
  await teardown();
  process.exit(2);
});
