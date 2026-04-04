/**
 * Content script — data extraction and fallback only.
 *
 * Architecture: The service worker is the primary automation engine.
 * All clicks and typing go through CDP (Chrome DevTools Protocol).
 * This content script handles:
 * - Data extraction (text, html, cookies, localStorage, sessionStorage)
 * - Basic click/type fallback for non-CSP pages
 * - DOM mutation observer for events
 * - Dialog interception (alert/confirm/prompt)
 *
 * execute_script is NOT handled here — it goes through service worker
 * MAIN world injection (chrome.scripting.executeScript) to bypass CSP.
 */

(() => {
  'use strict';

  // Only run in top frame — NOT iframes (avoids reCAPTCHA frames, etc.)
  if (window !== window.top) return;

  // Prevent double injection
  if (window.__automationContentLoaded) return;
  window.__automationContentLoaded = true;

  // ── Data Extraction ──────────────────────────────────────────────────

  const DataAccess = {
    getText(selector) {
      const el = selector ? document.querySelector(selector) : document.body;
      return el?.innerText || '';
    },
    getHTML(selector) {
      const el = selector ? document.querySelector(selector) : document.documentElement;
      return el?.outerHTML?.slice(0, 500000) || '';
    },
    getCookies() {
      return document.cookie;
    },
    getLocalStorage() {
      const data = {};
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        data[k] = localStorage.getItem(k);
      }
      return data;
    },
    getSessionStorage() {
      const data = {};
      for (let i = 0; i < sessionStorage.length; i++) {
        const k = sessionStorage.key(i);
        data[k] = sessionStorage.getItem(k);
      }
      return data;
    },
  };

  // ── Fallback DOM Actions (for non-CSP pages where JS click works) ───

  function resolveElement(target) {
    if (!target || !target.value) return null;
    if (target.method === 'xpath') {
      const result = document.evaluate(target.value, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
      return result.singleNodeValue;
    }
    if (target.method === 'text') {
      const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
      while (walk.nextNode()) {
        const el = walk.currentNode;
        if (el.textContent?.trim() === target.value && el.children.length === 0) return el;
      }
      return null;
    }
    return document.querySelector(target.value);
  }

  // ── Command Dispatcher ──────────────────────────────────────────────

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg.channel !== 'bg-to-content') return;
    const { action, target, params = {} } = msg.payload;

    (async () => {
      try {
        let result = {};

        switch (action) {
          case 'extract': {
            const t = params.type || 'text';
            if (t === 'text') result.extracted_data = { text: DataAccess.getText(params.selector) };
            else if (t === 'html') result.extracted_data = { html: DataAccess.getHTML(params.selector) };
            else if (t === 'cookies') result.extracted_data = { cookies: DataAccess.getCookies() };
            else if (t === 'localStorage') result.extracted_data = { localStorage: DataAccess.getLocalStorage() };
            else if (t === 'sessionStorage') result.extracted_data = { sessionStorage: DataAccess.getSessionStorage() };
            break;
          }

          // Fallback click — only for simple pages where trusted events aren't needed
          case 'click': {
            const el = resolveElement(target);
            if (!el) throw new Error(`Element not found: ${target?.value}`);
            el.scrollIntoView({ behavior: 'instant', block: 'center' });
            el.click();
            break;
          }

          // Fallback type — only for simple pages (NOT React/contentEditable)
          case 'type': {
            const el = resolveElement(target);
            if (!el) throw new Error(`Element not found: ${target?.value}`);
            el.scrollIntoView({ behavior: 'instant', block: 'center' });
            el.focus();
            if (params.clear_first !== false) {
              el.value = '';
              el.dispatchEvent(new Event('input', { bubbles: true }));
            }
            el.value = params.text || '';
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            break;
          }

          case 'scroll':
            window.scrollBy({
              top: (params.direction === 'up' ? -1 : 1) * (params.amount || 500),
              behavior: 'smooth',
            });
            await new Promise(r => setTimeout(r, 400));
            break;

          case 'wait':
            await handleWait(params);
            break;

          default:
            throw new Error(`Unknown content action: ${action}`);
        }

        sendResponse({ status: 'success', data: result });
      } catch (err) {
        sendResponse({ status: 'failure', data: {}, error: err.message });
      }
    })();
    return true; // async
  });

  async function handleWait(params) {
    const { condition, value } = params;
    const deadline = Date.now() + (params.timeout || 10000);
    if (condition === 'element_visible') {
      while (Date.now() < deadline) {
        const el = document.querySelector(value);
        if (el && el.offsetParent !== null) return;
        await new Promise(r => setTimeout(r, 200));
      }
      throw new Error(`Timeout waiting for ${value}`);
    }
    if (condition === 'element_hidden') {
      while (Date.now() < deadline) {
        const el = document.querySelector(value);
        if (!el || el.offsetParent === null) return;
        await new Promise(r => setTimeout(r, 200));
      }
      throw new Error(`Timeout waiting for ${value} to hide`);
    }
    if (condition === 'time') {
      await new Promise(r => setTimeout(r, parseInt(value) || 1000));
      return;
    }
    await new Promise(r => setTimeout(r, 1000));
  }

  // ── DOM Mutation Observer — report significant changes with context ──

  let _mutationTimeout = null;
  let _pendingMutations = [];
  const observer = new MutationObserver((mutations) => {
    _pendingMutations.push(...mutations);
    clearTimeout(_mutationTimeout);
    _mutationTimeout = setTimeout(() => {
      // Summarize pending mutations into structured counts
      let adds = 0, removes = 0, attrs = 0, texts = 0;
      const significantTags = new Set();
      for (const m of _pendingMutations) {
        if (m.type === 'childList') {
          adds += m.addedNodes.length;
          removes += m.removedNodes.length;
          for (const n of m.addedNodes) {
            if (n.nodeType === 1) significantTags.add(n.tagName.toLowerCase());
          }
        } else if (m.type === 'attributes') {
          attrs++;
        } else if (m.type === 'characterData') {
          texts++;
        }
      }
      _pendingMutations = [];

      // Only report if there were meaningful changes
      const total = adds + removes + attrs + texts;
      if (total > 0) {
        chrome.runtime.sendMessage({
          channel: 'content-to-bg',
          payload: {
            type: 'event',
            event: 'dom_mutation',
            data: {
              url: location.href,
              stats: { adds, removes, attrs, texts, total },
              tags: [...significantTags].slice(0, 10),
            },
          },
        }).catch(() => {});
      }
    }, 1000);
  });
  if (document.body) {
    observer.observe(document.body, { childList: true, subtree: true, attributes: true, characterData: true });
  }

  // ── Dialog Interception ─────────────────────────────────────────────

  const _origAlert = window.alert;
  const _origConfirm = window.confirm;
  const _origPrompt = window.prompt;

  window.alert = function (msg) {
    chrome.runtime.sendMessage({
      channel: 'content-to-bg',
      payload: { type: 'event', event: 'dialog_opened', data: { dialog: 'alert', message: msg } },
    }).catch(() => {});
    return _origAlert.call(this, msg);
  };

  window.confirm = function (msg) {
    chrome.runtime.sendMessage({
      channel: 'content-to-bg',
      payload: { type: 'event', event: 'dialog_opened', data: { dialog: 'confirm', message: msg } },
    }).catch(() => {});
    return _origConfirm.call(this, msg);
  };

  window.prompt = function (msg, def) {
    chrome.runtime.sendMessage({
      channel: 'content-to-bg',
      payload: { type: 'event', event: 'dialog_opened', data: { dialog: 'prompt', message: msg } },
    }).catch(() => {});
    return _origPrompt.call(this, msg, def);
  };
})();
