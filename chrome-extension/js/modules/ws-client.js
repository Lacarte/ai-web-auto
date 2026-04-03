/**
 * WebSocket client with auto-reconnect, auth handshake, and message handling.
 */

const DEFAULT_TOKEN = 'local-dev-token';

export class WebSocketClient {
  constructor(url) {
    this.url = url;
    this._ws = null;
    this._authToken = DEFAULT_TOKEN;
    this._reconnectDelay = 1000;
    this._maxReconnectDelay = 5000;
    this._pingInterval = null;
    this._commandHandler = null;
    this._connected = false;
    this._intentionalClose = false;
    this._tokenReady = false;

    // Load auth token from storage before connecting
    this._loadToken();

    // Listen for token changes from popup
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area === 'local' && changes.ws_auth_token) {
        this._authToken = changes.ws_auth_token.newValue || DEFAULT_TOKEN;
        this._tokenReady = true;
        console.log('[WS] Token updated from storage');
      }
    });
  }

  get connected() { return this._connected; }

  _loadToken() {
    return new Promise((resolve) => {
      chrome.storage.local.get('ws_auth_token', (res) => {
        this._authToken = res.ws_auth_token || DEFAULT_TOKEN;
        this._tokenReady = true;
        resolve();
      });
    });
  }

  setAuthToken(token) {
    this._authToken = token;
    this._tokenReady = !!token;
    chrome.storage.local.set({ ws_auth_token: token });
  }

  async connect() {
    if (this._ws && this._ws.readyState <= 1) return;
    this._intentionalClose = false;

    // Wait for token to be loaded from storage
    if (!this._tokenReady) {
      await this._loadToken();
    }

    console.log('[WS] Connecting with token:', this._authToken?.slice(0, 8) + '...');

    try {
      this._ws = new WebSocket(this.url);
    } catch {
      this._scheduleReconnect();
      return;
    }

    this._ws.onopen = () => {
      console.log('[WS] Connected, authenticating...');
      this._reconnectDelay = 1000;
      this._sendRaw({ type: 'auth', token: this._authToken });
    };

    this._ws.onmessage = (event) => {
      let msg;
      try { msg = JSON.parse(event.data); } catch { return; }

      if (msg.type === 'auth_response') {
        if (msg.status === 'accepted') {
          this._connected = true;
          this._startPing();
          console.log('[WS] Authenticated');
        } else {
          console.error('[WS] Auth denied');
          this._ws.close();
        }
        return;
      }

      if (msg.type === 'command' && this._commandHandler) {
        this._commandHandler(msg);
      }
    };

    this._ws.onclose = () => {
      const wasConnected = this._connected;
      this._connected = false;
      this._stopPing();
      if (!this._intentionalClose) {
        if (wasConnected) {
          console.log('[WS] Disconnected, reconnecting...');
        }
        this._scheduleReconnect();
      }
    };

    this._ws.onerror = () => {
      // Suppress — onclose handles reconnect. Errors here are just connection failures.
    };
  }

  disconnect() {
    this._intentionalClose = true;
    this._stopPing();
    if (this._ws) this._ws.close();
  }

  async reconnect() {
    this.disconnect();
    this._intentionalClose = false;
    await this._loadToken();
    // Small delay for socket cleanup
    await new Promise((r) => setTimeout(r, 500));
    this.connect();
  }

  onCommand(handler) {
    this._commandHandler = handler;
  }

  sendResponse(correlationId, status, data, error = null) {
    this._sendRaw({
      id: crypto.randomUUID(),
      correlation_id: correlationId,
      timestamp: new Date().toISOString(),
      type: 'response',
      status,
      data,
      error,
    });
  }

  sendEvent(eventType, data) {
    this._sendRaw({
      id: crypto.randomUUID(),
      timestamp: new Date().toISOString(),
      type: 'event',
      event: eventType,
      data,
    });
  }

  // ── Internal ──────────────────────────────────────────────────────────

  _sendRaw(obj) {
    if (this._ws && this._ws.readyState === WebSocket.OPEN) {
      this._ws.send(JSON.stringify(obj));
    }
  }

  _scheduleReconnect() {
    setTimeout(() => this.connect(), this._reconnectDelay);
    this._reconnectDelay = Math.min(this._reconnectDelay * 2, this._maxReconnectDelay);
  }

  _startPing() {
    this._stopPing();
    this._pingInterval = setInterval(() => {
      if (this._ws && this._ws.readyState === WebSocket.OPEN) {
        this._sendRaw({ type: 'ping', timestamp: new Date().toISOString() });
      }
    }, 30000);
  }

  _stopPing() {
    if (this._pingInterval) {
      clearInterval(this._pingInterval);
      this._pingInterval = null;
    }
  }
}
