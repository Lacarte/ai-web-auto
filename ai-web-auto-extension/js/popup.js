/**
 * Popup UI controller — displays connection status, config, and activity log.
 */

const wsStatus = document.getElementById('ws-status');
const authInput = document.getElementById('auth-token');
const logContainer = document.getElementById('log-container');

// ── Load saved token ────────────────────────────────────────────────────

chrome.storage.local.get('ws_auth_token', (res) => {
  authInput.value = res.ws_auth_token || 'local-dev-token';
});

// ── Poll status ─────────────────────────────────────────────────────────

function refreshStatus() {
  chrome.runtime.sendMessage({ channel: 'popup-to-bg' }, (resp) => {
    if (chrome.runtime.lastError || !resp) return;
    wsStatus.textContent = resp.connected ? 'Connected' : 'Disconnected';
    wsStatus.className = `badge ${resp.connected ? 'connected' : 'disconnected'}`;

    logContainer.innerHTML = '';
    (resp.taskLog || []).slice(-30).forEach((entry) => {
      const div = document.createElement('div');
      div.className = `log-entry ${entry.status || ''}`;
      const time = new Date(entry.timestamp).toLocaleTimeString();
      div.textContent = `[${time}] ${entry.action || entry.event || 'info'} — ${entry.status || ''}`;
      logContainer.appendChild(div);
    });
    logContainer.scrollTop = logContainer.scrollHeight;
  });
}

refreshStatus();
setInterval(refreshStatus, 2000);

// ── Buttons ─────────────────────────────────────────────────────────────

document.getElementById('save-token').addEventListener('click', () => {
  const token = authInput.value.trim();
  if (!token) return;
  chrome.storage.local.set({ ws_auth_token: token }, () => {
    log('Token saved — reconnecting...');
    // Storage change listener in service worker will auto-reconnect
  });
});

document.getElementById('btn-reconnect').addEventListener('click', () => {
  // Reload the service worker to force reconnect
  chrome.runtime.sendMessage({ channel: 'popup-to-bg', action: 'reconnect' });
  log('Reconnecting...');
});

document.getElementById('btn-screenshot').addEventListener('click', async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return log('No active tab', 'error');
  try {
    const dataUrl = await chrome.tabs.captureVisibleTab(null, { format: 'png' });
    if (dataUrl) log('Screenshot captured');
    else log('Screenshot failed', 'error');
  } catch (e) {
    log(`Screenshot failed: ${e.message}`, 'error');
  }
});

document.getElementById('btn-extract').addEventListener('click', async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return log('No active tab', 'error');
  if (tab.url?.startsWith('chrome://') || tab.url?.startsWith('chrome-extension://') || tab.url?.startsWith('edge://')) {
    return log('Cannot extract from browser pages', 'error');
  }

  // Ensure content script is injected
  try {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ['js/content/content-main.js'],
    });
  } catch {
    // Already injected or can't inject — try sending anyway
  }

  chrome.tabs.sendMessage(tab.id, {
    channel: 'bg-to-content',
    payload: { id: 'manual', action: 'extract', target: {}, params: { type: 'text' } },
  }, (resp) => {
    if (chrome.runtime.lastError) {
      log('Content script not available on this page', 'error');
    } else if (resp?.status === 'success') {
      log('DOM extracted');
    } else {
      log(`Extract failed: ${resp?.error || 'unknown'}`, 'error');
    }
  });
});

function log(text, cls = 'success') {
  const div = document.createElement('div');
  div.className = `log-entry ${cls}`;
  div.textContent = `[${new Date().toLocaleTimeString()}] ${text}`;
  logContainer.appendChild(div);
  logContainer.scrollTop = logContainer.scrollHeight;
}
