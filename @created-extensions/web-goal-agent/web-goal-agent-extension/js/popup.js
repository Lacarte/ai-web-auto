/**
 * Popup: control panel. All state comes from the service worker over a port;
 * the popup only renders it and sends user intents. Site access is requested
 * here because chrome.permissions.request needs a user gesture.
 */

import { originOf, TERMINAL_STATES } from './lib/protocol.js';

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);

let state = null;
let tab = null;
let mode = 'spec';
let goals = [];
let goalErrors = [];
let ai = { available: false };
let goalsLoaded = false;

async function send(op, extra = {}) {
  const r = await chrome.runtime.sendMessage({ channel: 'popup', op, ...extra });
  if (!r?.ok) throw new Error(r?.error || 'request failed');
  return r.result;
}
const ui = (request, args = {}) => send('ui', { request, args });

function showError(code, message, details) {
  $('errorBox').hidden = false;
  $('errorCode').textContent = code;
  $('errorMessage').textContent = message;
  $('errorMore').hidden = !details;
  $('errorDetails').textContent = details ? JSON.stringify(details, null, 2) : '';
}

function clearLocalError() {
  if (!state?.run?.error) $('errorBox').hidden = true;
}

// ── Target tab and site access ───────────────────────────────────────────────

async function loadTab() {
  if (params.has('tab')) {
    document.body.classList.add('tab');
    tab = await chrome.tabs.get(Number(params.get('tab'))).catch(() => null);
  } else {
    [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  }
  await renderSite();
}

async function renderSite() {
  const origin = originOf(tab?.url || '');
  $('siteOrigin').textContent = origin || 'This page cannot be automated (open a website tab)';
  if (!origin) { $('sitePerm').textContent = ''; $('btnAuthorize').hidden = true; return; }
  const granted = await chrome.permissions.contains({ origins: [`${origin}/*`] });
  $('sitePerm').textContent = granted ? 'access granted' : 'not authorized';
  $('sitePerm').className = `pill ${granted ? 'ok' : 'no'}`;
  $('btnAuthorize').hidden = granted;
}

$('btnAuthorize').addEventListener('click', () => {
  const origin = originOf(tab?.url || '');
  if (!origin) return;
  chrome.permissions.request({ origins: [`${origin}/*`] }).then(renderSite, (e) => showError('permission', e.message));
});

// ── Goals ────────────────────────────────────────────────────────────────────

async function loadGoals() {
  goalsLoaded = true;
  try {
    const r = await ui('list_goals');
    goals = r.goals || [];
    goalErrors = r.errors || [];
    ai = r.ai_planner || { available: false };
  } catch (e) {
    goalsLoaded = false;
    $('goalDesc').textContent = `Could not load goals: ${e.message}`;
    return;
  }
  const select = $('goalSelect');
  select.replaceChildren();
  if (!goals.length) select.append(new Option('No goal specs found in the backend goals folder', ''));
  for (const g of goals) select.append(new Option(g.title, g.id));
  const wanted = params.get('goal') || (await chrome.storage.local.get('wga.lastGoal'))['wga.lastGoal'];
  if (wanted && goals.some((g) => g.id === wanted)) select.value = wanted;
  $('goalErrors').hidden = !goalErrors.length;
  $('goalErrors').textContent = goalErrors.length ? `${goalErrors.length} goal file(s) rejected: ${goalErrors.join(' | ')}` : '';
  renderGoal();
  render();
}

function selectedGoal() {
  return goals.find((g) => g.id === $('goalSelect').value) || null;
}

function renderGoal() {
  const g = selectedGoal();
  const box = $('goalInputs');
  box.replaceChildren();
  $('goalDesc').textContent = g ? `${g.description || ''} ${g.steps} steps · allowed: ${g.allowed_origins.join(', ')}`.trim() : '';
  if (!g) return;
  for (const [name, spec] of Object.entries(g.inputs || {})) {
    const id = `in_${name}`;
    const label = document.createElement('label');
    label.htmlFor = id;
    label.textContent = `${spec.description || name}${spec.required ? ' (required)' : ''}`;
    const input = document.createElement('input');
    input.id = id;
    input.dataset.input = name;
    input.type = spec.secret ? 'password' : 'text';
    input.autocomplete = 'off';
    if (spec.default != null) input.value = spec.default;
    input.addEventListener('input', render);
    box.append(label, input);
  }
}

$('goalSelect').addEventListener('change', () => {
  chrome.storage.local.set({ 'wga.lastGoal': $('goalSelect').value });
  renderGoal();
  render();
});
$('goalText').addEventListener('input', render);

for (const [btn, m] of [['tabSpec', 'spec'], ['tabText', 'text']]) {
  $(btn).addEventListener('click', () => {
    mode = m;
    $('tabSpec').setAttribute('aria-selected', String(m === 'spec'));
    $('tabText').setAttribute('aria-selected', String(m === 'text'));
    $('paneSpec').hidden = m !== 'spec';
    $('paneText').hidden = m !== 'text';
    render();
  });
}

// ── Run controls ─────────────────────────────────────────────────────────────

$('btnStart').addEventListener('click', () => {
  clearLocalError();
  const goal = selectedGoal();
  const origins = mode === 'spec' ? (goal?.allowed_origins || []) : [originOf(tab?.url || '')].filter(Boolean);
  if (!origins.length) { showError('no_site', 'Open the website you want to automate first.'); return; }
  // Must run synchronously inside the click: permission prompts need the user gesture.
  chrome.permissions.request({ origins: origins.map((o) => `${o}/*`) }).then(async () => {
    const granted = [];
    for (const o of origins) if (await chrome.permissions.contains({ origins: [`${o}/*`] })) granted.push(o);
    if (granted.length < origins.length) {
      showError('no_permission', `Site access was not granted for ${origins.filter((o) => !granted.includes(o)).join(', ')}.`);
      return;
    }
    await renderSite();
    const args = { tab_id: tab.id, granted_origins: granted };
    if (mode === 'spec') {
      args.goal_id = goal.id;
      args.inputs = {};
      for (const el of document.querySelectorAll('[data-input]')) if (el.value !== '') args.inputs[el.dataset.input] = el.value;
    } else {
      args.goal_text = $('goalText').value.trim();
    }
    try { await ui('start_run', args); } catch (e) { showError('start_failed', e.message); }
  }, (e) => showError('permission', e.message));
});

async function runOp(op) {
  clearLocalError();
  try { await ui(op, { run_id: state?.run?.run_id }); } catch (e) { showError(op, e.message); }
}
$('btnPause').addEventListener('click', () => runOp('pause_run'));
$('btnResume').addEventListener('click', () => runOp('resume_run'));
$('btnStop').addEventListener('click', () => runOp('stop_run'));

async function answer(approved) {
  const c = state?.confirmation;
  if (!c) return;
  try { await ui('confirm', { run_id: state.run.run_id, confirmation_id: c.id, approved }); } catch (e) { showError('confirm', e.message); }
}
$('btnApprove').addEventListener('click', () => answer(true));
$('btnReject').addEventListener('click', () => answer(false));

// ── Backend controls and settings ────────────────────────────────────────────

const backendOp = (op) => send(op).catch((e) => showError(op, e.message));
$('btnReconnect').addEventListener('click', () => backendOp('backend:reconnect'));
$('btnStartBackend').addEventListener('click', () => backendOp('backend:start'));
$('btnRestart').addEventListener('click', () => backendOp('backend:restart'));
$('btnStopBackend').addEventListener('click', () => backendOp('backend:stop'));

async function loadSettings() {
  const s = await chrome.storage.local.get(['wga.settings', 'wga.manualConn']);
  $('setAutoStart').checked = (s['wga.settings'] || {}).autoStart !== false;
  $('setPort').value = s['wga.manualConn']?.port || '';
  $('setToken').value = s['wga.manualConn']?.token || '';
}

$('btnSaveSettings').addEventListener('click', () => {
  const token = $('setToken').value.trim();
  const port = Number($('setPort').value) || 8766;
  send('settings:save', {
    settings: { autoStart: $('setAutoStart').checked },
    manualConn: token ? { host: '127.0.0.1', port, token } : null,
  }).catch((e) => showError('settings', e.message));
});

// ── Rendering ────────────────────────────────────────────────────────────────

const ICON = { ok: '✓', warn: '!', error: '✗', human: '?', info: '•' };

function render() {
  if (!state) return;
  const link = state.link || {};
  const connected = link.status === 'connected';
  $('linkDot').className = `dot ${link.status}`;
  $('linkText').textContent = connected ? `Backend v${link.backend?.version ?? '?'}` : (link.status || 'idle');
  $('linkCard').hidden = connected;
  $('linkDetail').textContent = link.detail || '';
  const tail = link.lastError?.log_tail;
  $('linkLog').hidden = !tail;
  $('linkLog').textContent = tail || '';
  $('btnRestart').hidden = link.native?.available === false;
  $('btnStartBackend').hidden = link.native?.available === false || link.status === 'starting';

  if (connected && !goalsLoaded) loadGoals();
  $('aiNote').textContent = ai.available ? 'Planned step by step by Claude; every step is verified, and sensitive actions ask you first.'
    : `Free-text goals need the AI planner, which is unavailable: ${ai.reason || 'not configured'}.`;

  const run = state.run;
  const active = run && !TERMINAL_STATES.has(run.state);
  const canStart = connected && !active && !!originOf(tab?.url || '')
    && (mode === 'spec' ? !!selectedGoal() && requiredInputsFilled() : ai.available && $('goalText').value.trim().length > 0);
  $('btnStart').disabled = !canStart;
  $('btnPause').disabled = !(active && run.state === 'running');
  $('btnResume').disabled = !(active && (run.state === 'paused' || (run.state === 'waiting_human' && !state.confirmation)));
  $('btnStop').disabled = !active;

  $('runState').textContent = run ? run.state.replace('_', ' ') : 'idle';
  $('runState').className = `pill state ${run?.state || ''}`;
  const total = run?.total_steps || 0;
  const idx = run?.step_index ?? -1;
  $('runStep').textContent = run ? `${run.title || ''}${total ? ` · step ${Math.min(idx + 1, total)}/${total}` : ''}` : 'No run yet';
  const done = run?.state === 'succeeded' ? total : Math.max(0, idx);
  $('runBar').style.width = total ? `${Math.round((done / total) * 100)}%` : '0%';
  $('runMessage').textContent = run?.message || '';
  if (run?.error) showError(run.error.code, run.error.message, run.error.details && Object.keys(run.error.details).length ? run.error.details : null);

  const c = state.confirmation;
  $('confirmCard').hidden = !c;
  if (c) {
    $('confirmText').textContent = c.description;
    $('confirmReason').textContent = `${c.category}: ${c.reason}`;
  }

  const list = $('history');
  list.replaceChildren();
  for (const h of [...(state.history || [])].reverse().slice(0, 20)) {
    const li = document.createElement('li');
    li.className = h.level || 'info';
    const ic = document.createElement('span');
    ic.className = 'ic';
    ic.textContent = ICON[h.level] || ICON.info;
    ic.setAttribute('aria-hidden', 'true');
    const body = document.createElement('span');
    const time = document.createElement('time');
    time.textContent = new Date(h.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    body.append(time, document.createTextNode(h.text));
    li.append(ic, body);
    list.append(li);
  }
}

function requiredInputsFilled() {
  const g = selectedGoal();
  if (!g) return false;
  return Object.entries(g.inputs || {}).every(([name, spec]) => !spec.required || (document.getElementById(`in_${name}`)?.value || '') !== '');
}

// ── Boot ─────────────────────────────────────────────────────────────────────

const port = chrome.runtime.connect({ name: 'popup' });
port.onMessage.addListener((msg) => {
  if (msg.type === 'state') { state = msg.state; render(); }
});
loadSettings();
loadTab().then(render);
