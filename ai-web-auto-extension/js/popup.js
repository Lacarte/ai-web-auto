/**
 * Popup UI controller — displays connection status, config, and activity log.
 * Styled with machined control-room theme (Omnitase inspired).
 */

const wsBadge = document.getElementById('ws-badge');
const wsStatus = document.getElementById('ws-status');
const statusIndicator = document.getElementById('status-indicator');
const authInput = document.getElementById('auth-token');
const logContainer = document.getElementById('log-container');
const logCount = document.getElementById('log-count');

// ── Load saved token ────────────────────────────────────────────────────

chrome.storage.local.get('ws_auth_token', (res) => {
  if (res.ws_auth_token) {
    authInput.value = res.ws_auth_token;
  }
});

// ── Poll status ─────────────────────────────────────────────────────────

function refreshStatus() {
  chrome.runtime.sendMessage({ channel: 'popup-to-bg' }, (resp) => {
    if (chrome.runtime.lastError || !resp) return;

    const isConnected = !!resp.connected;
    wsBadge.textContent = isConnected ? 'Connected' : 'Disconnected';
    wsBadge.className = `badge ${isConnected ? 'connected' : 'disconnected'}`;

    if (statusIndicator) {
      statusIndicator.className = `status-indicator ${isConnected ? 'connected' : ''}`;
    }

    if (wsStatus) {
      wsStatus.textContent = isConnected ? 'ws://localhost:8765 (live)' : 'offline';
      wsStatus.style.color = isConnected ? 'var(--ok)' : 'var(--muted)';
    }

    const logs = resp.taskLog || [];
    if (logCount) {
      logCount.textContent = `${logs.length} event${logs.length === 1 ? '' : 's'}`;
    }

    logContainer.innerHTML = '';
    if (logs.length === 0) {
      const emptyDiv = document.createElement('div');
      emptyDiv.className = 'log-entry';
      emptyDiv.style.opacity = '0.5';
      emptyDiv.textContent = 'Waiting for engine activity...';
      logContainer.appendChild(emptyDiv);
    } else {
      logs.slice(-40).forEach((entry) => {
        const div = document.createElement('div');
        const isHandshake = entry.action === 'handshake';
        const entryClass = isHandshake ? 'handshake' : (entry.status || '');
        div.className = `log-entry ${entryClass}`;

        const time = new Date(entry.timestamp).toLocaleTimeString();
        const actionLabel = isHandshake ? '🤝 Handshake' : (entry.action || entry.event || 'info');
        const detailSuffix = entry.details ? ` » ${entry.details}` : '';
        div.textContent = `[${time}] ${actionLabel} [${entry.status || 'ok'}]${detailSuffix}`;
        logContainer.appendChild(div);
      });
    }
    logContainer.scrollTop = logContainer.scrollHeight;
  });
}

refreshStatus();
setInterval(refreshStatus, 1500);

// ── Buttons ─────────────────────────────────────────────────────────────

document.getElementById('save-token').addEventListener('click', () => {
  const token = authInput.value.trim();
  if (!token) return;
  chrome.storage.local.set({ ws_auth_token: token }, () => {
    appendLocalLog('Security token saved — reconnecting...', 'success');
  });
});

document.getElementById('btn-reconnect').addEventListener('click', () => {
  chrome.runtime.sendMessage({ channel: 'popup-to-bg', action: 'reconnect' });
  appendLocalLog('Reconnecting socket...', 'running');
});

document.getElementById('btn-screenshot').addEventListener('click', async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return appendLocalLog('No active tab', 'error');
  try {
    const dataUrl = await chrome.tabs.captureVisibleTab(null, { format: 'png' });
    if (dataUrl) appendLocalLog('Viewport captured (PNG)', 'success');
    else appendLocalLog('Screenshot failed', 'error');
  } catch (e) {
    appendLocalLog(`Screenshot failed: ${e.message}`, 'error');
  }
});

document.getElementById('btn-extract').addEventListener('click', async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return appendLocalLog('No active tab', 'error');
  if (tab.url?.startsWith('chrome://') || tab.url?.startsWith('chrome-extension://') || tab.url?.startsWith('edge://')) {
    return appendLocalLog('Cannot extract from internal browser pages', 'error');
  }

  try {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ['js/content/content-main.js'],
    });
  } catch {}

  chrome.tabs.sendMessage(tab.id, {
    channel: 'bg-to-content',
    payload: { id: 'manual', action: 'extract', target: {}, params: { type: 'text' } },
  }, (resp) => {
    if (chrome.runtime.lastError) {
      appendLocalLog('Content script not accessible on this page', 'error');
    } else if (resp?.status === 'success') {
      appendLocalLog('DOM text extracted successfully', 'success');
    } else {
      appendLocalLog(`Extract failed: ${resp?.error || 'unknown'}`, 'error');
    }
  });
});

function appendLocalLog(text, cls = 'success') {
  const div = document.createElement('div');
  div.className = `log-entry ${cls}`;
  const time = new Date().toLocaleTimeString();
  div.textContent = `[${time}] ${text}`;
  logContainer.appendChild(div);
  logContainer.scrollTop = logContainer.scrollHeight;
}
