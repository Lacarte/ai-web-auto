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
    this._authenticated = false;
    this._intentionalClose = false;
    this._tokenReady = false;

    // ── Auth-denied circuit breaker ─────────────────────────────────────
    // A wrong token used to cause an endless ~1s deny/reconnect loop because
    // backoff reset on socket open. Now N consecutive denials trip a breaker:
    // automatic reconnection stops until the token changes or a manual
    // reconnect (popup / reconnect()) forces a retry.
    this._MAX_AUTH_FAILURES = 5;
    this._authFailures = 0;
    this._authLockedOut = false;

    // ── Heartbeat / liveness ────────────────────────────────────────────
    // A socket can stay "open" from the browser's point of view even after
    // the backend process dies (half-open TCP). We ping every PING_INTERVAL
    // and require a pong back within PONG_TIMEOUT; missing pongs mean the
    // backend is gone even though onclose never fired → no false positive.
    this._PING_INTERVAL = 5000;
    this._PONG_TIMEOUT = 8000;
    this._lastPongAt = 0;
    this._pongWatchdog = null;

    // Load auth token from storage before connecting
    this._loadToken();

    // Listen for token changes from popup
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area === 'local' && changes.ws_auth_token) {
        this._authToken = changes.ws_auth_token.newValue || DEFAULT_TOKEN;
        this._tokenReady = true;
        // A new token is a deliberate fix — clear the breaker.
        this._authFailures = 0;
        this._authLockedOut = false;
        console.log('[WS] Token updated from storage');
      }
    });
  }

  get connected() {
    // "Connected" is only true when the socket is open, auth was accepted,
    // AND we've heard a pong recently. A dead backend (half-open socket)
    // stops answering pongs, so this flips to false without waiting for
    // onclose — which is what prevents the false-positive "Connected".
    if (!this._connected || !this._authenticated) return false;
    if (!this._ws || this._ws.readyState !== WebSocket.OPEN) return false;
    if (!this._lastPongAt) return true; // just authenticated, no ping cycle yet
    return (Date.now() - this._lastPongAt) < (this._PING_INTERVAL + this._PONG_TIMEOUT);
  }

  /** Register a callback invoked on every meaningful WS lifecycle change. */
  onStatusChange(cb) { this._statusCb = cb; }

  _emitStatus(state, detail) {
    if (this._statusCb) {
      try { this._statusCb(state, detail); } catch { /* ignore */ }
    }
  }

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
    this._authFailures = 0;
    this._authLockedOut = false;
    chrome.storage.local.set({ ws_auth_token: token });
  }

  async connect(force = false) {
    if (this._authLockedOut && !force) {
      console.warn('[WS] Circuit breaker open after repeated auth denials — ' +
        'update the token or use reconnect() to retry');
      return;
    }
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
      console.log('[WS] Socket open, authenticating...');
      // Backoff is NOT reset here — a wrong token would otherwise cause an
      // instant-reconnect loop. It resets on successful authentication.
      // Socket is open but NOT yet usable — auth must succeed first.
      this._connected = true;
      this._authenticated = false;
      this._lastPongAt = 0;
      this._sendRaw({ type: 'auth', token: this._authToken });
    };

    this._ws.onmessage = (event) => {
      let msg;
      try { msg = JSON.parse(event.data); } catch { return; }

      if (msg.type === 'auth_response') {
        if (msg.status === 'accepted') {
          this._authenticated = true;
          this._authFailures = 0;
          this._authLockedOut = false;
          this._reconnectDelay = 1000; // backoff resets only on real success
          this._lastPongAt = Date.now(); // treat accept as first liveness proof
          this._startPing();
          console.log('[WS] Authenticated');
          this._emitStatus('connected');
        } else {
          this._authFailures += 1;
          console.error(`[WS] Auth denied (${this._authFailures}/${this._MAX_AUTH_FAILURES})`);
          this._authenticated = false;
          this._emitStatus('auth_denied');
          if (this._authFailures >= this._MAX_AUTH_FAILURES) {
            // Trip the breaker: stop the deny/reconnect churn until a human
            // fixes the token (storage change resets this) or reconnect() forces.
            this._authLockedOut = true;
            this._intentionalClose = true; // keep onclose from scheduling retries
            console.error('[WS] Circuit breaker OPEN — automatic reconnection stopped. ' +
              'Fix the token in the popup, then press Reconnect.');
            this._emitStatus('locked_out', `${this._authFailures} consecutive auth denials`);
          }
          this._ws.close();
        }
        return;
      }

      // Application-level pong → backend is alive.
      if (msg.type === 'pong') {
        this._lastPongAt = Date.now();
        return;
      }

      if (msg.type === 'command' && this._commandHandler) {
        this._commandHandler(msg);
      }
    };

    this._ws.onclose = () => {
      const wasConnected = this._connected;
      this._connected = false;
      this._authenticated = false;
      this._stopPing();
      if (wasConnected) this._emitStatus('disconnected');
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
    // Forced: a manual reconnect (popup button, token change) is an explicit
    // human intent to retry — bypass the auth circuit breaker.
    this.disconnect();
    this._intentionalClose = false;
    this._authFailures = 0;
    this._authLockedOut = false;
    await this._loadToken();
    // Small delay for socket cleanup
    await new Promise((r) => setTimeout(r, 500));
    this.connect(true);
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

        // If the backend has gone silent (no pong within the window), the
        // socket is half-open. Force-close it so onclose runs the reconnect
        // path — and connected flips to false — instead of lying.
        const silentFor = Date.now() - this._lastPongAt;
        if (this._lastPongAt && silentFor > (this._PING_INTERVAL + this._PONG_TIMEOUT)) {
          console.warn(`[WS] No pong for ${silentFor}ms — backend appears dead, dropping socket`);
          this._emitStatus('stale');
          try { this._ws.close(); } catch { /* ignore */ }
        }
      }
    }, this._PING_INTERVAL);
  }

  _stopPing() {
    if (this._pingInterval) {
      clearInterval(this._pingInterval);
      this._pingInterval = null;
    }
  }
}
