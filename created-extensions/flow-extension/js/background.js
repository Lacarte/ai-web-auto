// Flow Automator — Background Service Worker (v2 clean rewrite)

const FLOW_URL = "https://labs.google/fx/tools/flow/";

const SELECTORS = {
  promptInput: '/html/body/div[1]/div[1]/div[5]/div/div/div[1]/div',
  mode: {
    Image: '[role="tab"][id$="-trigger-IMAGE"]',
    Video: '[role="tab"][id$="-trigger-VIDEO"]',
  },
  ratio: {
    '16:9': '[role="tab"][id$="-trigger-LANDSCAPE"]',
    '4:3':  '[role="tab"][id$="-trigger-LANDSCAPE_4_3"]',
    '1:1':  '[role="tab"][id$="-trigger-SQUARE"]',
    '3:4':  '[role="tab"][id$="-trigger-PORTRAIT_3_4"]',
    '9:16': '[role="tab"][id$="-trigger-PORTRAIT"]',
  },
  count: {
    1: '[role="tab"][id$="-trigger-1"]',
    2: '[role="tab"][id$="-trigger-2"]',
    3: '[role="tab"][id$="-trigger-3"]',
    4: '[role="tab"][id$="-trigger-4"]',
  },
};

let state = { status: "idle", step: "", tabId: null, attached: false };

function updateState(p) {
  Object.assign(state, p);
  chrome.runtime.sendMessage({ type: "state", state }).catch(() => {});
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// Keep SW alive
chrome.alarms.create('keepalive', { periodInMinutes: 0.25 });
chrome.alarms.onAlarm.addListener(() => {});

// ── CDP helpers ─────────────────────────────────────────────────────────────

async function attach(tabId) {
  if (!state.attached) {
    await chrome.debugger.attach({ tabId }, "1.3");
    state.attached = true;
  }
}

async function detach(tabId) {
  if (state.attached) {
    try { await chrome.debugger.detach({ tabId }); } catch {}
    state.attached = false;
  }
}

function cdp(tabId, method, params = {}) {
  return chrome.debugger.sendCommand({ tabId }, method, params);
}

async function cdpClick(tabId, x, y) {
  await cdp(tabId, "Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: 1 });
  await cdp(tabId, "Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: 1 });
}

async function cdpEscape(tabId) {
  await cdp(tabId, "Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await cdp(tabId, "Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
}

async function cdpType(tabId, text) {
  for (const ch of text) {
    await cdp(tabId, "Input.insertText", { text: ch });
    await sleep(5);
  }
}

async function cdpClear(tabId) {
  await cdp(tabId, "Input.dispatchKeyEvent", { type: "keyDown", modifiers: 2, key: "a", code: "KeyA", windowsVirtualKeyCode: 65 });
  await cdp(tabId, "Input.dispatchKeyEvent", { type: "keyUp", modifiers: 2, key: "a", code: "KeyA", windowsVirtualKeyCode: 65 });
  await sleep(50);
  await cdp(tabId, "Input.dispatchKeyEvent", { type: "keyDown", key: "Backspace", code: "Backspace", windowsVirtualKeyCode: 8 });
  await cdp(tabId, "Input.dispatchKeyEvent", { type: "keyUp", key: "Backspace", code: "Backspace", windowsVirtualKeyCode: 8 });
  await sleep(100);
}

// ── MAIN world JS execution ─────────────────────────────────────────────────

async function exec(tabId, func, args = []) {
  const results = await chrome.scripting.executeScript({
    target: { tabId }, world: "MAIN", func, args,
  });
  return results?.[0]?.result;
}

// Find element center by CSS selector or XPath, return {found, x, y}
async function findPos(tabId, sel) {
  return exec(tabId, (s) => {
    let el = null;
    if (s.startsWith('/')) {
      el = document.evaluate(s, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
    }
    if (!el) { try { el = document.querySelector(s); } catch {} }
    if (!el && s.includes('aria-haspopup')) {
      for (const b of document.querySelectorAll(s)) {
        if (b.getBoundingClientRect().y > 400) { el = b; break; }
      }
    }
    if (el) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0)
        return { found: true, x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2) };
    }
    return { found: false };
  }, [sel]);
}

// ── Tab finding ─────────────────────────────────────────────────────────────

async function findOrOpenFlowTab() {
  const tabs = await chrome.tabs.query({});
  const existing = tabs.find(t => t.url?.startsWith("https://labs.google/fx"));
  if (existing) {
    await chrome.tabs.update(existing.id, { active: true });
    return existing.id;
  }
  const tab = await chrome.tabs.create({ url: FLOW_URL });
  await new Promise(resolve => {
    const fn = (id, info) => { if (id === tab.id && info.status === "complete") { chrome.tabs.onUpdated.removeListener(fn); resolve(); } };
    chrome.tabs.onUpdated.addListener(fn);
  });
  await sleep(3000);
  return tab.id;
}

// ── Main orchestrator ───────────────────────────────────────────────────────

async function runGeneration(options) {
  const { prompt, mode, ratio, count, autoDownload } = options;

  try {
    // 1. Find or open Flow tab
    updateState({ status: "running", step: "Finding Flow tab..." });
    const tabId = await findOrOpenFlowTab();
    state.tabId = tabId;
    await sleep(2000);

    // Attach debugger for the ENTIRE flow — never detach until done
    await attach(tabId);
    console.log("[BG] Debugger attached to tab", tabId);

    // 2. Handle landing page (banner + new project)
    updateState({ step: "Preparing project..." });
    const page = await exec(tabId, () => {
      const hasPrompt = !!document.querySelector('[contenteditable="true"]');
      let closeBtn = null;
      for (const b of document.querySelectorAll('button,[role="button"]')) {
        const t = b.textContent.trim();
        const r = b.getBoundingClientRect();
        if ((t === '×' || t.toLowerCase() === 'close' || t.toLowerCase() === 'x' ||
             b.getAttribute('aria-label')?.toLowerCase().includes('close') ||
             b.getAttribute('aria-label')?.toLowerCase().includes('dismiss'))
            && r.x > 800 && r.y < 200 && r.width < 80) {
          closeBtn = { x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2) };
          break;
        }
      }
      let newBtn = null;
      for (const b of document.querySelectorAll('button,a,div[tabindex],[role="button"]')) {
        if (b.textContent.toLowerCase().includes('new project')) {
          const r = b.getBoundingClientRect();
          if (r.width > 30) { newBtn = { x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2) }; break; }
        }
      }
      return { hasPrompt, closeBtn, newBtn };
    });
    console.log("[BG] Page state:", JSON.stringify(page));

    if (page?.closeBtn) {
      updateState({ step: "Closing banner..." });
      await cdpClick(tabId, page.closeBtn.x, page.closeBtn.y);
      await sleep(1000);
    }
    if (!page?.hasPrompt) {
      // Re-find new project button after banner dismiss
      const np = page?.newBtn || await exec(tabId, () => {
        for (const b of document.querySelectorAll('button,a,div[tabindex],[role="button"]')) {
          if (b.textContent.toLowerCase().includes('new project')) {
            const r = b.getBoundingClientRect();
            if (r.width > 30) return { x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2) };
          }
        }
        return null;
      });
      if (np) {
        updateState({ step: "Creating new project..." });
        await cdpClick(tabId, np.x, np.y);
        await sleep(5000);
      }
    }

    // 3. Type prompt — click input via CDP first to ensure focus
    updateState({ step: "Typing prompt..." });
    {
      // Find the prompt input position
      const inputPos = await findPos(tabId, SELECTORS.promptInput);
      if (inputPos?.found) {
        // CDP click to focus the input (trusted event)
        await cdpClick(tabId, inputPos.x, inputPos.y);
        await sleep(300);
      } else {
        // Fallback: find any contenteditable near bottom
        const fallback = await exec(tabId, () => {
          for (const c of document.querySelectorAll('[contenteditable="true"],[role="textbox"]')) {
            const r = c.getBoundingClientRect();
            if (r.y > 400 && r.width > 100) return { x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2) };
          }
          return null;
        });
        if (fallback) await cdpClick(tabId, fallback.x, fallback.y);
        await sleep(300);
      }
      // Now clear and type (input is focused via CDP click)
      await cdpClear(tabId);
      await cdpType(tabId, prompt);
    }
    console.log("[BG] Typed prompt:", prompt.slice(0, 40));
    await sleep(500);

    // 4. Open settings panel — use exact xpath for the Nano Banana button
    updateState({ step: "Opening settings..." });
    let panelOk = false;
    const PANEL_XPATH = '/html/body/div[1]/div[1]/div[5]/div/div/div[2]/div[2]/button[1]';
    for (let i = 0; i < 3 && !panelOk; i++) {
      const pos = await findPos(tabId, PANEL_XPATH);
      console.log(`[BG] Panel button attempt ${i+1}:`, JSON.stringify(pos));
      if (pos?.found) {
        await cdpClick(tabId, pos.x, pos.y);
        await sleep(1200);
        const tabCount = await exec(tabId, () => document.querySelectorAll('[role="tab"]').length);
        console.log(`[BG] Tabs after panel click: ${tabCount}`);
        if (tabCount > 3) panelOk = true;
      }
      if (!panelOk) await sleep(1000);
    }

    // 5. Select mode, ratio, count
    async function selectSetting(label, selector) {
      updateState({ step: `Setting ${label}...` });
      const pos = await findPos(tabId, selector);
      console.log(`[BG] ${label}: findPos=${JSON.stringify(pos)}`);
      if (pos?.found) {
        await cdpClick(tabId, pos.x, pos.y);
        await sleep(500);
        const after = await exec(tabId, (s) => {
          const el = document.querySelector(s);
          return el ? el.getAttribute('data-state') : 'missing';
        }, [selector]);
        console.log(`[BG] ${label}: after=${after}`);
        return after === 'active';
      }
      console.warn(`[BG] ${label}: NOT FOUND with selector ${selector}`);
      return false;
    }

    if (panelOk) {
      await selectSetting("Image", SELECTORS.mode[mode]);
      await selectSetting(ratio, SELECTORS.ratio[ratio]);
      await selectSetting("x" + count, SELECTORS.count[count]);

      // Log active tabs
      const active = await exec(tabId, () => {
        return Array.from(document.querySelectorAll('[role="tab"][data-state="active"]'))
          .map(t => t.textContent.trim().slice(0, 15));
      });
      console.log("[BG] Active settings:", JSON.stringify(active));
      updateState({ step: `Settings: ${(active || []).join(', ')}` });
    }

    // 6. Close panel
    updateState({ step: "Closing settings..." });
    await cdpEscape(tabId);
    await sleep(500);

    // 7. Click Create
    updateState({ step: "Generating..." });
    const createPos = await exec(tabId, () => {
      for (const b of document.querySelectorAll('button')) {
        if (b.textContent.includes('arrow_forward') && b.textContent.includes('Create')) {
          const r = b.getBoundingClientRect();
          return { found: true, x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2) };
        }
      }
      return { found: false };
    });
    console.log("[BG] Create button:", JSON.stringify(createPos));
    if (createPos?.found) await cdpClick(tabId, createPos.x, createPos.y);

    // Detach debugger before polling
    await detach(tabId);
    await sleep(2000);

    // 8. Poll for images
    updateState({ step: "Waiting for images..." });
    const urls = await pollForImages(tabId);

    if (urls.length === 0) {
      updateState({ status: "error", step: "Timeout — no images detected" });
      return;
    }

    // 9. Download
    updateState({ step: `${urls.length} image(s) generated!` });
    if (autoDownload) {
      for (const url of urls) {
        try { await chrome.downloads.download({ url, conflictAction: "uniquify" }); } catch (e) { console.error("DL fail:", e); }
      }
      updateState({ status: "done", step: `Downloaded ${urls.length} image(s)!` });
    } else {
      updateState({ status: "done", step: `${urls.length} image(s) ready` });
    }

  } catch (err) {
    console.error("[BG] Error:", err);
    updateState({ status: "error", step: `Error: ${err.message}` });
    if (state.tabId) await detach(state.tabId);
  }
}

async function pollForImages(tabId, maxMs = 180000) {
  const start = Date.now();
  while (Date.now() - start < maxMs) {
    const result = await exec(tabId, () => {
      const urls = [];
      for (const img of document.querySelectorAll("img")) {
        if (img.width > 100 && img.src.includes("media.getMediaUrlRedirect")) urls.push(img.src);
      }
      return { count: urls.length, urls };
    });
    const elapsed = Math.round((Date.now() - start) / 1000);
    updateState({ step: `Waiting... ${result.count} image(s) [${elapsed}s]` });
    if (result.count > 0) return result.urls;
    await sleep(3000);
  }
  return [];
}

// ── Message handler ─────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "generate") { runGeneration(msg.options); sendResponse({ ok: true }); }
  else if (msg.type === "getState") { sendResponse({ state }); }
  return true;
});

chrome.debugger.onDetach.addListener(() => { state.attached = false; });
