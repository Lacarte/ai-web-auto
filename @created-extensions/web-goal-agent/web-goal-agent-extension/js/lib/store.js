/**
 * Service-worker state: what the popup shows. Persisted to storage.session so
 * an MV3 worker restart (or a disconnected backend) doesn't blank the popup;
 * the backend's state_snapshot replaces it on every reconnect.
 */

import { TERMINAL_STATES } from './protocol.js';

const KEY = 'wga.state';
const HISTORY_MAX = 60;
const ports = new Set();

let state = {
  link: { status: 'idle', detail: 'Not connected yet' },
  run: null,
  history: [],
  confirmation: null,
  goals: null,
  updatedAt: Date.now(),
};
let persistTimer = null;

export async function hydrate() {
  try {
    const saved = (await chrome.storage.session.get(KEY))[KEY];
    if (saved) state = { ...state, ...saved, link: state.link };
  } catch { /* first run */ }
}

export function get() { return state; }

export function addPort(port) {
  ports.add(port);
  port.onDisconnect.addListener(() => ports.delete(port));
  port.postMessage({ type: 'state', state });
}

export function update(patch) {
  state = { ...state, ...patch, updatedAt: Date.now() };
  for (const port of ports) {
    try { port.postMessage({ type: 'state', state }); } catch { ports.delete(port); }
  }
  updateBadge();
  clearTimeout(persistTimer);
  persistTimer = setTimeout(() => {
    chrome.storage.session.set({ [KEY]: { run: state.run, history: state.history, confirmation: state.confirmation } }).catch(() => {});
  }, 200);
}

export function pushHistory(entry) {
  const history = [...state.history, { ts: Date.now(), ...entry }].slice(-HISTORY_MAX);
  update({ history });
}

/** Fold a backend run_event into the run summary the popup renders. */
export function applyRunEvent(ev) {
  const run = { ...(state.run || {}) };
  if (run.run_id !== ev.run_id) Object.assign(run, { run_id: ev.run_id, steps: [], error: null, outputs: {} });
  Object.assign(run, {
    title: ev.title ?? run.title, state: ev.state ?? run.state, phase: ev.phase ?? run.phase,
    step_index: ev.step_index ?? run.step_index, total_steps: ev.total_steps ?? run.total_steps,
  });
  if (ev.kind === 'state' || ev.kind === 'phase' || ev.kind === 'result') run.message = ev.text;
  if (ev.kind === 'result') { run.outcome = ev.outcome; run.error = ev.error || null; run.outputs = ev.outputs || {}; }
  const patch = { run };
  if (ev.kind === 'confirm') patch.confirmation = ev.confirmation || null;
  else if (ev.state !== 'waiting_human') patch.confirmation = null;
  update(patch);
  if (['log', 'step', 'result', 'confirm', 'human', 'recover', 'state'].includes(ev.kind) && ev.text) {
    pushHistory({ kind: ev.kind, level: ev.level, text: ev.text, run_id: ev.run_id });
  }
}

export function applySnapshot(snapshot) {
  const r = snapshot?.run;
  if (!r) { update({ run: null, confirmation: null }); return; }
  update({
    run: { run_id: r.run_id, title: r.title, state: r.state, phase: r.phase, message: r.message,
      step_index: r.step_index, total_steps: r.total_steps, steps: r.steps, error: r.error, outputs: r.outputs,
      outcome: TERMINAL_STATES.has(r.state) ? r.state : undefined, mode: r.mode },
    confirmation: r.pending_confirmation || null,
    history: (r.history || []).map((h) => ({ ts: Date.parse(h.ts) || Date.now(), kind: h.kind, level: h.level, text: h.text, run_id: r.run_id })),
  });
}

function updateBadge() {
  const link = state.link?.status;
  const run = state.run;
  let text = '', color = '#6b7280', title = 'Web Goal Agent';
  if (link !== 'connected') {
    text = link === 'error' ? 'ERR' : link === 'starting' || link === 'connecting' ? '…' : 'OFF';
    color = link === 'error' ? '#dc2626' : '#6b7280';
    title += ` — backend ${link}`;
  }
  if (run && !TERMINAL_STATES.has(run.state)) {
    if (run.state === 'waiting_human') { text = '!'; color = '#c026d3'; title += ' — needs your approval'; }
    else if (run.state === 'paused') { text = '❚❚'; color = '#d97706'; }
    else if (run.state === 'interrupted') { text = '…'; color = '#d97706'; }
    else { text = 'RUN'; color = '#2563eb'; }
  }
  chrome.action.setBadgeText({ text }).catch(() => {});
  chrome.action.setBadgeBackgroundColor({ color }).catch(() => {});
  chrome.action.setTitle({ title }).catch(() => {});
}
