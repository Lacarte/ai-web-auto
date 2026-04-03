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

const WS_URL = 'ws://localhost:8765';
const ws = new WebSocketClient(WS_URL);
const router = new MessageRouter(ws);

// Track the tab we're automating
let targetTabId = null;

// Track debugger attachment per tab to maintain single session
const attachedTabs = new Set();

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
});

// Clean up debugger on detach events (user closed devtools bar, etc.)
chrome.debugger.onDetach.addListener((source) => {
  if (source.tabId) attachedTabs.delete(source.tabId);
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
  await cdpSend(tabId, 'Input.dispatchMouseEvent', {
    type: 'mousePressed', x, y, button: 'left', clickCount: 1,
  });
  await cdpSend(tabId, 'Input.dispatchMouseEvent', {
    type: 'mouseReleased', x, y, button: 'left', clickCount: 1,
  });
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
  if (targetTabId) {
    try {
      const tab = await chrome.tabs.get(targetTabId);
      if (tab) return targetTabId;
    } catch {
      targetTabId = null;
    }
  }
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab?.id || null;
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

  try {
    switch (action) {

      // ── NAVIGATE ────────────────────────────────────────────────────────
      case 'navigate': {
        const url = params.url;
        console.log(`[SW] Navigate to: ${url}`);
        const newTab = await chrome.tabs.create({ url, active: true });
        targetTabId = newTab.id;
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
