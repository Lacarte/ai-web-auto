// Meta AI Image Generator — Background Service Worker
// Fixed: ratio selection via role="option", download via CDP hover + click

const META_AI_URL = "https://www.meta.ai/";

const SELECTORS = {
  createImageTab: '//button[contains(.,"Create image")]',
  // The ratio trigger is whichever button shows "1:1", "9:16", or "16:9"
  ratioTrigger:   '//button[contains(.,"9:16") or contains(.,"1:1") or contains(.,"16:9")]',
  promptInput:    '//div[@contenteditable="true"]',
  sendButton:     '//button[@aria-label="Send"]',
  // Media item containers (parent of download buttons)
  mediaItem:      '.group\\/media-item',
  downloadButton: 'button[aria-label="Download"]',
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

// Move mouse to (x, y) — triggers CSS :hover on the element underneath
async function cdpHover(tabId, x, y) {
  await cdp(tabId, "Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
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

async function findPos(tabId, sel) {
  return exec(tabId, (s) => {
    let el = null;
    if (s.startsWith('/') || s.startsWith('(')) {
      el = document.evaluate(s, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
    }
    if (!el) { try { el = document.querySelector(s); } catch {} }
    if (el) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0)
        return { found: true, x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
    }
    return { found: false };
  }, [sel]);
}

// ── Tab finding ─────────────────────────────────────────────────────────────

async function findOrOpenMetaTab() {
  const tabs = await chrome.tabs.query({});
  const existing = tabs.find(t => t.url?.startsWith("https://www.meta.ai"));
  if (existing) {
    await chrome.tabs.update(existing.id, { active: true });
    return existing.id;
  }
  const tab = await chrome.tabs.create({ url: META_AI_URL });
  await new Promise(resolve => {
    const fn = (id, info) => {
      if (id === tab.id && info.status === "complete") {
        chrome.tabs.onUpdated.removeListener(fn);
        resolve();
      }
    };
    chrome.tabs.onUpdated.addListener(fn);
  });
  await sleep(5000);
  return tab.id;
}

// ── Ratio selection ─────────────────────────────────────────────────────────
// The dropdown uses Radix Select: items are role="option" with text "1:1"/"9:16"/"16:9"
// data-state="checked" on the active one, aria-selected="true"

async function selectRatio(tabId, ratio) {
  // Read the current ratio from the trigger button text
  const currentRatio = await exec(tabId, () => {
    for (const b of document.querySelectorAll('button')) {
      const t = b.textContent.trim();
      if (['1:1', '9:16', '16:9'].includes(t)) return t;
    }
    return null;
  });

  if (currentRatio === ratio) {
    console.log(`[BG] Ratio already ${ratio}`);
    return true;
  }

  // Click the trigger button to open the dropdown
  const triggerPos = await findPos(tabId, SELECTORS.ratioTrigger);
  if (!triggerPos?.found) {
    console.warn("[BG] Ratio trigger not found");
    return false;
  }
  await cdpClick(tabId, triggerPos.x, triggerPos.y);
  await sleep(800);

  // Find and click the correct role="option" by matching its text
  const optionPos = await exec(tabId, (target) => {
    const options = document.querySelectorAll('[role="option"]');
    for (const opt of options) {
      if (opt.textContent.trim() === target) {
        const r = opt.getBoundingClientRect();
        if (r.width > 0 && r.height > 0)
          return { found: true, x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
      }
    }
    return { found: false };
  }, [ratio]);

  if (!optionPos?.found) {
    console.warn(`[BG] Ratio option ${ratio} not found in dropdown`);
    return false;
  }

  await cdpClick(tabId, optionPos.x, optionPos.y);
  await sleep(500);

  // Verify: the trigger button text should now match
  const newRatio = await exec(tabId, (expected) => {
    for (const b of document.querySelectorAll('button')) {
      const t = b.textContent.trim();
      if (t === expected) return true;
    }
    return false;
  }, [ratio]);

  console.log(`[BG] Ratio ${ratio}: ${newRatio ? 'OK' : 'VERIFY FAILED'}`);
  return !!newRatio;
}

// ── Download images ─────────────────────────────────────────────────────────
// Download buttons have opacity-0 + pointer-events-none by default.
// They become visible/clickable only on hover of parent .group/media-item.
// Strategy: CDP hover over each image container, then click the Download button.

async function downloadAllImages(tabId) {
  // Download buttons have opacity-0 + pointer-events-none and CDP/JS .click()
  // doesn't reliably trigger Meta AI's React download handler.
  // Proven approach: extract image src URLs directly from <img> tags inside
  // .group/media-item containers, then use chrome.downloads.download().

  // Step 1: Extract image URLs from the page
  const imageUrls = await exec(tabId, () => {
    const urls = [];
    for (const img of document.querySelectorAll('img')) {
      // Generated images are large (>200px natural width) and from fbcdn
      if (img.naturalWidth > 200 && img.src.includes('fbcdn.net')) {
        urls.push(img.src);
      }
    }
    return urls;
  });

  console.log(`[BG] Found ${imageUrls.length} image URLs`);

  if (imageUrls.length === 0) {
    // Fallback: try all large images
    const fallbackUrls = await exec(tabId, () => {
      const urls = [];
      for (const img of document.querySelectorAll('img')) {
        if (img.naturalWidth > 200 && img.naturalHeight > 200) {
          urls.push(img.src);
        }
      }
      return urls;
    });
    console.log(`[BG] Fallback: found ${fallbackUrls.length} large images`);
    imageUrls.push(...fallbackUrls);
  }

  // Step 2: Download each image via chrome.downloads API
  let downloaded = 0;
  for (let i = 0; i < imageUrls.length; i++) {
    updateState({ step: `Downloading ${i + 1}/${imageUrls.length}...` });
    try {
      await chrome.downloads.download({
        url: imageUrls[i],
        conflictAction: "uniquify",
      });
      downloaded++;
      console.log(`[BG] Downloaded image ${i + 1}/${imageUrls.length}`);
    } catch (e) {
      console.error(`[BG] Download failed for image ${i + 1}:`, e);
    }
    await sleep(500);
  }

  return downloaded;
}

// ── Poll for generation completion ──────────────────────────────────────────

async function waitForGeneration(tabId, maxMs = 120000) {
  const start = Date.now();
  while (Date.now() - start < maxMs) {
    const result = await exec(tabId, () => {
      // Check for media-item containers (appear when images are ready)
      const mediaItems = document.querySelectorAll('.group\\/media-item');
      const downloads = document.querySelectorAll('button[aria-label="Download"]');
      // Check for loading indicators
      const hasLoader = !!document.querySelector(
        '[class*="skeleton"], [class*="shimmer"], [class*="loading"], ' +
        '[class*="spinner"], [aria-busy="true"], [class*="pulse"]'
      );
      return {
        mediaCount: mediaItems.length,
        downloadCount: downloads.length,
        hasLoader,
        ready: downloads.length > 0
      };
    });

    const elapsed = Math.round((Date.now() - start) / 1000);
    updateState({
      step: `Generating... ${elapsed}s | images=${result.mediaCount} loader=${result.hasLoader}`
    });

    if (result.ready) return result.downloadCount;
    await sleep(2000);
  }
  return 0;
}

// ── Main orchestrator ───────────────────────────────────────────────────────

async function runGeneration(options) {
  const { prompt, ratio = "9:16", autoDownload = true } = options;

  try {
    // 1. Find or open Meta AI tab
    updateState({ status: "running", step: "Finding Meta AI tab..." });
    const tabId = await findOrOpenMetaTab();
    state.tabId = tabId;
    await sleep(2000);

    // Attach debugger for the entire flow
    await attach(tabId);
    console.log("[BG] Debugger attached to tab", tabId);

    // 2. Click "Create image" tab
    updateState({ step: "Clicking Create image..." });
    const createImgPos = await findPos(tabId, SELECTORS.createImageTab);
    if (createImgPos?.found) {
      await cdpClick(tabId, createImgPos.x, createImgPos.y);
      await sleep(2000);
      console.log("[BG] Clicked Create image tab");
    } else {
      const ratioCheck = await findPos(tabId, SELECTORS.ratioTrigger);
      if (!ratioCheck?.found) {
        throw new Error("Cannot find Create image tab or ratio selector");
      }
      console.log("[BG] Already in Create image mode");
    }

    // 3. Select aspect ratio via Radix Select dropdown
    updateState({ step: `Setting ratio ${ratio}...` });
    await selectRatio(tabId, ratio);

    // 4. Type prompt into contentEditable input
    updateState({ step: "Typing prompt..." });
    const inputPos = await findPos(tabId, SELECTORS.promptInput);
    if (!inputPos?.found) throw new Error("Cannot find prompt input");

    await cdpClick(tabId, inputPos.x, inputPos.y);
    await sleep(300);
    await cdpClear(tabId);
    await sleep(200);
    await cdpType(tabId, prompt);
    await sleep(500);
    console.log("[BG] Typed prompt:", prompt.slice(0, 40));

    // Verify
    const verified = await exec(tabId, (expected) => {
      const ce = document.querySelector('[contenteditable="true"]');
      return ce && ce.textContent.includes(expected.slice(0, 20));
    }, [prompt]);
    if (!verified) console.warn("[BG] Text verification failed — proceeding anyway");

    // 5. Click Send
    updateState({ step: "Sending..." });
    const sendPos = await findPos(tabId, SELECTORS.sendButton);
    if (!sendPos?.found) throw new Error("Cannot find Send button");
    await cdpClick(tabId, sendPos.x, sendPos.y);
    await sleep(2000);
    console.log("[BG] Clicked Send");

    // Detach debugger during generation polling (no trusted events needed)
    await detach(tabId);

    // 6. Wait for generation (poll for Download buttons appearing)
    updateState({ step: "Waiting for generation..." });
    const downloadCount = await waitForGeneration(tabId, 120000);

    if (downloadCount === 0) {
      updateState({ status: "error", step: "Timeout — no images generated" });
      return;
    }

    updateState({ step: `${downloadCount} image(s) generated!` });

    // 7. Download all images via hover + click
    if (autoDownload) {
      updateState({ step: "Downloading images..." });
      await attach(tabId);

      const downloaded = await downloadAllImages(tabId);

      await detach(tabId);
      updateState({ status: "done", step: `Downloaded ${downloaded} image(s)!` });
    } else {
      updateState({ status: "done", step: `${downloadCount} image(s) ready` });
    }

  } catch (err) {
    console.error("[BG] Error:", err);
    updateState({ status: "error", step: `Error: ${err.message}` });
    if (state.tabId) await detach(state.tabId);
  }
}

// ── Message handler ─────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "generate") {
    runGeneration(msg.options);
    sendResponse({ ok: true });
  } else if (msg.type === "getState") {
    sendResponse({ state });
  }
  return true;
});

chrome.debugger.onDetach.addListener(() => { state.attached = false; });
