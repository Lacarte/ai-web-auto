/**
 * Chrome DevTools Protocol session per tab: trusted input, screenshots,
 * network-idle tracking, and native JS dialog detection.
 *
 * Input patterns come from ai-web-auto-extension/js/service-worker.js (proven
 * on React/Radix sites): mouse press+release at resolved coordinates,
 * Input.insertText for text, focus BEFORE select-all. Added here: mouseMoved
 * before pressing (hover-dependent UIs), editing `commands` for select-all and
 * delete (layout-independent), Enter with text "\r" so forms submit.
 */

export class CdpError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}

const attached = new Set();
const inflight = new Map();         // tabId -> Map(requestId -> startedAt)
const dialogs = new Map();          // tabId -> { dialog_type, message }
const dialogWaiters = new Map();    // tabId -> Set(fn)
const listeners = { dialog: new Set(), detach: new Set() };

// Registered at module top level so they survive service-worker restarts.
chrome.debugger.onEvent.addListener((source, method, params) => {
  const tabId = source.tabId;
  if (tabId == null) return;
  switch (method) {
    case 'Network.requestWillBeSent': {
      if (['EventSource', 'WebSocket', 'Ping', 'Other'].includes(params.type)) return;
      if (!inflight.has(tabId)) inflight.set(tabId, new Map());
      inflight.get(tabId).set(params.requestId, Date.now());
      break;
    }
    case 'Network.loadingFinished':
    case 'Network.loadingFailed':
      inflight.get(tabId)?.delete(params.requestId);
      break;
    case 'Page.frameNavigated':
      if (!params.frame.parentId) inflight.set(tabId, new Map());
      break;
    case 'Page.javascriptDialogOpening': {
      const info = { dialog_type: params.type, message: String(params.message || '').slice(0, 300) };
      dialogs.set(tabId, info);
      for (const fn of dialogWaiters.get(tabId) || []) fn(info);
      for (const fn of listeners.dialog) fn(tabId, true, info);
      break;
    }
    case 'Page.javascriptDialogClosed':
      dialogs.delete(tabId);
      for (const fn of listeners.dialog) fn(tabId, false, {});
      break;
    default: break;
  }
});

chrome.debugger.onDetach.addListener((source, reason) => {
  if (source.tabId == null) return;
  attached.delete(source.tabId);
  inflight.delete(source.tabId);
  for (const fn of listeners.detach) fn(source.tabId, reason);
});

export function onDialog(fn) { listeners.dialog.add(fn); }
export function onDetach(fn) { listeners.detach.add(fn); }
export function openDialog(tabId) { return dialogs.get(tabId) || null; }

/** Resolves when a JS dialog opens in the tab (used to abort calls the dialog would block). */
export function whenDialogOpens(tabId) {
  let fn;
  const promise = new Promise((resolve) => {
    fn = resolve;
    if (!dialogWaiters.has(tabId)) dialogWaiters.set(tabId, new Set());
    dialogWaiters.get(tabId).add(fn);
  });
  return { promise, cancel: () => dialogWaiters.get(tabId)?.delete(fn) };
}

export function isAttached(tabId) { return attached.has(tabId); }

export async function ensureAttached(tabId) {
  if (attached.has(tabId)) return;
  try {
    await chrome.debugger.attach({ tabId }, '1.3');
  } catch (e) {
    const msg = String(e.message || e);
    if (/already attached/i.test(msg)) {
      // Possibly our own session from before a service-worker restart.
      try {
        await chrome.debugger.sendCommand({ tabId }, 'Runtime.evaluate', { expression: '1' });
      } catch {
        throw new CdpError('debugger_busy', 'Another debugger is attached to this tab (DevTools or another extension). Close it and retry.');
      }
    } else if (/chrome:\/\/|chrome-extension:\/\/|Cannot access/i.test(msg)) {
      throw new CdpError('unsupported_page', `This page can't be automated: ${msg}`);
    } else {
      throw new CdpError('debugger_failed', msg);
    }
  }
  attached.add(tabId);
  await send(tabId, 'Page.enable');
  await send(tabId, 'Network.enable', { maxTotalBufferSize: 0, maxResourceBufferSize: 0 });
}

export async function detach(tabId) {
  if (!attached.has(tabId)) return;
  attached.delete(tabId);
  inflight.delete(tabId);
  try { await chrome.debugger.detach({ tabId }); } catch { /* already gone */ }
}

export async function send(tabId, method, params = {}) {
  try {
    return await chrome.debugger.sendCommand({ tabId }, method, params);
  } catch (e) {
    const msg = String(e.message || e);
    if (/not attached/i.test(msg)) attached.delete(tabId);
    throw new CdpError('cdp_failed', `${method}: ${msg}`);
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function move(tabId, x, y) {
  await send(tabId, 'Input.dispatchMouseEvent', { type: 'mouseMoved', x, y, button: 'none' });
}

export async function click(tabId, x, y) {
  await move(tabId, x, y);
  await send(tabId, 'Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', buttons: 1, clickCount: 1 });
  await send(tabId, 'Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', buttons: 0, clickCount: 1 });
}

export async function wheel(tabId, x, y, deltaY) {
  await send(tabId, 'Input.dispatchMouseEvent', { type: 'mouseWheel', x, y, deltaX: 0, deltaY });
}

/** Input.insertText in chunks: the only text input React contentEditable reliably accepts. */
export async function insertText(tabId, text) {
  const chunk = 50;
  for (let i = 0; i < text.length; i += chunk) {
    await send(tabId, 'Input.insertText', { text: text.slice(i, i + chunk) });
  }
}

const KEYS = {
  Enter: { key: 'Enter', code: 'Enter', keyCode: 13, text: '\r' },
  Escape: { key: 'Escape', code: 'Escape', keyCode: 27 },
  Tab: { key: 'Tab', code: 'Tab', keyCode: 9 },
  Backspace: { key: 'Backspace', code: 'Backspace', keyCode: 8 },
  Delete: { key: 'Delete', code: 'Delete', keyCode: 46 },
  ArrowDown: { key: 'ArrowDown', code: 'ArrowDown', keyCode: 40 },
  ArrowUp: { key: 'ArrowUp', code: 'ArrowUp', keyCode: 38 },
  ArrowLeft: { key: 'ArrowLeft', code: 'ArrowLeft', keyCode: 37 },
  ArrowRight: { key: 'ArrowRight', code: 'ArrowRight', keyCode: 39 },
  Space: { key: ' ', code: 'Space', keyCode: 32, text: ' ' },
  PageUp: { key: 'PageUp', code: 'PageUp', keyCode: 33 },
  PageDown: { key: 'PageDown', code: 'PageDown', keyCode: 34 },
  Home: { key: 'Home', code: 'Home', keyCode: 36 },
  End: { key: 'End', code: 'End', keyCode: 35 },
};

const MODIFIER_BITS = { alt: 1, ctrl: 2, meta: 4, shift: 8 };

export async function key(tabId, name, modifiers = []) {
  const mods = modifiers.reduce((m, k) => m | (MODIFIER_BITS[k] || 0), 0);
  const def = KEYS[name] || (name.length === 1
    ? { key: name, code: /[a-z]/i.test(name) ? `Key${name.toUpperCase()}` : '', keyCode: name.toUpperCase().charCodeAt(0), text: name }
    : { key: name, code: name, keyCode: 0 });
  const text = mods & (MODIFIER_BITS.ctrl | MODIFIER_BITS.meta | MODIFIER_BITS.alt) ? undefined : def.text;
  await send(tabId, 'Input.dispatchKeyEvent', {
    type: text ? 'keyDown' : 'rawKeyDown', key: def.key, code: def.code, windowsVirtualKeyCode: def.keyCode,
    modifiers: mods, text, unmodifiedText: text,
  });
  await sleep(20);
  await send(tabId, 'Input.dispatchKeyEvent', { type: 'keyUp', key: def.key, code: def.code, windowsVirtualKeyCode: def.keyCode, modifiers: mods });
}

/** Select-all then delete in the FOCUSED field (callers verify focus first). */
export async function clearFocused(tabId) {
  await send(tabId, 'Input.dispatchKeyEvent', { type: 'rawKeyDown', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 2, commands: ['selectAll'] });
  await send(tabId, 'Input.dispatchKeyEvent', { type: 'keyUp', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 2 });
  await deleteBackward(tabId);
}

export async function deleteBackward(tabId) {
  await send(tabId, 'Input.dispatchKeyEvent', { type: 'rawKeyDown', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8, commands: ['deleteBackward'] });
  await send(tabId, 'Input.dispatchKeyEvent', { type: 'keyUp', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8 });
}

export async function screenshot(tabId) {
  const { data } = await send(tabId, 'Page.captureScreenshot', { format: 'png' });
  return data;
}

export async function viewport(tabId) {
  const m = await send(tabId, 'Page.getLayoutMetrics');
  const v = m.cssVisualViewport || m.visualViewport || m.layoutViewport;
  return { width: v.clientWidth, height: v.clientHeight };
}

export async function evaluate(tabId, expression) {
  return send(tabId, 'Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
}

/** True once no tracked request has been open for `idleMs` (requests older than 10s are ignored: long-polling). */
export async function networkIdle(tabId, { idleMs = 500, timeoutMs = 4000 } = {}) {
  if (!attached.has(tabId)) return { idle: true, tracked: false };
  const deadline = Date.now() + timeoutMs;
  let quietSince = null;
  while (Date.now() < deadline) {
    const now = Date.now();
    const open = [...(inflight.get(tabId)?.values() || [])].filter((t) => now - t < 10000).length;
    if (open === 0) {
      quietSince = quietSince ?? now;
      if (now - quietSince >= idleMs) return { idle: true, tracked: true };
    } else {
      quietSince = null;
    }
    await sleep(100);
  }
  return { idle: false, tracked: true, open: [...(inflight.get(tabId)?.values() || [])].length };
}
