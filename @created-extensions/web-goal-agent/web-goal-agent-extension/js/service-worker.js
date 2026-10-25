/**
 * Web Goal Agent — service worker.
 *
 * Wiring only: the backend link (native host + WebSocket), the executor that
 * performs the backend's commands, the popup channel, and the in-page HUD.
 * Every listener is registered synchronously at top level so Chrome can wake a
 * suspended MV3 worker for it.
 */

import { BackendLink } from './lib/backend-link.js';
import * as cdp from './lib/cdp.js';
import { createExecutor } from './lib/executor.js';
import { callPage } from './lib/page.js';
import { KEEPALIVE_ALARM, TERMINAL_STATES } from './lib/protocol.js';
import * as store from './lib/store.js';

const link = new BackendLink({
  onCommand: (cmd) => executor.onCommand(cmd),
  onEvent: handleBackendEvent,
  onState: (s) => store.update({ link: s }),
});
const executor = createExecutor({ link });

// ── Backend events ──────────────────────────────────────────────────────────

const PHASE_STATE = { observe: 'inspect', plan: 'inspect', act: 'act', verify: 'wait', recover: 'retry' };

function handleBackendEvent(event, data) {
  switch (event) {
    case 'connected':
      store.pushHistory({ kind: 'link', level: 'ok', text: `Connected to backend v${data.backend_version} (pid ${data.pid}, started by ${data.started_by})` });
      break;
    case 'disconnected':
      store.pushHistory({ kind: 'link', level: 'warn', text: 'Backend connection lost — reconnecting' });
      break;
    case 'shutting_down':
      store.pushHistory({ kind: 'link', level: 'warn', text: `Backend shutting down (${data.reason})` });
      break;
    case 'state_snapshot':
      store.applySnapshot(data);
      break;
    case 'run_event':
      store.applyRunEvent(data);
      updateHud(data);
      break;
    default:
      break;
  }
}

/** Mirror real run events into the page HUD (the executor draws target boxes itself). */
function updateHud(ev) {
  const tabId = executor.bound.tabId;
  if (tabId == null || TERMINAL_STATES.has(ev.state) || ev.kind === 'result') return;
  let state = PHASE_STATE[ev.phase] || 'inspect';
  if (ev.state === 'waiting_human') state = 'human';
  else if (ev.state === 'paused' || ev.state === 'interrupted') state = 'wait';
  else if (ev.level === 'error') state = 'fail';
  else if (ev.kind === 'recover') state = 'retry';
  else if (ev.kind === 'step' && ev.level === 'ok') state = 'success';
  const phase = ev.state === 'running' ? (ev.phase || 'running') : ev.state.replace('_', ' ');
  executor.overlay(tabId, { op: 'hud', title: ev.title, phase, state, text: ev.text, step: ev.step_index, total: ev.total_steps });
}

// ── Browser events forwarded to the backend ─────────────────────────────────

chrome.tabs.onRemoved.addListener((tabId) => {
  if (tabId === executor.bound.tabId) link.sendEvent('tab_closed', { tab_id: tabId });
});

chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
  if (tabId === executor.bound.tabId && info.status === 'complete') {
    link.sendEvent('page_load', { url: tab.url, title: tab.title, tabId }); // keeps core SessionState in sync
  }
});

cdp.onDialog((tabId, open, info) => {
  if (tabId === executor.bound.tabId) link.sendEvent('js_dialog', { tab_id: tabId, open, ...info });
});

cdp.onDetach((tabId, reason) => {
  if (tabId === executor.bound.tabId) {
    link.sendEvent('debugger_detached', { tab_id: tabId, reason });
    store.pushHistory({ kind: 'link', level: 'warn', text: `Debugger detached from the tab (${reason})` });
  }
});

// ── Lifecycle: start, keep alive, reconnect ─────────────────────────────────

chrome.runtime.onInstalled.addListener(() => link.start('installed'));
chrome.runtime.onStartup.addListener(() => link.start('browser-startup'));
chrome.alarms.create(KEEPALIVE_ALARM, { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== KEEPALIVE_ALARM) return;
  const s = link.state.status;
  if (link.wanted && (s === 'disconnected' || s === 'error') && !link._starting) link.start('keepalive');
});

// ── Popup channel ───────────────────────────────────────────────────────────

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== 'popup') return;
  store.addPort(port);
  if (!link.connected && link.state.status !== 'starting' && link.state.status !== 'connecting') link.start('popup');
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id || msg?.channel !== 'popup') return false;
  handlePopup(msg).then((result) => sendResponse({ ok: true, result }), (e) => sendResponse({ ok: false, error: String(e.message || e) }));
  return true;
});

async function handlePopup(msg) {
  switch (msg.op) {
    case 'status': return store.get();
    case 'backend:reconnect': return link.reconnect();
    case 'backend:start': return link.start('user');
    case 'backend:restart': return link.restartBackend();
    case 'backend:stop': return link.stopBackend();
    case 'settings:save': {
      await chrome.storage.local.set({ 'wga.settings': msg.settings || {} });
      if (msg.manualConn !== undefined) {
        if (msg.manualConn?.token) await chrome.storage.local.set({ 'wga.manualConn': msg.manualConn });
        else await chrome.storage.local.remove('wga.manualConn');
      }
      return link.reconnect();
    }
    case 'ui': {
      const result = await link.request(msg.request, msg.args || {});
      if (msg.request === 'list_goals') store.update({ goals: result });
      if (['start_run', 'pause_run', 'resume_run', 'stop_run', 'confirm', 'get_state'].includes(msg.request) && result) {
        if (msg.request === 'get_state') store.applySnapshot(result);
        else store.applySnapshot({ run: result });
      }
      return result;
    }
    default: throw new Error(`unknown popup op ${msg.op}`);
  }
}

// ── Boot (also runs when Chrome wakes the worker) ───────────────────────────

store.hydrate().then(() => link.start('worker-start'));

// Test/diagnostic handle (extension context only; pages cannot reach it).
globalThis.__wgaDebug = { link, executor, store, cdp, callPage };
