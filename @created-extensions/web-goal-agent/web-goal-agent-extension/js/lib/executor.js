/**
 * Executor: handles `command` messages from the backend.
 *
 * Goal-agent verbs: bind_tab, release_tab, navigate, observe, locate,
 * scroll_search, act, wait_for, check, settle, extract_fields, screenshot,
 * overlay.
 * ai-web-auto core verbs (so existing BrowserAgent scripts can drive this
 * extension on its port): navigate, screenshot, execute_script, extract,
 * click, type, key, scroll, find, wait, list_tabs.
 *
 * A run binds exactly one tab. Commands act on that tab only — the platform's
 * "guess the active tab" behaviour was the source of its tab-targeting bug.
 */

import * as cdp from './cdp.js';
import { callPage, PageError } from './page.js';
import { originOf } from './protocol.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export class ExecError extends Error {
  constructor(code, message, details) { super(message); this.code = code; this.details = details; }
}

export function createExecutor({ link, onOverlay }) {
  const bound = { tabId: null, runId: null, allowed: [], baselineUrl: '' };
  let compatTabId = null;
  const overlayLog = [];

  // ── Tabs and authorization ───────────────────────────────────────────────

  async function getTab(tabId) {
    try { return await chrome.tabs.get(tabId); } catch { throw new ExecError('tab_closed', 'the target tab was closed'); }
  }

  async function hasPermission(origin) {
    if (!origin) return false;
    return chrome.permissions.contains({ origins: [`${origin}/*`] });
  }

  async function targetTab(params = {}) {
    if (bound.tabId != null) return bound.tabId;
    if (params.tab_id != null) return params.tab_id;
    if (compatTabId != null) {
      try { await chrome.tabs.get(compatTabId); return compatTabId; } catch { compatTabId = null; }
    }
    const [active] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (active && originOf(active.url || '')) return (compatTabId = active.id);
    throw new ExecError('no_target_tab', 'no run is bound to a tab and no web tab is active');
  }

  /** Refuse to act outside the run's authorized origins (defence in depth: the engine checks too). */
  async function assertAllowed(tabId) {
    const tab = await getTab(tabId);
    const origin = originOf(tab.url || '');
    if (bound.tabId === tabId && bound.allowed.length && !bound.allowed.includes(origin)) {
      throw new ExecError('origin_not_allowed', `the tab is on ${origin || tab.url}, outside the authorized origins`);
    }
    if (origin && !(await hasPermission(origin))) {
      throw new ExecError('no_permission', `site access for ${origin} is not granted — authorize it in the popup`);
    }
    return tab;
  }

  async function activate(tabId) {
    const tab = await getTab(tabId);
    if (!tab.active) await chrome.tabs.update(tabId, { active: true });
    return tab;
  }

  async function waitForComplete(tabId, timeoutMs, { sawLoading = false } = {}) {
    const deadline = Date.now() + timeoutMs;
    const started = Date.now();
    while (Date.now() < deadline) {
      const tab = await getTab(tabId);
      if (tab.status === 'loading') sawLoading = true;
      if (tab.status === 'complete' && (sawLoading || Date.now() - started > 400)) return tab;
      await sleep(100);
    }
    throw new ExecError('navigation_timeout', `the page did not finish loading within ${Math.round(timeoutMs / 1000)}s`);
  }

  // ── Page calls with navigation tolerance ──────────────────────────────────

  async function page(tabId, op, args, opts) {
    try {
      return await callPage(tabId, op, args, opts);
    } catch (e) {
      if (e instanceof PageError) throw new ExecError(e.code, e.message);
      throw e;
    }
  }

  /** Retries once across a navigation (the call ran in a document that went away). */
  async function pageAcrossNav(tabId, op, args, timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    let navigated = false;
    for (;;) {
      try {
        const res = await page(tabId, op, { ...args, timeout_ms: Math.max(100, deadline - Date.now()) },
          { timeoutMs: Math.max(100, deadline - Date.now()) + 10000 });
        return { ...res, navigated };
      } catch (e) {
        if (e.code !== 'navigating' || Date.now() >= deadline) throw e;
        navigated = true;
        await waitForComplete(tabId, Math.max(500, deadline - Date.now()), { sawLoading: true }).catch(() => {});
      }
    }
  }

  async function overlay(tabId, args) {
    overlayLog.push({ t: Date.now(), tab: tabId, ...args, handle: undefined });
    if (overlayLog.length > 400) overlayLog.shift();
    onOverlay?.(args);
    try { await callPage(tabId, 'overlay', args, { timeoutMs: 3000 }); } catch { /* feedback is best-effort */ }
  }

  // ── Goal-agent verbs ──────────────────────────────────────────────────────

  const handlers = {
    async bind_tab({ tab_id, allowed_origins = [], run_id }) {
      const tab = await getTab(tab_id);
      Object.assign(bound, { tabId: tab_id, runId: run_id, allowed: allowed_origins, baselineUrl: tab.url || '' });
      const origin = originOf(tab.url || '');
      const permitted = origin ? await hasPermission(origin) : false;
      await activate(tab_id);
      if (origin && permitted) {
        await cdp.ensureAttached(tab_id);
        await page(tab_id, 'ping', {});
      }
      return { ok: true, url: tab.url || '', title: tab.title || '', origin, permitted, attached: cdp.isAttached(tab_id) };
    },

    async release_tab({ outcome = 'idle', message = '' }) {
      const tabId = bound.tabId;
      if (tabId == null) return { ok: true };
      const state = outcome === 'succeeded' ? 'success' : outcome === 'failed' ? 'fail' : 'idle';
      await overlay(tabId, { op: 'done', state, phase: outcome, text: message, linger_ms: outcome === 'failed' ? 15000 : 6000 });
      await cdp.detach(tabId);
      Object.assign(bound, { tabId: null, runId: null, allowed: [], baselineUrl: '' });
      return { ok: true };
    },

    async navigate({ url, timeout_ms = 30000, tab_id }) {
      const origin = originOf(url);
      if (!origin) throw new ExecError('bad_url', `not an http(s) URL: ${url}`);
      if (bound.tabId != null && bound.allowed.length && !bound.allowed.includes(origin)) {
        throw new ExecError('origin_not_allowed', `${origin} is outside the authorized origins`);
      }
      if (!(await hasPermission(origin))) throw new ExecError('no_permission', `site access for ${origin} is not granted`);
      let tabId;
      try { tabId = await targetTab({ tab_id }); } catch { tabId = (await chrome.tabs.create({ url: 'about:blank', active: true })).id; compatTabId = tabId; }
      await chrome.tabs.update(tabId, { url, active: true });
      const tab = await waitForComplete(tabId, timeout_ms);
      if (originOf(tab.url || '') && (await hasPermission(originOf(tab.url)))) {
        await cdp.ensureAttached(tabId);
        await page(tabId, 'ping', {});
      }
      if (bound.tabId === tabId) bound.baselineUrl = tab.url;
      return { ok: true, url: tab.url, title: tab.title, tabId };
    },

    async observe(params) {
      const tabId = await targetTab(params);
      await assertAllowed(tabId);
      return page(tabId, 'observe', params, { timeoutMs: 20000 });
    },

    async locate({ locator, purpose = 'click', timeout_ms = 1500, label }) {
      const tabId = await targetTab();
      await assertAllowed(tabId);
      await activate(tabId);
      return (await pageAcrossNav(tabId, 'locate', { locator, purpose, label }, timeout_ms));
    },

    async scroll_search({ locator, label, max_scrolls = 15 }) {
      const tabId = await targetTab();
      await assertAllowed(tabId);
      return page(tabId, 'scroll_search', { locator, label, max_scrolls }, { timeoutMs: max_scrolls * 1500 + 5000 });
    },

    async act(params) {
      const tabId = await targetTab();
      const tab = await assertAllowed(tabId);
      await activate(tabId);
      await cdp.ensureAttached(tabId);
      bound.baselineUrl = tab.url || '';
      const result = await performAction(tabId, params);
      const after = await getTab(tabId).catch(() => tab);
      return { ...result, url: after.url };
    },

    async wait_for({ expect, timeout_ms = 2000, label }) {
      const tabId = await targetTab();
      return pageAcrossNav(tabId, 'wait_for', { expect, baseline_url: bound.baselineUrl, label }, timeout_ms);
    },

    async check({ expect }) {
      const tabId = await targetTab();
      return pageAcrossNav(tabId, 'check', { expect, baseline_url: bound.baselineUrl }, 5000);
    },

    async settle({ timeout_ms = 4000, label }) {
      const tabId = await targetTab();
      if (label) await overlay(tabId, { op: 'phase', state: 'wait', text: label });
      const [dom, net] = await Promise.all([
        pageAcrossNav(tabId, 'settle', {}, timeout_ms).catch((e) => ({ settled: false, error: e.message })),
        cdp.networkIdle(tabId, { timeoutMs: timeout_ms }),
      ]);
      return { settled: !!dom.settled && net.idle, dom, network: net };
    },

    async extract_fields({ fields }) {
      const tabId = await targetTab();
      await assertAllowed(tabId);
      return page(tabId, 'extract_fields', { fields });
    },

    async screenshot(params = {}) {
      const tabId = await targetTab(params);
      await cdp.ensureAttached(tabId);
      return { ok: true, __screenshot: await cdp.screenshot(tabId) };
    },

    async overlay(params) {
      const tabId = bound.tabId;
      if (tabId != null) await overlay(tabId, params);
      return { ok: true };
    },

    // ── ai-web-auto core compatibility ──────────────────────────────────────

    async execute_script({ script }) {
      const tabId = await targetTab();
      await assertAllowed(tabId);
      await cdp.ensureAttached(tabId);
      const { result, exceptionDetails } = await cdp.evaluate(tabId, script);
      if (exceptionDetails) return { result: { _error: exceptionDetails.exception?.description || exceptionDetails.text } };
      return { result: result?.value };
    },

    async extract({ type = 'text' }) {
      const tabId = await targetTab();
      await assertAllowed(tabId);
      await cdp.ensureAttached(tabId);
      const expr = type === 'html' ? 'document.documentElement.outerHTML.slice(0, 500000)' : 'document.body ? document.body.innerText : ""';
      const { result } = await cdp.evaluate(tabId, expr);
      return { [type === 'html' ? 'html' : 'text']: result?.value ?? '' };
    },

    async list_tabs() {
      const tabs = await chrome.tabs.query({});
      return { tabs: tabs.filter((t) => originOf(t.url || '')).map((t) => ({ id: t.id, url: t.url, title: t.title, active: t.active })) };
    },

    async click(params, target) { return legacyAct('click', params, target); },
    async type(params, target) { return legacyAct('type', { ...params, value: params.text ?? params.value ?? '' }, target); },
    async find(params, target) {
      const r = await handlers.locate({ locator: legacyLocator(params, target), purpose: 'hover', timeout_ms: params.timeout ?? 2000 });
      if (!r.ok) throw new ExecError(r.code || 'not_found', r.message || 'not found');
      return r;
    },

    async key({ key = 'Enter', ctrl, alt, shift, meta }) {
      const tabId = await targetTab();
      await assertAllowed(tabId);
      await cdp.ensureAttached(tabId);
      await cdp.key(tabId, key, Object.entries({ ctrl, alt, shift, meta }).filter(([, v]) => v).map(([k]) => k));
      return { key };
    },

    async scroll({ direction = 'down', amount = 500 }) {
      const tabId = await targetTab();
      await assertAllowed(tabId);
      await cdp.ensureAttached(tabId);
      const vp = await cdp.viewport(tabId);
      await cdp.wheel(tabId, Math.round(vp.width / 2), Math.round(vp.height / 2), direction === 'up' ? -amount : amount);
      return { scrolled: amount };
    },

    async wait({ condition = 'time', value, timeout = 10000 }) {
      if (condition === 'time') { await sleep(Math.min(parseInt(value, 10) || 1000, 60000)); return {}; }
      const loc = String(value || '').startsWith('/') ? { xpath: value } : { css: value };
      const kind = condition === 'element_hidden' ? 'hidden' : 'visible';
      const r = await handlers.wait_for({ expect: [{ kind, args: loc }], timeout_ms: timeout });
      if (!r.ok) throw new ExecError('timeout', `${condition} not met for ${value}`);
      return r;
    },
  };

  function legacyLocator(params, target) {
    const sel = params.selector || params.xpath || target?.value || '';
    if (!sel) throw new ExecError('no_selector', 'no selector provided');
    if (sel.startsWith('text:')) return { text: { contains: sel.slice(5) } };
    return sel.startsWith('/') || sel.startsWith('./') ? { xpath: sel } : { css: sel };
  }

  async function legacyAct(action, params, target) {
    const locator = legacyLocator(params, target);
    const found = await handlers.locate({ locator, purpose: action, timeout_ms: 3000 });
    if (!found.ok) throw new ExecError(found.code || 'not_found', found.message || 'element not found');
    const r = await handlers.act({ action, handle: found.handle, locator, label: `${action} ${locator.css || locator.xpath || ''}`,
      value: params.value, clear: params.clear !== false && params.clear_first !== false });
    if (!r.ok) throw new ExecError(r.code || 'act_failed', r.message || `${action} failed`);
    return r;
  }

  // ── Actions ───────────────────────────────────────────────────────────────

  async function performAction(tabId, p) {
    const { action, handle, locator, label = action } = p;
    if (!handle && (action === 'press' || action === 'scroll')) {
      if (action === 'press') {
        await cdp.key(tabId, p.key || 'Enter', p.modifiers || []);
        await overlay(tabId, { op: 'phase', state: 'act', text: `Pressed ${(p.modifiers || []).concat(p.key || 'Enter').join('+')}` });
      } else {
        const vp = await cdp.viewport(tabId);
        await cdp.wheel(tabId, Math.round(vp.width / 2), Math.round(vp.height / 2), (p.direction === 'up' ? -0.8 : 0.8) * vp.height);
      }
      return { ok: true, dispatched: true };
    }
    // Re-validate immediately before input: fresh reference, visible, enabled, unobstructed, not moving.
    const prep = await page(tabId, 'prepare', { handle, locator, purpose: action });
    if (!prep.ok) return { ...prep, dispatched: false };
    const h = prep.handle;
    const { x, y } = prep.point;
    await overlay(tabId, { op: 'state', handle: h, state: 'act', text: label });

    switch (action) {
      case 'click':
      case 'check':
        await cdp.click(tabId, x, y);
        return { ok: true, dispatched: true, refreshed: prep.refreshed };
      case 'hover':
        await cdp.move(tabId, x, y);
        return { ok: true, dispatched: true };
      case 'scroll':
        await cdp.wheel(tabId, x, y, p.direction === 'up' ? -400 : 400);
        return { ok: true, dispatched: true };
      case 'press':
        await page(tabId, 'focus', { handle: h });
        await cdp.key(tabId, p.key || 'Enter', p.modifiers || []);
        return { ok: true, dispatched: true };
      case 'select': {
        const r = await page(tabId, 'select_option', { handle: h, value: p.value ?? '' });
        return r.ok ? { ok: true, dispatched: true } : { ...r, dispatched: false };
      }
      case 'type':
        return typeInto(tabId, h, x, y, p);
      default:
        return { ok: false, code: 'unsupported_action', message: `unsupported action ${action}`, dispatched: false, terminal: true };
    }
  }

  async function typeInto(tabId, h, x, y, p) {
    const value = String(p.value ?? '');
    const clear = p.clear !== false;
    await cdp.click(tabId, x, y); // trusted focus — Ctrl+A before focus would select the whole page
    const focus = await page(tabId, 'focus_info', { handle: h });
    if (!focus.ok) return { ok: false, code: 'focus_failed', message: 'could not focus the field', dispatched: true };
    if (clear) {
      await cdp.clearFocused(tabId);
      const v = await page(tabId, 'read_value', { handle: h });
      if (v.ok && !v.empty) {
        await page(tabId, 'select_all_in', { handle: h });
        await cdp.deleteBackward(tabId);
      }
    }
    await cdp.insertText(tabId, value);
    let method = 'cdp-insertText';
    let match = await page(tabId, 'value_matches', { handle: h, value, mode: clear ? 'equals' : 'contains' });
    if (!match.ok && clear) {
      // Framework-controlled inputs occasionally drop trusted inserts; the native
      // setter + input event is the React-compatible fallback (CLAUDE.md).
      const set = await page(tabId, 'set_value_native', { handle: h, value });
      if (set.ok) {
        method = 'native-setter';
        match = await page(tabId, 'value_matches', { handle: h, value, mode: 'equals' });
      }
    }
    return { ok: !!match.ok, dispatched: true, method, value_ok: !!match.ok, code: match.ok ? undefined : 'value_mismatch',
      message: match.ok ? undefined : 'the field does not hold the typed value' };
  }

  // ── Dispatcher ────────────────────────────────────────────────────────────

  async function onCommand(cmd) {
    const { id, action, params = {}, target } = cmd;
    const handler = handlers[action];
    if (!handler) {
      link.respond(id, 'failure', { code: 'unsupported' }, `unsupported action: ${action}`);
      return;
    }
    try {
      const data = (await handler(params, target)) || {};
      const shot = data.__screenshot;
      delete data.__screenshot;
      link.respond(id, 'success', data, null, shot);
    } catch (e) {
      const code = e.code || 'error';
      link.respond(id, 'failure', { code, ...(e.details || {}) }, String(e.message || e));
    }
  }

  return { onCommand, bound, overlayLog, overlay };
}
