/**
 * Service Worker — CDP-first automation engine.
 *
 * Architecture: Service worker is the primary automation brain.
 * All clicks/typing go through Chrome DevTools Protocol (CDP).
 * Content script is only for data extraction and fallback.
 *
 * Hard-won lessons encoded:
 * - CDP Input.insertText is the ONLY method that works with React contentEditable
 * - CDP Input.dispatchMouseEvent is required for Radix UI / trusted events
 * - CDP click on input BEFORE Ctrl+A (otherwise selects entire page)
 * - chrome.scripting.executeScript world:'MAIN' bypasses CSP
 * - XPath for stable element targeting
 * - Single debugger attach/detach per interaction sequence
 * - chrome.alarms keepalive every 15s (MV3 kills SW after 30s)
 * - Banner/modal dismissal before automation
 */

import { WebSocketClient } from './modules/ws-client.js';
import { MessageRouter } from './modules/message-router.js';
import { diffSnapshots, computeStats, summarizeForAI } from './modules/dom-observer.js';

const WS_URL = 'ws://localhost:8765';
const ws = new WebSocketClient(WS_URL);
const router = new MessageRouter(ws);

// Tracks the action currently being handled so the sendResponse wrapper can
// label the outcome in the activity log.
let _currentAction = null;

// Wrap sendResponse so every command outcome (success/failure) is logged to
// the popup activity log without editing each individual command case.
const _origSendResponse = ws.sendResponse.bind(ws);
ws.sendResponse = (correlationId, status, data, error = null) => {
  if (_currentAction) {
    logResult(_currentAction, status, error || '');
  }
  return _origSendResponse(correlationId, status, data, error);
};

// ── DOM Observer state (per-tab snapshots for structured diffing) ──────────
const domSnapshots = new Map(); // tabId → { selector, html, recording, intervalId }

// Track the tab we're automating
let targetTabId = null;

// The last confirmed http(s) tab we drove. Used as a "sticky" preference so the
// target doesn't drift to the extension popup / chrome:// pages between commands.
let lastWebTabId = null;

// Track debugger attachment per tab to maintain single session
const attachedTabs = new Set();

// ── Network interception state ──────────────────────────────────────────────
// Per-tab capture of requests seen via the CDP Network domain. Populated by
// the chrome.debugger.onEvent listener below when capture is enabled.
const networkCapture = new Map(); // tabId → { enabled, requests: Map, order: [] }

// Current cursor position per tab (for relative moves / drag origin).
const cursorPos = new Map(); // tabId → { x, y }

// ── Boot ────────────────────────────────────────────────────────────────────

ws.connect();

// Keep service worker alive (MV3 kills after 30s inactivity)
chrome.alarms.create('keepalive', { periodInMinutes: 0.25 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'keepalive') {
    if (!ws.connected) {
      console.log('[SW] Alarm: not connected, retrying...');
      ws.connect();
    }
  }
});

// Auto-reconnect when token changes
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && changes.ws_auth_token) {
    const newVal = changes.ws_auth_token.newValue;
    if (newVal && newVal !== changes.ws_auth_token.oldValue) {
      console.log('[SW] Token changed, scheduling reconnect...');
      setTimeout(() => ws.reconnect(), 300);
    }
  }
});

// Clean up debugger on tab close
chrome.tabs.onRemoved.addListener((tabId) => {
  attachedTabs.delete(tabId);
  if (tabId === targetTabId) targetTabId = null;
  if (tabId === lastWebTabId) lastWebTabId = null;
});

// Clean up debugger on detach events (user closed devtools bar, etc.)
chrome.debugger.onDetach.addListener((source) => {
  if (source.tabId) {
    attachedTabs.delete(source.tabId);
    networkCapture.delete(source.tabId);
  }
});

// ── CDP event stream — network requests/responses when capture is on ────────
chrome.debugger.onEvent.addListener((source, method, params) => {
  const tabId = source.tabId;
  if (!tabId) return;
  const cap = networkCapture.get(tabId);
  if (!cap || !cap.enabled) return;

  if (method === 'Network.requestWillBeSent') {
    const r = {
      requestId: params.requestId,
      url: params.request.url,
      method: params.request.method,
      resourceType: params.type || '',
      status: null,
      mimeType: '',
      encodedDataLength: 0,
      fromCache: false,
      ts: params.wallTime || params.timestamp,
      requestHeaders: params.request.headers || {},
    };
    cap.requests.set(params.requestId, r);
    cap.order.push(params.requestId);
    // Cap memory: keep the most recent 500 requests.
    if (cap.order.length > 500) {
      const old = cap.order.shift();
      cap.requests.delete(old);
    }
  } else if (method === 'Network.responseReceived') {
    const r = cap.requests.get(params.requestId);
    if (r) {
      r.status = params.response.status;
      r.mimeType = params.response.mimeType || '';
      r.resourceType = params.type || r.resourceType;
      r.remoteIP = params.response.remoteIPAddress || '';
      r.fromCache = !!params.response.fromDiskCache;
    }
  } else if (method === 'Network.loadingFinished') {
    const r = cap.requests.get(params.requestId);
    if (r) r.encodedDataLength = params.encodedDataLength || r.encodedDataLength;
  } else if (method === 'Network.loadingFailed') {
    const r = cap.requests.get(params.requestId);
    if (r) { r.failed = true; r.errorText = params.errorText || ''; }

  // ── WebSocket lifecycle + live frames (trading feeds, chat, etc.) ──────
  } else if (method === 'Network.webSocketCreated') {
    cap.sockets = cap.sockets || new Map();
    cap.sockets.set(params.requestId, { url: params.url, frames: 0 });
  } else if (method === 'Network.webSocketFrameReceived' ||
             method === 'Network.webSocketFrameSent') {
    cap.frames = cap.frames || [];
    const payload = params.response?.payloadData ?? '';
    cap.frames.push({
      dir: method === 'Network.webSocketFrameSent' ? 'out' : 'in',
      opcode: params.response?.opcode,
      len: payload.length,
      // Keep a bounded text preview — trading ticks are short JSON/arrays.
      data: typeof payload === 'string' ? payload.slice(0, 400) : '',
      socketId: params.requestId,
    });
    const sock = cap.sockets?.get(params.requestId);
    if (sock) sock.frames++;
    // Ring buffer: keep the most recent 400 frames.
    if (cap.frames.length > 400) cap.frames.shift();
  }
});

// ── Message routing from content scripts / popup ────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.channel === 'content-to-bg') {
    router.handleContentMessage(msg.payload, sender.tab).then(sendResponse);
    return true;
  }
  if (msg.channel === 'popup-to-bg') {
    if (msg.action === 'reconnect') {
      ws.reconnect();
      sendResponse({ ok: true });
    } else {
      sendResponse(router.getStatus());
    }
  }
});

// Tab load events → forward to Python
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status === 'complete' && tab.url) {
    ws.sendEvent('page_load', { url: tab.url, title: tab.title, tabId });
  }
});

// ═══════════════════════════════════════════════════════════════════════════
// CDP ENGINE — Single session debugger management
// ═══════════════════════════════════════════════════════════════════════════

async function cdpAttach(tabId) {
  if (attachedTabs.has(tabId)) return;
  await chrome.debugger.attach({ tabId }, '1.3');
  attachedTabs.add(tabId);
}

async function cdpDetach(tabId) {
  if (!attachedTabs.has(tabId)) return;
  try {
    await chrome.debugger.detach({ tabId });
  } catch { /* already detached */ }
  attachedTabs.delete(tabId);
}

async function cdpSend(tabId, method, params = {}) {
  return chrome.debugger.sendCommand({ tabId }, method, params);
}

/**
 * CDP trusted mouse click — required for Radix UI tabs, dropdowns, etc.
 * Uses mousePressed + mouseReleased (trusted events).
 */
async function cdpClick(tabId, x, y) {
  await updateCursorOverlay(tabId, x, y, true);
  await cdpSend(tabId, 'Input.dispatchMouseEvent', {
    type: 'mousePressed', x, y, button: 'left', clickCount: 1,
  });
  await cdpSend(tabId, 'Input.dispatchMouseEvent', {
    type: 'mouseReleased', x, y, button: 'left', clickCount: 1,
  });
  cursorPos.set(tabId, { x, y });
  await updateCursorOverlay(tabId, x, y, false);
}

/**
 * CDP type via Input.insertText — the ONLY method that works with React
 * contentEditable. NOT new Function, NOT execCommand, NOT el.value =.
 * Types char by char for React state tracking.
 */
async function cdpType(tabId, text) {
  for (const char of text) {
    await cdpSend(tabId, 'Input.insertText', { text: char });
    await sleep(5);
  }
}

/**
 * CDP clear — click the input FIRST (critical: otherwise Ctrl+A selects
 * the entire page, not just the input), then Ctrl+A + Backspace.
 */
async function cdpClear(tabId) {
  // Ctrl+A
  await cdpSend(tabId, 'Input.dispatchKeyEvent', {
    type: 'keyDown', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 2,
  });
  await cdpSend(tabId, 'Input.dispatchKeyEvent', {
    type: 'keyUp', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 2,
  });
  await sleep(50);
  // Backspace
  await cdpSend(tabId, 'Input.dispatchKeyEvent', {
    type: 'keyDown', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8,
  });
  await cdpSend(tabId, 'Input.dispatchKeyEvent', {
    type: 'keyUp', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8,
  });
  await sleep(100);
}

/**
 * CDP Escape key — for closing dropdowns/modals.
 */
async function cdpEscape(tabId) {
  await cdpSend(tabId, 'Input.dispatchKeyEvent', {
    type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27,
  });
  await cdpSend(tabId, 'Input.dispatchKeyEvent', {
    type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27,
  });
}

/**
 * CDP Enter key — for form submission.
 */
async function cdpEnter(tabId) {
  await cdpSend(tabId, 'Input.dispatchKeyEvent', {
    type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13,
  });
  await cdpSend(tabId, 'Input.dispatchKeyEvent', {
    type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13,
  });
}

/**
 * CDP Tab key — for moving focus.
 */
async function cdpTab(tabId) {
  await cdpSend(tabId, 'Input.dispatchKeyEvent', {
    type: 'keyDown', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9,
  });
  await cdpSend(tabId, 'Input.dispatchKeyEvent', {
    type: 'keyUp', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9,
  });
}

/**
 * Move the mouse to (x, y). Optionally interpolate from the last known cursor
 * position over `steps` intermediate mouseMoved events so movement looks human
 * and triggers hover states along the path. Updates the visible cursor overlay.
 */
async function cdpMouseMove(tabId, x, y, steps = 12, buttonDown = false) {
  const prev = cursorPos.get(tabId) || { x, y };
  const n = Math.max(1, steps);
  for (let i = 1; i <= n; i++) {
    const ix = Math.round(prev.x + (x - prev.x) * (i / n));
    const iy = Math.round(prev.y + (y - prev.y) * (i / n));
    await cdpSend(tabId, 'Input.dispatchMouseEvent', {
      type: 'mouseMoved', x: ix, y: iy,
      button: buttonDown ? 'left' : 'none',
      buttons: buttonDown ? 1 : 0,
    });
    await updateCursorOverlay(tabId, ix, iy, buttonDown);
    await sleep(8);
  }
  cursorPos.set(tabId, { x, y });
}

/**
 * Full drag: press at (x1,y1), move to (x2,y2) with interpolation, release.
 * Works for HTML5 drag surfaces, canvas, sliders, sortable lists.
 */
async function cdpDrag(tabId, x1, y1, x2, y2, steps = 20) {
  await cdpMouseMove(tabId, x1, y1, 6, false);
  await cdpSend(tabId, 'Input.dispatchMouseEvent', {
    type: 'mousePressed', x: x1, y: y1, button: 'left', buttons: 1, clickCount: 1,
  });
  await updateCursorOverlay(tabId, x1, y1, true);
  await sleep(60);
  await cdpMouseMove(tabId, x2, y2, steps, true);
  await sleep(60);
  await cdpSend(tabId, 'Input.dispatchMouseEvent', {
    type: 'mouseReleased', x: x2, y: y2, button: 'left', buttons: 0, clickCount: 1,
  });
  await updateCursorOverlay(tabId, x2, y2, false);
}

/**
 * Inject / update a floating cursor dot so the human can watch what the brain
 * is doing. Cheap: reuses a single element keyed by a fixed id.
 */
async function updateCursorOverlay(tabId, x, y, pressed) {
  try {
    await exec(tabId, (cx, cy, isDown) => {
      let dot = document.getElementById('__awa_cursor__');
      if (!dot) {
        dot = document.createElement('div');
        dot.id = '__awa_cursor__';
        Object.assign(dot.style, {
          position: 'fixed', width: '18px', height: '18px', zIndex: '2147483647',
          borderRadius: '50%', pointerEvents: 'none', left: '0', top: '0',
          transform: 'translate(-50%, -50%)', transition: 'left 40ms linear, top 40ms linear',
          border: '2px solid #fff', boxShadow: '0 0 0 2px rgba(0,0,0,0.5), 0 0 10px rgba(0,120,255,0.9)',
        });
        document.documentElement.appendChild(dot);
      }
      dot.style.left = cx + 'px';
      dot.style.top = cy + 'px';
      dot.style.background = isDown ? 'rgba(255,60,60,0.85)' : 'rgba(0,120,255,0.65)';
    }, [x, y, !!pressed]);
  } catch { /* overlay is best-effort */ }
}

// ═══════════════════════════════════════════════════════════════════════════
// DOM ENGINE — All via chrome.scripting.executeScript MAIN world
// Bypasses Content Security Policy that blocks eval/new Function
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Execute a function in MAIN world on the page.
 * This is the safe way to interact with page DOM, bypassing CSP.
 */
async function exec(tabId, func, args = []) {
  const results = await chrome.scripting.executeScript({
    target: { tabId, allFrames: false },
    world: 'MAIN',
    func,
    args,
  });
  return results?.[0]?.result ?? null;
}

/**
 * Find element position by selector or XPath.
 * Returns { found: true, x, y, width, height } or { found: false }.
 *
 * Handles:
 * - XPath (starts with /)
 * - CSS selectors
 * - Text content matching (starts with text:)
 * - Role-based selectors (e.g. [role="tab"][id$="-trigger-PORTRAIT"])
 * - aria-haspopup filtering
 */
async function findPos(tabId, selectorOrXpath) {
  return exec(tabId, (sel) => {
    let el = null;

    if (sel.startsWith('/')) {
      // XPath
      const result = document.evaluate(sel, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
      el = result.singleNodeValue;
    } else if (sel.startsWith('text:')) {
      // Text content matching — works when IDs are dynamic
      const searchText = sel.slice(5);
      const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
      while (walk.nextNode()) {
        const node = walk.currentNode;
        if (node.textContent?.trim() === searchText && node.children.length === 0) {
          el = node;
          break;
        }
      }
      // Partial match fallback
      if (!el) {
        const walk2 = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
        while (walk2.nextNode()) {
          const node = walk2.currentNode;
          if (node.textContent?.trim().includes(searchText) && node.children.length === 0) {
            el = node;
            break;
          }
        }
      }
    } else {
      // CSS selector
      el = document.querySelector(sel);
    }

    if (!el) return { found: false };

    el.scrollIntoView({ behavior: 'instant', block: 'center' });
    const rect = el.getBoundingClientRect();
    return {
      found: true,
      x: Math.round(rect.x + rect.width / 2),
      y: Math.round(rect.y + rect.height / 2),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
      tag: el.tagName?.toLowerCase(),
      text: (el.textContent || '').trim().slice(0, 80),
    };
  }, [selectorOrXpath]);
}

/**
 * Discover all interactive elements on the page.
 */
async function discover(tabId) {
  return exec(tabId, () => {
    const selectors = 'a, button, input, select, textarea, [role="button"], [role="tab"], [role="menuitem"], [role="option"], [onclick], [tabindex], [contenteditable="true"], [aria-haspopup]';
    const elements = [];
    for (const el of document.querySelectorAll(selectors)) {
      if (elements.length >= 150) break;
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) continue;
      // Build a stable selector
      let selector = '';
      if (el.id) selector = `#${el.id}`;
      else if (el.getAttribute('role') && el.id) selector = `[role="${el.getAttribute('role')}"][id="${el.id}"]`;
      else if (el.name && el.tagName === 'INPUT') selector = `input[name="${el.name}"]`;
      else {
        // Build path
        const parts = [];
        let cur = el;
        while (cur && cur !== document.body) {
          let s = cur.tagName.toLowerCase();
          if (cur.id) { parts.unshift(`#${cur.id}`); break; }
          const parent = cur.parentElement;
          if (parent) {
            const sibs = Array.from(parent.children).filter(c => c.tagName === cur.tagName);
            if (sibs.length > 1) s += `:nth-of-type(${sibs.indexOf(cur) + 1})`;
          }
          parts.unshift(s);
          cur = parent;
        }
        selector = parts.join(' > ');
      }
      elements.push({
        tag: el.tagName.toLowerCase(),
        type: el.type || el.getAttribute('role') || '',
        text: (el.textContent || el.value || el.placeholder || '').trim().slice(0, 80),
        selector,
        href: el.href || '',
        name: el.name || '',
        id: el.id || '',
        ariaLabel: el.getAttribute('aria-label') || '',
        ariaHaspopup: el.getAttribute('aria-haspopup') || '',
        contentEditable: el.contentEditable === 'true',
        rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
      });
    }
    return elements;
  });
}

/**
 * Get visible text from the page.
 */
async function getPageText(tabId) {
  return exec(tabId, () => document.body?.innerText || '');
}

/**
 * Get page HTML.
 */
async function getPageHTML(tabId) {
  return exec(tabId, () => document.documentElement?.outerHTML?.slice(0, 500000) || '');
}

// ═══════════════════════════════════════════════════════════════════════════
// TAB MANAGEMENT
// ═══════════════════════════════════════════════════════════════════════════

async function getTargetTabId() {
  // 1. Validate cached targetTabId — must exist AND not be a chrome-extension:// URL
  if (targetTabId) {
    try {
      const tab = await chrome.tabs.get(targetTabId);
      if (tab && tab.url && !tab.url.startsWith('chrome-extension://') && !tab.url.startsWith('chrome://')) {
        lastWebTabId = targetTabId;
        return targetTabId;
      }
      // Cached ID points to an invalid tab — clear it
      targetTabId = null;
    } catch {
      targetTabId = null;
    }
  }

  // 2. Fallback: search all tabs for a regular web page (not chrome://, not chrome-extension://)
  const allTabs = await chrome.tabs.query({});
  const webTabs = allTabs.filter(t => t.url && (t.url.startsWith('http://') || t.url.startsWith('https://')));

  // Prefer the last automated tab if it's still a web tab, even if not active —
  // this stops the target from "drifting" to whatever the user clicks (e.g. the
  // extension popup or chrome://extensions) between two orchestrated commands.
  if (lastWebTabId != null) {
    const sticky = webTabs.find(t => t.id === lastWebTabId);
    if (sticky) {
      targetTabId = sticky.id;
      return sticky.id;
    }
  }

  // Then the active web tab in the current window.
  const [activeWebTab] = webTabs.filter(t => t.active && t.windowId);
  if (activeWebTab) {
    targetTabId = activeWebTab.id;
    lastWebTabId = activeWebTab.id;
    console.log(`[SW] Auto-selected active web tab: ${activeWebTab.url}`);
    return activeWebTab.id;
  }

  // Otherwise, any web tab.
  if (webTabs.length > 0) {
    targetTabId = webTabs[0].id;
    lastWebTabId = webTabs[0].id;
    console.log(`[SW] Auto-selected web tab: ${webTabs[0].url}`);
    return webTabs[0].id;
  }

  console.warn('[SW] No valid web tab found');
  return null;
}

function waitForTabLoad(tabId, timeoutMs = 25000) {
  return new Promise((resolve) => {
    let resolved = false;
    const done = () => {
      if (!resolved) {
        resolved = true;
        chrome.tabs.onUpdated.removeListener(onUpdated);
        resolve();
      }
    };
    const onUpdated = (tid, info) => {
      if (tid === tabId && info.status === 'complete') done();
    };
    chrome.tabs.onUpdated.addListener(onUpdated);
    setTimeout(done, timeoutMs);
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// COMMAND ROUTER — Handles all commands from Python
// ═══════════════════════════════════════════════════════════════════════════

ws.onCommand(async (command) => {
  const { id, action, target, params = {} } = command;

  // ── Log every incoming interaction for the popup activity log ──────────
  // Typing/clicking get their payload surfaced so you can see exactly what
  // the extension received (the "log the type interaction" request).
  _currentAction = action;
  logInteraction(action, target, params);

  try {
    switch (action) {

      // ── NAVIGATE ────────────────────────────────────────────────────────
      case 'navigate': {
        const url = params.url;
        console.log(`[SW] Navigate to: ${url}`);
        // Reuse existing tab if we have one, otherwise create new
        if (targetTabId) {
          try {
            await chrome.tabs.get(targetTabId); // check if tab still exists
            await chrome.tabs.update(targetTabId, { url, active: true });
          } catch {
            // Tab was closed, create new one
            const newTab = await chrome.tabs.create({ url, active: true });
            targetTabId = newTab.id;
          }
        } else {
          const newTab = await chrome.tabs.create({ url, active: true });
          targetTabId = newTab.id;
        }
        lastWebTabId = targetTabId; // lock this as the sticky automation tab
        await waitForTabLoad(targetTabId, 25000);
        // Give SPA frameworks time to hydrate
        await sleep(2000);

        // Banner/modal dismissal — try to close common overlays
        if (params.dismiss_banners !== false) {
          await dismissBanners(targetTabId);
        }

        ws.sendResponse(id, 'success', { tabId: targetTabId });
        return;
      }

      // ── SCREENSHOT ──────────────────────────────────────────────────────
      case 'screenshot': {
        const tabId = await getTargetTabId();
        if (tabId) {
          await chrome.tabs.update(tabId, { active: true });
          await sleep(300);
        }
        const dataUrl = await chrome.tabs.captureVisibleTab(null, { format: 'png' });
        const b64 = dataUrl.replace(/^data:image\/png;base64,/, '');
        ws.sendResponse(id, 'success', { screenshot: b64 });
        return;
      }

      // ── CLICK — findPos + cdpClick (NEVER coordinates from Python) ─────
      case 'click': {
        const tabId = await getTargetTabId();
        if (!tabId) { ws.sendResponse(id, 'failure', {}, 'No target tab'); return; }

        const selector = params.selector || params.xpath || target?.value;
        if (!selector) { ws.sendResponse(id, 'failure', {}, 'No selector provided'); return; }

        const pos = await findPos(tabId, selector);
        if (!pos?.found) {
          ws.sendResponse(id, 'failure', {}, `Element not found: ${selector}`);
          return;
        }

        await cdpAttach(tabId);
        await cdpClick(tabId, pos.x, pos.y);
        await sleep(params.delay_after || 200);

        ws.sendResponse(id, 'success', { clicked: true, ...pos });
        return;
      }

      // ── TYPE — findPos + cdpClick to focus + cdpClear + cdpType ────────
      case 'type': {
        const tabId = await getTargetTabId();
        if (!tabId) { ws.sendResponse(id, 'failure', {}, 'No target tab'); return; }

        const selector = params.selector || params.xpath || target?.value;
        const text = params.text || '';

        if (selector) {
          const pos = await findPos(tabId, selector);
          if (!pos?.found) {
            ws.sendResponse(id, 'failure', {}, `Element not found: ${selector}`);
            return;
          }

          await cdpAttach(tabId);
          // Click to focus FIRST — critical for Ctrl+A to work on input, not page
          await cdpClick(tabId, pos.x, pos.y);
          await sleep(150);
        } else {
          await cdpAttach(tabId);
        }

        if (params.clear !== false) {
          await cdpClear(tabId);
        }

        await cdpType(tabId, text);
        await sleep(params.delay_after || 100);

        ws.sendResponse(id, 'success', { typed: text.length });
        return;
      }

      // ── TYPE_CDP — direct CDP typing with xpath focus ──────────────────
      case 'type_cdp': {
        const tabId = await getTargetTabId();
        if (!tabId) { ws.sendResponse(id, 'failure', {}, 'No target tab'); return; }

        const text = params.text || '';
        const xpath = params.xpath || params.selector || '';
        const clear = params.clear !== false;

        await cdpAttach(tabId);

        if (xpath) {
          // Focus via Runtime.evaluate (works inside CDP session)
          const { result } = await cdpSend(tabId, 'Runtime.evaluate', {
            expression: `(() => {
              const el = document.evaluate('${xpath.replace(/'/g, "\\'")}', document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
              if (el) { el.scrollIntoView({block:'center'}); return JSON.stringify(el.getBoundingClientRect()); }
              return null;
            })()`,
          });

          if (result?.value) {
            const rect = JSON.parse(result.value);
            // CDP click to focus — NOT el.focus(), need trusted event
            await cdpClick(tabId, Math.round(rect.x + rect.width / 2), Math.round(rect.y + rect.height / 2));
            await sleep(200);
          } else {
            ws.sendResponse(id, 'failure', {}, `XPath not found: ${xpath}`);
            return;
          }

          if (clear) await cdpClear(tabId);
        }

        await cdpType(tabId, text);
        ws.sendResponse(id, 'success', { typed: text.length });
        return;
      }

      // ── CLICK_CDP — direct CDP mouse events ───────────────────────────
      case 'click_cdp': {
        const tabId = await getTargetTabId();
        if (!tabId) { ws.sendResponse(id, 'failure', {}, 'No target tab'); return; }

        let x = params.x || 0;
        let y = params.y || 0;

        // If selector/xpath provided, find position first (preferred over raw coords)
        const selector = params.selector || params.xpath;
        if (selector) {
          const pos = await findPos(tabId, selector);
          if (!pos?.found) {
            ws.sendResponse(id, 'failure', {}, `Element not found: ${selector}`);
            return;
          }
          x = pos.x;
          y = pos.y;
        }

        await cdpAttach(tabId);
        await cdpClick(tabId, x, y);
        await sleep(params.delay_after || 200);

        ws.sendResponse(id, 'success', { clicked: true, x, y });
        return;
      }

      // ── EXECUTE_SCRIPT — runs JS via CDP Runtime.evaluate (bypasses CSP) ──
      case 'execute_script': {
        const tabId = await getTargetTabId();
        if (!tabId) { ws.sendResponse(id, 'failure', {}, 'No target tab'); return; }

        // Use CDP Runtime.evaluate which bypasses CSP entirely
        // (unlike new Function() in exec() which CSP blocks on strict sites)
        await cdpAttach(tabId);
        try {
          const { result: evalResult, exceptionDetails } = await cdpSend(tabId, 'Runtime.evaluate', {
            expression: params.script,
            returnByValue: true,
            awaitPromise: true,
          });
          if (exceptionDetails) {
            ws.sendResponse(id, 'success', { extracted_data: { result: { _error: exceptionDetails.text || exceptionDetails.exception?.description } } });
          } else {
            ws.sendResponse(id, 'success', { extracted_data: { result: evalResult?.value } });
          }
        } catch (e) {
          ws.sendResponse(id, 'success', { extracted_data: { result: { _error: e.message } } });
        }
        return;
      }

      // ── EXTRACT — text/html/cookies/localStorage via content script ───
      case 'extract': {
        const tabId = await getTargetTabId();
        if (!tabId) { ws.sendResponse(id, 'failure', {}, 'No target tab'); return; }

        const extractType = params.type || 'text';
        const data = {};

        if (extractType === 'text') {
          data.text = await getPageText(tabId);
        } else if (extractType === 'html') {
          data.html = await getPageHTML(tabId);
        } else if (extractType === 'cookies') {
          const cookies = await chrome.cookies.getAll({ url: (await chrome.tabs.get(tabId)).url });
          data.cookies = cookies;
        } else if (extractType === 'localStorage' || extractType === 'sessionStorage') {
          data[extractType] = await exec(tabId, (storageType) => {
            const storage = storageType === 'localStorage' ? localStorage : sessionStorage;
            const d = {};
            for (let i = 0; i < storage.length; i++) {
              const k = storage.key(i);
              d[k] = storage.getItem(k);
            }
            return d;
          }, [extractType]);
        }

        ws.sendResponse(id, 'success', { extracted_data: data });
        return;
      }

      // ── DISCOVER — find all interactive elements ──────────────────────
      case 'discover': {
        const tabId = await getTargetTabId();
        if (!tabId) { ws.sendResponse(id, 'failure', {}, 'No target tab'); return; }
        const elements = await discover(tabId);
        ws.sendResponse(id, 'success', { extracted_data: { elements } });
        return;
      }

      // ── WAIT — poll for condition ─────────────────────────────────────
      case 'wait': {
        const tabId = await getTargetTabId();
        if (!tabId) { ws.sendResponse(id, 'failure', {}, 'No target tab'); return; }

        const condition = params.condition || 'time';
        const value = params.value || params.selector;
        const deadline = Date.now() + (params.timeout || 10000);

        if (condition === 'time') {
          await sleep(parseInt(value) || 1000);
        } else if (condition === 'element_visible') {
          while (Date.now() < deadline) {
            const pos = await findPos(tabId, value);
            if (pos?.found) break;
            await sleep(300);
          }
        } else if (condition === 'element_hidden') {
          while (Date.now() < deadline) {
            const pos = await findPos(tabId, value);
            if (!pos?.found) break;
            await sleep(300);
          }
        } else if (condition === 'network_idle') {
          await sleep(2000);
        } else {
          await sleep(1000);
        }

        ws.sendResponse(id, 'success', {});
        return;
      }

      // ── SCROLL ────────────────────────────────────────────────────────
      case 'scroll': {
        const tabId = await getTargetTabId();
        if (!tabId) { ws.sendResponse(id, 'failure', {}, 'No target tab'); return; }

        await exec(tabId, (dir, amt) => {
          const y = dir === 'up' ? -amt : amt;
          window.scrollBy({ top: y, behavior: 'smooth' });
        }, [params.direction || 'down', params.amount || 500]);

        await sleep(400);
        ws.sendResponse(id, 'success', {});
        return;
      }

      // ── KEY — send a single key via CDP ───────────────────────────────
      case 'key': {
        const tabId = await getTargetTabId();
        if (!tabId) { ws.sendResponse(id, 'failure', {}, 'No target tab'); return; }

        await cdpAttach(tabId);
        const key = params.key || 'Enter';
        if (key === 'Escape') await cdpEscape(tabId);
        else if (key === 'Enter') await cdpEnter(tabId);
        else if (key === 'Tab') await cdpTab(tabId);
        else {
          await cdpSend(tabId, 'Input.dispatchKeyEvent', {
            type: 'keyDown', key, code: `Key${key.toUpperCase()}`,
          });
          await cdpSend(tabId, 'Input.dispatchKeyEvent', {
            type: 'keyUp', key, code: `Key${key.toUpperCase()}`,
          });
        }

        ws.sendResponse(id, 'success', { key });
        return;
      }

      // ── DETACH — explicitly detach debugger when sequence is done ─────
      case 'detach': {
        const tabId = await getTargetTabId();
        if (tabId) await cdpDetach(tabId);
        ws.sendResponse(id, 'success', {});
        return;
      }

      // ── HOVER — CDP mouseMoved to trigger CSS :hover state ────────────
      case 'hover': {
        const tabId = await getTargetTabId();
        if (!tabId) { ws.sendResponse(id, 'failure', {}, 'No target tab'); return; }

        let x = params.x || 0, y = params.y || 0;
        const selector = params.selector || params.xpath || target?.value;
        if (selector) {
          const pos = await findPos(tabId, selector);
          if (!pos?.found) {
            ws.sendResponse(id, 'failure', { extracted_data: {} }, `Element not found: ${selector}`);
            return;
          }
          x = pos.x;
          y = pos.y;
        }

        await cdpAttach(tabId);
        await cdpSend(tabId, 'Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
        await sleep(params.delay_after || 300);

        ws.sendResponse(id, 'success', { extracted_data: { hovered: true, x, y } });
        return;
      }

      // ── OBSERVE_START — begin DOM observation on a selector or full page ─
      case 'observe_start': {
        const tabId = await getTargetTabId();
        if (!tabId) { ws.sendResponse(id, 'failure', {}, 'No target tab'); return; }

        const selector = params.selector || 'body';
        const intervalMs = params.interval || 1000;

        // Capture initial HTML snapshot
        const [initResult] = await chrome.scripting.executeScript({
          target: { tabId, allFrames: false },
          world: 'MAIN',
          func: (sel) => {
            const el = sel === 'body' ? document.body : (document.querySelector(sel) || document.body);
            return el.outerHTML.slice(0, 500000);
          },
          args: [selector],
        });

        // Clear previous observer for this tab
        const prev = domSnapshots.get(tabId);
        if (prev?.intervalId) clearInterval(prev.intervalId);

        domSnapshots.set(tabId, {
          selector,
          html: initResult.result,
          recording: true,
          intervalId: null,
          changes: [],
          stats: { total: 0, add: 0, rem: 0, attr: 0, text: 0 },
        });

        ws.sendResponse(id, 'success', { extracted_data: { observing: selector } });
        return;
      }

      // ── OBSERVE_DIFF — snapshot current DOM and diff against last snapshot ─
      case 'observe_diff': {
        const tabId = await getTargetTabId();
        if (!tabId) { ws.sendResponse(id, 'failure', {}, 'No target tab'); return; }

        const obs = domSnapshots.get(tabId);
        if (!obs) { ws.sendResponse(id, 'failure', {}, 'No observation started — call observe_start first'); return; }

        // Capture current HTML
        const [currResult] = await chrome.scripting.executeScript({
          target: { tabId, allFrames: false },
          world: 'MAIN',
          func: (sel) => {
            const el = sel === 'body' ? document.body : (document.querySelector(sel) || document.body);
            return el.outerHTML.slice(0, 500000);
          },
          args: [obs.selector],
        });

        const currHTML = currResult.result;

        // Diff using the DOM observer engine (runs in SW context with temp DOM elements)
        // Since service worker has no DOM, we run the diff in the page context
        const [diffResult] = await chrome.scripting.executeScript({
          target: { tabId, allFrames: false },
          world: 'MAIN',
          func: (prevHTML, currentHTML) => {
            // Inline the diff logic for page-context execution
            function _getSelector(el) {
              if (!el || el.nodeType !== 1) return '';
              const parts = [];
              let cur = el;
              while (cur && cur !== document.body && cur !== document.documentElement) {
                const tag = cur.tagName.toLowerCase();
                if (cur.id) { parts.unshift(tag + '#' + cur.id); break; }
                const cls = Array.from(cur.classList || []).filter(c => !/^(awa-|sar-)/.test(c)).slice(0, 2).join('.');
                let sel = cls ? tag + '.' + cls : tag;
                const parent = cur.parentElement;
                if (parent) {
                  const siblings = parent.querySelectorAll(':scope > ' + tag);
                  if (siblings.length > 1) sel += ':nth-child(' + (Array.from(siblings).indexOf(cur) + 1) + ')';
                }
                parts.unshift(sel);
                cur = cur.parentElement;
              }
              return parts.join(' > ');
            }
            function _getXPath(el, root) {
              if (!el || el.nodeType !== 1) return '';
              const parts = [];
              let cur = el;
              while (cur && cur !== root && cur !== document.documentElement) {
                const tag = cur.tagName.toLowerCase();
                const parent = cur.parentElement;
                if (parent) {
                  const siblings = Array.from(parent.children).filter(c => c.tagName === cur.tagName);
                  parts.unshift(tag + '[' + (siblings.indexOf(cur) + 1) + ']');
                } else { parts.unshift(tag); }
                cur = cur.parentElement;
              }
              return '/' + parts.join('/');
            }
            function _trunc(s, max = 200) {
              if (!s) return '';
              s = s.replace(/\s+/g, ' ').trim();
              return s.length > max ? s.substring(0, max) + '...' : s;
            }
            function _inferReason(c) {
              if (c.type === 'add') {
                const t = c.tag;
                if (t === 'script') return 'Script injected';
                if (t === 'style' || t === 'link') return 'Stylesheet added';
                if (/^(img|svg|canvas|video|picture)$/.test(t)) return 'Media element inserted';
                if (t === 'iframe') return 'Iframe embedded';
                if (/^(input|textarea|select)$/.test(t)) return 'Form element added';
                if (/^(li|tr|td|option)$/.test(t)) return 'List/table item added';
                if (/^(a|button)$/.test(t)) return 'Interactive element added';
                return 'Element inserted';
              }
              if (c.type === 'rem') {
                const t = c.tag;
                if (/^(li|tr|td|option)$/.test(t)) return 'List/table item removed';
                return 'Element removed';
              }
              if (c.type === 'attr') {
                const attr = c.attribute;
                if (attr === 'class') {
                  const oC = (c.oldValue || '').split(/\s+/), nC = (c.newValue || '').split(/\s+/);
                  const added = nC.filter(x => x && !oC.includes(x)), removed = oC.filter(x => x && !nC.includes(x));
                  if (added.some(x => /hidden|hide|collapse|invisible|d-none/.test(x))) return 'Element hidden';
                  if (removed.some(x => /hidden|hide|collapse|invisible|d-none/.test(x))) return 'Element shown';
                  if (added.some(x => /active|selected|open|expanded/.test(x))) return 'Element activated';
                  if (removed.some(x => /active|selected|open|expanded/.test(x))) return 'Element deactivated';
                  if (added.some(x => /loading|spinner|pending/.test(x))) return 'Loading state applied';
                  if (removed.some(x => /loading|spinner|pending/.test(x))) return 'Loading completed';
                  if (added.some(x => /error|invalid|danger/.test(x))) return 'Error state applied';
                  if (added.some(x => /success|valid|complete/.test(x))) return 'Success state applied';
                  return 'CSS classes changed';
                }
                if (attr === 'disabled') return c.newValue === '(removed)' ? 'Element enabled' : 'Element disabled';
                if (attr === 'value') return 'Input value changed';
                if (/^aria-/.test(attr)) return 'ARIA state changed';
                if (/^data-/.test(attr)) return 'Data attribute changed';
                return 'Attribute "' + attr + '" modified';
              }
              if (c.type === 'text') {
                const oL = (c.oldValue || '').length, nL = (c.newValue || '').length;
                if (oL === 0 && nL > 0) return 'Text populated';
                if (oL > 0 && nL === 0) return 'Text cleared';
                return 'Text updated';
              }
              return 'DOM mutation';
            }

            const prev = document.createElement('div');
            prev.innerHTML = prevHTML;
            const curr = document.createElement('div');
            curr.innerHTML = currentHTML;

            const prevMap = {}, currMap = {};
            prev.querySelectorAll('*').forEach(el => { const s = _getSelector(el); if (s) prevMap[s] = el; });
            curr.querySelectorAll('*').forEach(el => { const s = _getSelector(el); if (s) currMap[s] = el; });

            const changes = [];

            // Added
            for (const sel of Object.keys(currMap)) {
              if (!prevMap[sel]) {
                const el = currMap[sel];
                const e = { type: 'add', tag: el.tagName.toLowerCase(), id: el.id || '', classes: Array.from(el.classList || []).join(' '), selector: sel, xpath: _getXPath(el, curr), html: _trunc(el.outerHTML, 150) };
                e.reason = _inferReason(e);
                changes.push(e);
              }
            }
            // Removed
            for (const sel of Object.keys(prevMap)) {
              if (!currMap[sel]) {
                const el = prevMap[sel];
                const e = { type: 'rem', tag: el.tagName.toLowerCase(), id: el.id || '', classes: Array.from(el.classList || []).join(' '), selector: sel, xpath: _getXPath(el, prev), html: _trunc(el.outerHTML, 150) };
                e.reason = _inferReason(e);
                changes.push(e);
              }
            }
            // Changed
            for (const sel of Object.keys(currMap)) {
              if (!prevMap[sel]) continue;
              const pEl = prevMap[sel], cEl = currMap[sel];
              const allA = new Set([...Array.from(pEl.attributes).map(a => a.name), ...Array.from(cEl.attributes).map(a => a.name)]);
              for (const attr of allA) {
                let oV = pEl.getAttribute(attr) || '', nV = cEl.getAttribute(attr) || '';
                if (attr === 'class') { oV = oV.split(/\s+/).filter(c => !/^(awa-|sar-)/.test(c)).join(' ').trim(); nV = nV.split(/\s+/).filter(c => !/^(awa-|sar-)/.test(c)).join(' ').trim(); }
                if (oV !== nV) {
                  const e = { type: 'attr', tag: cEl.tagName.toLowerCase(), selector: sel, xpath: _getXPath(cEl, curr), attribute: attr, oldValue: _trunc(oV || '(none)', 80), newValue: _trunc(nV || '(removed)', 80) };
                  e.reason = _inferReason(e);
                  changes.push(e);
                }
              }
              const pT = Array.from(pEl.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent).join('');
              const cT = Array.from(cEl.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent).join('');
              if (pT.trim() !== cT.trim() && (pT.trim() || cT.trim())) {
                const e = { type: 'text', tag: cEl.tagName.toLowerCase(), selector: sel, xpath: _getXPath(cEl, curr), oldValue: _trunc(pT.trim(), 80), newValue: _trunc(cT.trim(), 80) };
                e.reason = _inferReason(e);
                changes.push(e);
              }
            }

            const stats = { total: changes.length, add: 0, rem: 0, attr: 0, text: 0 };
            for (const c of changes) { if (stats[c.type] !== undefined) stats[c.type]++; }

            // Build AI summary
            const lines = ['DOM changes: ' + stats.total + ' total (' + stats.add + ' added, ' + stats.rem + ' removed, ' + stats.attr + ' attr, ' + stats.text + ' text)'];
            const rc = {};
            for (const c of changes) { rc[c.reason] = (rc[c.reason] || 0) + 1; }
            const sorted = Object.entries(rc).sort((a, b) => b[1] - a[1]);
            if (sorted.length) {
              lines.push('Summary:');
              for (const [r, n] of sorted.slice(0, 10)) lines.push('  - ' + r + ' (x' + n + ')');
            }

            return { changes: changes.slice(0, 50), stats, summary: lines.join('\n') };
          },
          args: [obs.html, currHTML],
        });

        const { changes, stats, summary } = diffResult.result;

        // Update stored snapshot
        obs.html = currHTML;
        obs.changes = changes;
        obs.stats = stats;

        ws.sendResponse(id, 'success', {
          extracted_data: { changes, stats, summary, selector: obs.selector },
        });
        return;
      }

      // ── OBSERVE_STOP — stop DOM observation ──────────────────────────────
      case 'observe_stop': {
        const tabId = await getTargetTabId();
        if (!tabId) { ws.sendResponse(id, 'failure', {}, 'No target tab'); return; }

        const obs = domSnapshots.get(tabId);
        if (obs?.intervalId) clearInterval(obs.intervalId);
        domSnapshots.delete(tabId);

        ws.sendResponse(id, 'success', {});
        return;
      }

      // ── MOUSE_MOVE — move cursor to selector or coords (visible) ───────
      case 'mouse_move': {
        const tabId = await getTargetTabId();
        if (!tabId) { ws.sendResponse(id, 'failure', {}, 'No target tab'); return; }

        let x = params.x, y = params.y;
        const selector = params.selector || params.xpath || target?.value;
        if (selector) {
          const pos = await findPos(tabId, selector);
          if (!pos?.found) { ws.sendResponse(id, 'failure', {}, `Element not found: ${selector}`); return; }
          x = pos.x; y = pos.y;
        }
        if (x == null || y == null) { ws.sendResponse(id, 'failure', {}, 'No coords or selector'); return; }

        await cdpAttach(tabId);
        await cdpMouseMove(tabId, x, y, params.steps || 12);
        await sleep(params.delay_after || 100);
        ws.sendResponse(id, 'success', { extracted_data: { x, y } });
        return;
      }

      // ── DRAG — press → move → release (selector→selector or coords) ────
      case 'drag': {
        const tabId = await getTargetTabId();
        if (!tabId) { ws.sendResponse(id, 'failure', {}, 'No target tab'); return; }

        let x1 = params.x1, y1 = params.y1, x2 = params.x2, y2 = params.y2;
        if (params.from) {
          const p = await findPos(tabId, params.from);
          if (!p?.found) { ws.sendResponse(id, 'failure', {}, `From not found: ${params.from}`); return; }
          x1 = p.x; y1 = p.y;
        }
        if (params.to) {
          const p = await findPos(tabId, params.to);
          if (!p?.found) { ws.sendResponse(id, 'failure', {}, `To not found: ${params.to}`); return; }
          x2 = p.x; y2 = p.y;
        }
        if ([x1, y1, x2, y2].some(v => v == null)) {
          ws.sendResponse(id, 'failure', {}, 'Need from/to selectors or x1,y1,x2,y2');
          return;
        }

        await cdpAttach(tabId);
        await cdpDrag(tabId, x1, y1, x2, y2, params.steps || 20);
        await sleep(params.delay_after || 200);
        ws.sendResponse(id, 'success', { extracted_data: { from: [x1, y1], to: [x2, y2] } });
        return;
      }

      // ── NETWORK_START — begin capturing requests via CDP Network domain ─
      case 'network_start': {
        const tabId = await getTargetTabId();
        if (!tabId) { ws.sendResponse(id, 'failure', {}, 'No target tab'); return; }

        await cdpAttach(tabId);
        await cdpSend(tabId, 'Network.enable', {
          maxTotalBufferSize: 10 * 1024 * 1024,
          maxResourceBufferSize: 5 * 1024 * 1024,
        });
        networkCapture.set(tabId, {
          enabled: true, requests: new Map(), order: [],
          sockets: new Map(), frames: [],
        });
        ws.sendResponse(id, 'success', { extracted_data: { capturing: true } });
        return;
      }

      // ── WS_FRAMES — dump captured WebSocket frames (live feed ticks) ────
      case 'ws_frames': {
        const tabId = await getTargetTabId();
        if (!tabId) { ws.sendResponse(id, 'failure', {}, 'No target tab'); return; }

        const cap = networkCapture.get(tabId);
        if (!cap) { ws.sendResponse(id, 'failure', {}, 'Not capturing — call network_start first'); return; }

        const sockets = Array.from((cap.sockets || new Map()).entries()).map(([sid, s]) => ({
          socketId: sid, url: s.url, frames: s.frames,
        }));
        let frames = cap.frames || [];
        if (params.contains) frames = frames.filter(f => f.data.includes(params.contains));
        if (params.dir) frames = frames.filter(f => f.dir === params.dir);
        const limit = params.limit || 60;
        ws.sendResponse(id, 'success', {
          extracted_data: {
            sockets,
            frame_count: (cap.frames || []).length,
            frames: frames.slice(-limit),
          },
        });
        return;
      }

      // ── NETWORK_DUMP — return captured requests (optionally filtered) ──
      case 'network_dump': {
        const tabId = await getTargetTabId();
        if (!tabId) { ws.sendResponse(id, 'failure', {}, 'No target tab'); return; }

        const cap = networkCapture.get(tabId);
        if (!cap) { ws.sendResponse(id, 'failure', {}, 'Not capturing — call network_start first'); return; }

        const typeFilter = params.resource_type;      // e.g. 'XHR', 'Media', 'Image', 'Fetch'
        const urlContains = params.url_contains;        // substring filter
        let list = cap.order.map(rid => cap.requests.get(rid)).filter(Boolean);
        if (typeFilter) list = list.filter(r => (r.resourceType || '').toLowerCase() === typeFilter.toLowerCase());
        if (urlContains) list = list.filter(r => r.url.includes(urlContains));

        // Trim headers for payload size unless explicitly requested
        const slim = list.map(r => ({
          url: r.url, method: r.method, resourceType: r.resourceType,
          status: r.status, mimeType: r.mimeType, size: r.encodedDataLength,
          fromCache: r.fromCache, failed: r.failed || false, errorText: r.errorText || '',
          ...(params.include_headers ? { requestHeaders: r.requestHeaders } : {}),
        }));

        ws.sendResponse(id, 'success', { extracted_data: { count: slim.length, requests: slim.slice(0, params.limit || 300) } });
        return;
      }

      // ── NETWORK_STOP — stop capturing, disable Network domain ──────────
      case 'network_stop': {
        const tabId = await getTargetTabId();
        if (!tabId) { ws.sendResponse(id, 'failure', {}, 'No target tab'); return; }

        const cap = networkCapture.get(tabId);
        if (cap) cap.enabled = false;
        try { await cdpSend(tabId, 'Network.disable'); } catch { /* ok */ }
        networkCapture.delete(tabId);
        ws.sendResponse(id, 'success', {});
        return;
      }

      // ── DOWNLOAD_ASSET — grab a URL to disk or return base64 ──────────
      case 'download_asset': {
        const tabId = await getTargetTabId();
        const url = params.url;
        if (!url) { ws.sendResponse(id, 'failure', {}, 'No url provided'); return; }

        if (params.return_data) {
          // Fetch in page context (keeps cookies/session) → base64 back to brain
          const b64 = await exec(tabId, async (u) => {
            try {
              const resp = await fetch(u, { credentials: 'include' });
              const buf = await resp.arrayBuffer();
              const bytes = new Uint8Array(buf);
              let bin = '';
              for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
              return { ok: true, mime: resp.headers.get('content-type') || '', b64: btoa(bin), size: bytes.length };
            } catch (e) { return { ok: false, error: e.message }; }
          }, [url]);
          if (b64?.ok) ws.sendResponse(id, 'success', { extracted_data: b64 });
          else ws.sendResponse(id, 'failure', {}, b64?.error || 'fetch failed');
          return;
        }

        // Otherwise use chrome.downloads (saves to Downloads folder)
        try {
          const downloadId = await chrome.downloads.download({
            url,
            filename: params.filename || undefined,
            conflictAction: 'uniquify',
          });
          ws.sendResponse(id, 'success', { extracted_data: { downloadId, url } });
        } catch (e) {
          ws.sendResponse(id, 'failure', {}, e.message);
        }
        return;
      }

      // ── MEDIA — control <video>/<audio>: play/pause/seek/mute/state ───
      case 'media': {
        const tabId = await getTargetTabId();
        if (!tabId) { ws.sendResponse(id, 'failure', {}, 'No target tab'); return; }

        const op = params.op || 'state';           // play|pause|toggle|seek|mute|unmute|state
        const selector = params.selector || 'video, audio';
        const seekTo = params.time;

        const result = await exec(tabId, async (sel, operation, time) => {
          const el = document.querySelector(sel);
          if (!el) return { found: false };
          try {
            if (operation === 'play') await el.play();
            else if (operation === 'pause') el.pause();
            else if (operation === 'toggle') { el.paused ? await el.play() : el.pause(); }
            else if (operation === 'seek' && time != null) el.currentTime = time;
            else if (operation === 'mute') el.muted = true;
            else if (operation === 'unmute') el.muted = false;
          } catch (e) { /* autoplay policies may reject */ }
          return {
            found: true, op: operation, paused: el.paused, muted: el.muted,
            currentTime: el.currentTime, duration: el.duration,
            src: el.currentSrc || el.src || '', volume: el.volume,
          };
        }, [selector, op, seekTo]);

        if (!result?.found) { ws.sendResponse(id, 'failure', {}, `No media element: ${selector}`); return; }
        ws.sendResponse(id, 'success', { extracted_data: result });
        return;
      }

      // ── SCRAPE — structured extraction: elements matching a selector ──
      case 'scrape': {
        const tabId = await getTargetTabId();
        if (!tabId) { ws.sendResponse(id, 'failure', {}, 'No target tab'); return; }

        const selector = params.selector || 'body';
        const fields = params.fields || null; // { name: {selector, attr} }
        const data = await exec(tabId, (sel, fieldMap, limit) => {
          const nodes = Array.from(document.querySelectorAll(sel)).slice(0, limit);
          return nodes.map(node => {
            if (!fieldMap) {
              return {
                text: (node.innerText || node.textContent || '').trim().slice(0, 500),
                html: node.outerHTML.slice(0, 1000),
                href: node.href || node.querySelector('a')?.href || '',
                src: node.src || node.querySelector('img')?.src || '',
              };
            }
            const row = {};
            for (const [key, spec] of Object.entries(fieldMap)) {
              const sub = spec.selector ? node.querySelector(spec.selector) : node;
              if (!sub) { row[key] = null; continue; }
              row[key] = spec.attr
                ? (spec.attr === 'text' ? (sub.innerText || sub.textContent || '').trim() : sub.getAttribute(spec.attr))
                : (sub.innerText || sub.textContent || '').trim();
            }
            return row;
          });
        }, [selector, fields, params.limit || 200]);

        ws.sendResponse(id, 'success', { extracted_data: { count: data.length, items: data } });
        return;
      }

      // ── FIND — just find element position without clicking ────────────
      case 'find': {
        const tabId = await getTargetTabId();
        if (!tabId) { ws.sendResponse(id, 'failure', {}, 'No target tab'); return; }
        const selector = params.selector || params.xpath || target?.value;
        const pos = await findPos(tabId, selector);
        ws.sendResponse(id, pos?.found ? 'success' : 'failure', { extracted_data: pos || {} }, pos?.found ? null : `Not found: ${selector}`);
        return;
      }

      // ── FALLBACK — forward to content script ──────────────────────────
      default: {
        const tabId = await getTargetTabId();
        if (!tabId) {
          ws.sendResponse(id, 'failure', {}, 'No target tab — navigate first');
          return;
        }

        // Ensure content script is injected (main frame only)
        try {
          await chrome.scripting.executeScript({
            target: { tabId, allFrames: false },
            files: ['js/content/content-main.js'],
          });
        } catch { /* already injected */ }

        await sleep(100);
        const result = await chrome.tabs.sendMessage(tabId, {
          channel: 'bg-to-content',
          payload: { id, action, target, params },
        });
        ws.sendResponse(id, result.status || 'success', result.data || {}, result.error || null);
      }
    }
  } catch (err) {
    console.error(`[SW] Command ${action} error:`, err);
    ws.sendResponse(id, 'failure', {}, err.message);
  }
});

// ═══════════════════════════════════════════════════════════════════════════
// HELPERS
// ═══════════════════════════════════════════════════════════════════════════

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

/**
 * Record an incoming interaction in the task log (shown in the popup) and
 * echo it to the SW console. Typing/clicking include a readable detail of
 * the actual payload received so it's easy to verify what came over the wire.
 */
function logInteraction(action, target, params = {}) {
  let detail = '';
  const sel = params.selector || params.xpath || target?.value || '';

  switch (action) {
    case 'type':
    case 'type_cdp': {
      const text = params.text ?? '';
      const preview = text.length > 60 ? text.slice(0, 60) + '…' : text;
      detail = `"${preview}" (${text.length} chars)` + (sel ? ` → ${sel}` : '');
      break;
    }
    case 'click':
    case 'click_cdp':
    case 'hover':
    case 'find':
      detail = sel || (params.x != null ? `@(${params.x},${params.y})` : '');
      break;
    case 'navigate':
      detail = params.url || '';
      break;
    case 'key':
      detail = params.key || 'Enter';
      break;
    case 'execute_script':
      detail = (params.script || '').slice(0, 60);
      break;
    case 'wait':
      detail = `${params.condition || 'time'} ${params.value || ''}`.trim();
      break;
    case 'drag':
      detail = (params.from && params.to) ? `${params.from} → ${params.to}`
        : `(${params.x1},${params.y1}) → (${params.x2},${params.y2})`;
      break;
    case 'mouse_move':
      detail = sel || `@(${params.x},${params.y})`;
      break;
    case 'download_asset':
      detail = params.url || '';
      break;
    case 'media':
      detail = `${params.op || 'state'}${params.time != null ? ' @' + params.time + 's' : ''}`;
      break;
    case 'network_dump':
      detail = [params.resource_type, params.url_contains].filter(Boolean).join(' ');
      break;
    case 'scrape':
      detail = params.selector || 'body';
      break;
    default:
      detail = sel;
  }

  console.log(`[SW] ◀ ${action}${detail ? ': ' + detail : ''}`);
  router.logTask({ action, detail, status: 'received' });
}

/**
 * Record the outcome of an interaction (success/failure) in the task log.
 */
function logResult(action, status, detail = '') {
  console.log(`[SW] ▶ ${action}: ${status}${detail ? ' — ' + detail : ''}`);
  router.logTask({ action, detail, status });
}

/**
 * Dismiss common banners/modals/cookie consent overlays.
 * Run after navigation before starting automation.
 */
async function dismissBanners(tabId) {
  try {
    await exec(tabId, () => {
      // Common dismiss patterns
      const dismissSelectors = [
        '[aria-label="Close"]',
        '[aria-label="Dismiss"]',
        'button.close',
        '.banner-close',
        '.cookie-close',
        '[data-testid="close-button"]',
        '.dismiss-button',
      ];
      for (const sel of dismissSelectors) {
        const el = document.querySelector(sel);
        if (el && el.offsetParent !== null) {
          el.click();
        }
      }
    });
  } catch { /* non-critical */ }
}
