/**
 * Calls into the in-page library (js/page/agent-page.js) in the ISOLATED world.
 * Injects it on first use per document, and turns Chrome's error strings into
 * codes the engine can act on (navigating vs. no permission vs. tab gone).
 */

import { whenDialogOpens, openDialog } from './cdp.js';

const PAGE_FILE = 'js/page/agent-page.js';

export class PageError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}

export function classify(err) {
  const msg = String(err?.message || err);
  if (/No tab with id/i.test(msg)) return new PageError('tab_closed', 'the target tab was closed');
  if (/Cannot access contents|Missing host permission|permission to access/i.test(msg)) {
    return new PageError('no_permission', 'site access is not granted for this page (authorize it in the popup)');
  }
  if (/chrome:\/\/|chrome-extension:\/\/|cannot be scripted|Cannot access a chrome/i.test(msg)) {
    return new PageError('unsupported_page', 'this kind of page cannot be automated');
  }
  if (/Frame with ID \d+ (was removed|is showing error page)|No frame with id|frame was removed|document.*unloaded|Execution context was destroyed|showing error page/i.test(msg)) {
    return new PageError('navigating', 'the page navigated during the call');
  }
  return new PageError('page_call_failed', msg);
}

async function exec(tabId, func, args) {
  const [res] = await chrome.scripting.executeScript({ target: { tabId, frameIds: [0] }, world: 'ISOLATED', func, args });
  return res?.result;
}

async function inject(tabId) {
  await chrome.scripting.executeScript({ target: { tabId, frameIds: [0] }, world: 'ISOLATED', files: [PAGE_FILE] });
}

/**
 * Run `op` in the page. A native JS dialog (alert/confirm/beforeunload) blocks
 * script execution, so the call is raced against one opening.
 */
export async function callPage(tabId, op, args = {}, { timeoutMs = 15000 } = {}) {
  const pending = openDialog(tabId);
  if (pending) throw new PageError('js_dialog_open', `a ${pending.dialog_type} dialog is open: "${pending.message}"`);
  const dialog = whenDialogOpens(tabId);
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new PageError('page_timeout', `page call '${op}' timed out`)), timeoutMs);
  });
  const blocked = dialog.promise.then((info) => { throw new PageError('js_dialog_open', `a ${info.dialog_type} dialog is open: "${info.message}"`); });
  try {
    return await Promise.race([run(tabId, op, args), timeout, blocked]);
  } finally {
    clearTimeout(timer);
    dialog.cancel();
  }
}

async function run(tabId, op, args) {
  for (let attempt = 0; attempt < 2; attempt++) {
    let value;
    try {
      value = await exec(tabId, (o, a) => (globalThis.__wga ? globalThis.__wga.call(o, a) : { __missing: true }), [op, args]);
    } catch (e) {
      throw classify(e);
    }
    if (value && value.__missing) {
      try { await inject(tabId); } catch (e) { throw classify(e); }
      continue;
    }
    if (value === undefined || value === null) throw new PageError('navigating', 'no result (the document changed)');
    return value;
  }
  throw new PageError('inject_failed', 'the page library could not be loaded');
}
