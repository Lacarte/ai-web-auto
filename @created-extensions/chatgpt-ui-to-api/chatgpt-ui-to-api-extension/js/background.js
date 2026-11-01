// ChatGPT API Bridge — Background Service Worker
// Automates ChatGPT: type prompt, send, wait for response, extract text.
// Connects to a local Python REST API server via WebSocket for programmatic access.

const CHATGPT_URL = "https://chatgpt.com/";
const WS_URL = "ws://localhost:8766";
// No newlines — ProseMirror treats \n as Enter which submits the form
const SYSTEM_SUFFIX = " — Respond directly and concisely, no markdown, no extra commentary.";

let state = { status: "idle", step: "", tabId: null, attached: false };
let ws = null;

function updateState(p) {
  Object.assign(state, p);
  chrome.runtime.sendMessage({ type: "state", state }).catch(() => {});
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// Keep SW alive
chrome.alarms.create("keepalive", { periodInMinutes: 0.25 });
chrome.alarms.onAlarm.addListener(() => { connectWS(); });

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

async function cdpType(tabId, text) {
  // Replace newlines with spaces — ProseMirror treats \n as Enter (submits form)
  const safe = text.replace(/\n/g, " ");
  for (const ch of safe) {
    await cdp(tabId, "Input.insertText", { text: ch });
    await sleep(3);
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
    if (s.startsWith("/")) {
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

async function findOrOpenChatGPT() {
  const tabs = await chrome.tabs.query({});
  const existing = tabs.find(t => t.url?.startsWith("https://chatgpt.com"));
  if (existing) {
    await chrome.tabs.update(existing.id, { active: true });
    return existing.id;
  }
  const tab = await chrome.tabs.create({ url: CHATGPT_URL });
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

// ── Core: send prompt and get response ──────────────────────────────────────

async function sendPrompt(prompt, options = {}) {
  const { appendSuffix = true, timeoutMs = 120000 } = options;
  const fullPrompt = appendSuffix ? prompt + SYSTEM_SUFFIX : prompt;

  try {
    updateState({ status: "running", step: "Finding ChatGPT tab..." });
    const tabId = await findOrOpenChatGPT();
    state.tabId = tabId;
    await sleep(1000);

    await attach(tabId);

    // Count existing assistant messages before we send
    const beforeCount = await exec(tabId, () => {
      return document.querySelectorAll('[data-message-author-role="assistant"]').length;
    }) || 0;

    // Click the input to focus
    updateState({ step: "Typing prompt..." });
    const inputPos = await findPos(tabId, "#prompt-textarea");
    if (!inputPos?.found) throw new Error("Cannot find ChatGPT input (#prompt-textarea)");

    await cdpClick(tabId, inputPos.x, inputPos.y);
    await sleep(300);
    await cdpClear(tabId);
    await sleep(200);
    await cdpType(tabId, fullPrompt);
    await sleep(1000); // Wait for ProseMirror to fully process all chars

    // Verify text is in the input before sending
    const typed = await exec(tabId, (expected) => {
      const el = document.querySelector("#prompt-textarea");
      return el ? el.textContent.includes(expected.slice(0, 20)) : false;
    }, [prompt]);
    if (!typed) console.warn("[BG] Text verification failed — sending anyway");

    // Click send
    updateState({ step: "Sending..." });
    const sendPos = await findPos(tabId, '[data-testid="send-button"]');
    if (!sendPos?.found) throw new Error("Cannot find send button");
    await cdpClick(tabId, sendPos.x, sendPos.y);
    await sleep(1000);

    // Detach during polling
    await detach(tabId);

    // Poll for response completion
    updateState({ step: "Waiting for response..." });
    const start = Date.now();
    let responseText = "";

    while (Date.now() - start < timeoutMs) {
      const result = await exec(tabId, (prevCount) => {
        const msgs = document.querySelectorAll('[data-message-author-role="assistant"]');
        if (msgs.length <= prevCount) return { status: "waiting", count: msgs.length };

        const last = msgs[msgs.length - 1];
        const stopBtn = document.querySelector('[data-testid="stop-button"], button[aria-label="Stop generating"]');
        const text = last.innerText.trim();
        return {
          status: stopBtn ? "streaming" : "done",
          text,
          count: msgs.length,
        };
      }, [beforeCount]);

      const elapsed = Math.round((Date.now() - start) / 1000);
      const status = result?.status || "waiting";
      updateState({ step: `${status}... ${elapsed}s` });

      if (status === "done" && result?.text) {
        responseText = result.text;
        break;
      }
      await sleep(1000);
    }

    if (!responseText) {
      // Last attempt to grab whatever is there
      responseText = await exec(tabId, () => {
        const msgs = document.querySelectorAll('[data-message-author-role="assistant"]');
        const last = msgs[msgs.length - 1];
        return last ? last.innerText.trim() : "";
      }) || "";
    }

    updateState({ status: "done", step: "Response received" });
    return { success: true, response: responseText, prompt };

  } catch (err) {
    console.error("[BG] Error:", err);
    updateState({ status: "error", step: err.message });
    if (state.tabId) await detach(state.tabId);
    return { success: false, error: err.message, prompt };
  }
}

// ── WebSocket client to Python REST API server ──────────────────────────────

function connectWS() {
  if (ws && ws.readyState === WebSocket.OPEN) return;

  try {
    ws = new WebSocket(WS_URL);

    ws.onopen = () => {
      console.log("[WS] Connected to API server");
      ws.send(JSON.stringify({ type: "hello", from: "chatgpt-ui-to-api-bridge" }));
    };

    ws.onmessage = async (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "prompt") {
          const result = await sendPrompt(msg.prompt, msg.options || {});
          ws.send(JSON.stringify({
            type: "response",
            id: msg.id,
            ...result,
          }));
        }
      } catch (e) {
        console.error("[WS] Message error:", e);
      }
    };

    ws.onclose = () => {
      console.log("[WS] Disconnected, will retry...");
      ws = null;
    };

    ws.onerror = () => { ws = null; };
  } catch (e) {
    ws = null;
  }
}

// Connect on startup
connectWS();

// ── Message handler (from popup) ────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "prompt") {
    sendPrompt(msg.prompt, msg.options || {}).then(result => {
      sendResponse(result);
    });
    return true; // async
  }
  if (msg.type === "getState") {
    sendResponse({ state });
    return;
  }
});

chrome.debugger.onDetach.addListener(() => { state.attached = false; });
