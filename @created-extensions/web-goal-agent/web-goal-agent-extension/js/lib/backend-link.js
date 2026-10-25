/**
 * Link to the Python backend: native-host lifecycle + WebSocket session.
 *
 * Startup: ask the native host to `ensure` the backend (it detects a running
 * instance, otherwise starts one and waits for /health), which returns the
 * port and per-install token; then open the WebSocket and authenticate with
 * protocol-version negotiation (same handshake as ai-web-auto).
 *
 * Failure handling: heartbeat ping/pong detects a dead peer; reconnects back
 * off (1s..15s); after a couple of failed attempts the native host is asked
 * to ensure the backend again, which restarts it if it died. A backend that
 * announced `shutting_down` (idle timeout, user stop) is not restarted until
 * something needs it.
 */

import {
  CLIENT_NAME, CONNECT_TIMEOUT_MS, DEFAULT_CONN, ENSURE_AFTER_FAILURES, EXTENSION_VERSION, HEARTBEAT_MS,
  NATIVE_HOST, PONG_TIMEOUT_MS, PROTOCOL_VERSION, RECONNECT_DELAYS_MS, UI_REQUEST_TIMEOUT_MS,
} from './protocol.js';

const CONN_KEY = 'wga.conn';
const MANUAL_KEY = 'wga.manualConn';

export class BackendLink {
  constructor({ onCommand, onEvent, onState }) {
    this.onCommand = onCommand;
    this.onEvent = onEvent;
    this.onState = onState;
    this.ws = null;
    this.state = {
      status: 'idle', detail: 'Not connected yet', since: Date.now(),
      backend: null, native: { available: null, error: null, last: null }, lastError: null,
      reconnectAttempt: 0,
    };
    this.wanted = true;
    this._failures = 0;
    this._reconnectTimer = null;
    this._heartbeat = null;
    this._lastPong = 0;
    this._starting = null;
    this._pending = new Map();
    this._shutdownAnnounced = null;
    this._intentionalClose = false;
  }

  get connected() { return this.state.status === 'connected'; }

  _set(patch) {
    if (patch.status && patch.status !== this.state.status) patch.since = Date.now();
    this.state = { ...this.state, ...patch };
    this.onState?.(this.state);
  }

  // ── Native host ─────────────────────────────────────────────────────────

  async native(cmd, extra = {}) {
    try {
      const reply = await chrome.runtime.sendNativeMessage(NATIVE_HOST, { cmd, client_version: EXTENSION_VERSION, ...extra });
      this._set({ native: { available: true, error: null, last: { cmd, ok: reply?.ok, status: reply?.status, at: Date.now() } } });
      return reply || { ok: false, error: 'empty reply from native host' };
    } catch (e) {
      const msg = String(e.message || e);
      const missing = /not found|forbidden/i.test(msg);
      this._set({ native: { available: !missing, error: missing ? 'Native host not installed (run install.bat)' : msg, last: { cmd, ok: false, at: Date.now() } } });
      return { ok: false, error: missing ? 'native host not installed — run install.bat in the web-goal-agent folder' : msg, native_error: true };
    }
  }

  async _conn() {
    const s = await chrome.storage.local.get([CONN_KEY, MANUAL_KEY]);
    return s[MANUAL_KEY]?.token ? { ...DEFAULT_CONN, ...s[MANUAL_KEY], manual: true } : (s[CONN_KEY] || null);
  }

  async ensureBackend(reason = 'start') {
    this._set({ status: 'starting', detail: 'Starting the backend via the native host…' });
    const reply = await this.native('ensure');
    if (reply.ok) {
      const conn = { host: reply.host, port: reply.port, token: reply.token };
      await chrome.storage.local.set({ [CONN_KEY]: conn });
      this._set({ backend: { ...(this.state.backend || {}), ...reply.backend }, detail: reply.status === 'started'
        ? `Backend started (pid ${reply.backend?.pid}, ${reply.startup_ms} ms)` : 'Backend already running' });
      return { ok: true, conn, started: reply.status === 'started', reason };
    }
    return { ok: false, error: reply.error || 'native host could not start the backend', log_tail: reply.log_tail, native_error: reply.native_error };
  }

  /** Idempotent: ensure the backend (if allowed) and connect. */
  start(reason = 'start') {
    this.wanted = true;
    if (this.connected) return Promise.resolve(true);
    if (this._starting) return this._starting;
    this._starting = this._startInner(reason).finally(() => { this._starting = null; });
    return this._starting;
  }

  async _startInner(reason) {
    clearTimeout(this._reconnectTimer);
    const { autoStart = true } = (await chrome.storage.local.get('wga.settings'))['wga.settings'] || {};
    let conn = await this._conn();
    if (!conn?.manual && autoStart) {
      const ensured = await this.ensureBackend(reason);
      if (ensured.ok) conn = ensured.conn;
      else if (!conn) {
        this._set({ status: 'error', detail: ensured.error, lastError: { message: ensured.error, log_tail: ensured.log_tail, at: Date.now() } });
        this._scheduleReconnect();
        return false;
      }
    }
    if (!conn) {
      this._set({ status: 'error', detail: 'No backend connection details. Install the native host (install.bat) or enter port and token in Settings.' });
      return false;
    }
    const ok = await this._connect(conn);
    if (!ok) this._scheduleReconnect();
    return ok;
  }

  _connect(conn) {
    return new Promise((resolve) => {
      this._closeSocket();
      this._set({ status: 'connecting', detail: `Connecting to ws://${conn.host}:${conn.port}…` });
      let settled = false;
      const done = (ok) => { if (!settled) { settled = true; clearTimeout(timer); resolve(ok); } };
      let ws;
      try {
        ws = new WebSocket(`ws://${conn.host}:${conn.port}`);
      } catch (e) {
        this._set({ status: 'disconnected', detail: `WebSocket error: ${e.message}` });
        done(false);
        return;
      }
      this.ws = ws;
      this._intentionalClose = false;
      this._shutdownAnnounced = null;
      const timer = setTimeout(() => { if (!settled) { try { ws.close(); } catch { /* */ } done(false); } }, CONNECT_TIMEOUT_MS);

      ws.onopen = () => {
        ws.send(JSON.stringify({ type: 'auth', token: conn.token, client: CLIENT_NAME, version: EXTENSION_VERSION, protocol: PROTOCOL_VERSION }));
      };
      ws.onmessage = (ev) => {
        let msg;
        try { msg = JSON.parse(ev.data); } catch { return; }
        if (msg.type === 'auth_response') {
          if (msg.status === 'accepted') {
            this._failures = 0;
            this._lastPong = Date.now();
            this._set({ status: 'connected', reconnectAttempt: 0, lastError: null,
              detail: `Connected to backend v${msg.backend_version}`,
              backend: { ...(this.state.backend || {}), version: msg.backend_version, protocol: msg.protocol, pid: msg.pid,
                started_by: msg.started_by, capabilities: msg.capabilities, session_id: msg.session_id } });
            this._startHeartbeat();
            done(true);
            this.onEvent?.('connected', msg);
          } else {
            const reason = msg.reason || 'denied';
            const detail = reason === 'protocol_mismatch'
              ? `Version mismatch: extension protocol ${PROTOCOL_VERSION}, backend protocol ${msg.protocol} (backend v${msg.backend_version}). Update and reload both.`
              : reason === 'bad_token' ? 'The backend rejected the token. Reinstall the native host or update the token in Settings.' : `Authentication denied (${reason})`;
            this._set({ status: 'error', detail, lastError: { message: detail, at: Date.now() } });
            this._intentionalClose = true;
            if (reason === 'bad_token' && !conn.manual) chrome.storage.local.remove(CONN_KEY);
            done(false);
          }
          return;
        }
        this._lastPong = Date.now();
        if (msg.type === 'pong') return;
        if (msg.type === 'command') { this.onCommand?.(msg); return; }
        if (msg.type === 'event') this._onBackendEvent(msg.event, msg.data || {});
      };
      ws.onclose = (ev) => {
        done(false);
        if (this.ws !== ws) return; // a replaced socket closing late must not touch the live one
        const wasConnected = this.state.status === 'connected';
        this.ws = null;
        this._stopHeartbeat();
        this._failPending('backend connection closed');
        if (this._intentionalClose) return;
        if (this._shutdownAnnounced) {
          this._set({ status: 'idle', detail: `Backend stopped (${this._shutdownAnnounced}). It restarts when needed.` });
          return;
        }
        if (wasConnected) {
          this._set({ status: 'disconnected', detail: `Connection lost (code ${ev.code}) — reconnecting`, lastError: { message: 'backend connection lost unexpectedly', at: Date.now() } });
          this.onEvent?.('disconnected', { code: ev.code });
          this._scheduleReconnect();
        }
      };
      ws.onerror = () => { /* onclose follows */ };
    });
  }

  _onBackendEvent(event, data) {
    if (event === 'ui_reply') {
      const p = this._pending.get(data.request_id);
      if (p) {
        this._pending.delete(data.request_id);
        clearTimeout(p.timer);
        data.ok ? p.resolve(data.result) : p.reject(new Error(data.error || 'request failed'));
      }
      return;
    }
    if (event === 'shutting_down') {
      this._shutdownAnnounced = data.reason || 'shutdown';
    }
    this.onEvent?.(event, data);
  }

  _scheduleReconnect() {
    if (!this.wanted || this._shutdownAnnounced) return;
    clearTimeout(this._reconnectTimer);
    const attempt = this._failures++;
    const delay = RECONNECT_DELAYS_MS[Math.min(attempt, RECONNECT_DELAYS_MS.length - 1)];
    this._set({ reconnectAttempt: attempt + 1 });
    this._reconnectTimer = setTimeout(async () => {
      if (this.connected || this._starting) return;
      const conn = await this._conn();
      if (conn && attempt + 1 < ENSURE_AFTER_FAILURES) {
        const ok = await this._connect(conn);
        if (!ok) this._scheduleReconnect();
      } else {
        // Repeated failures: let the native host check the process and restart it if it died.
        await this.start('reconnect');
      }
    }, delay);
  }

  _startHeartbeat() {
    this._stopHeartbeat();
    this._heartbeat = setInterval(() => {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
      if (Date.now() - this._lastPong > HEARTBEAT_MS + PONG_TIMEOUT_MS) {
        this._set({ detail: 'Backend stopped answering heartbeats — reconnecting' });
        try { this.ws.close(4000, 'heartbeat timeout'); } catch { /* */ }
        return;
      }
      this.ws.send(JSON.stringify({ type: 'ping', ts: Date.now() }));
    }, HEARTBEAT_MS);
  }

  _stopHeartbeat() { clearInterval(this._heartbeat); this._heartbeat = null; }

  _closeSocket() {
    if (this.ws) {
      this._intentionalClose = true;
      try { this.ws.close(1000, 'reconnecting'); } catch { /* */ }
      this.ws = null;
      this._stopHeartbeat();
      this._failPending('connection replaced');
      // Status must follow the socket, or start() would short-circuit on a dead link.
      if (this.state.status === 'connected') this._set({ status: 'disconnected', detail: 'Reconnecting…' });
    }
  }

  _failPending(reason) {
    for (const [id, p] of this._pending) { clearTimeout(p.timer); p.reject(new Error(reason)); this._pending.delete(id); }
  }

  // ── User actions ────────────────────────────────────────────────────────

  async reconnect() {
    this._closeSocket();
    this._failures = 0;
    return this.start('user-reconnect');
  }

  async restartBackend() {
    this._set({ status: 'starting', detail: 'Restarting the backend…' });
    this._closeSocket();
    const reply = await this.native('restart');
    if (!reply.ok) {
      this._set({ status: 'error', detail: reply.error || 'restart failed', lastError: { message: reply.error, log_tail: reply.log_tail, at: Date.now() } });
      return reply;
    }
    await chrome.storage.local.set({ [CONN_KEY]: { host: reply.host, port: reply.port, token: reply.token } });
    await this._connect({ host: reply.host, port: reply.port, token: reply.token });
    return reply;
  }

  async stopBackend() {
    this.wanted = false;
    clearTimeout(this._reconnectTimer);
    const reply = await this.native('stop');
    if (reply.ok) {
      this._closeSocket();
      this._set({ status: 'idle', detail: 'Backend stopped by you. Start it from the popup when needed.' });
    } else {
      this.wanted = true;
      this._set({ lastError: { message: reply.error, at: Date.now() } });
    }
    return reply;
  }

  // ── Messaging ───────────────────────────────────────────────────────────

  _send(obj) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return false;
    this.ws.send(JSON.stringify(obj));
    return true;
  }

  respond(correlationId, status, extracted = {}, error = null, screenshot = null) {
    const data = { extracted_data: extracted };
    if (screenshot) data.screenshot = screenshot;
    return this._send({ id: crypto.randomUUID(), correlation_id: correlationId, timestamp: new Date().toISOString(),
      type: 'response', status, data, error });
  }

  sendEvent(event, data) {
    return this._send({ id: crypto.randomUUID(), timestamp: new Date().toISOString(), type: 'event', event, data });
  }

  /** Request/reply over the event channel (ui_request -> ui_reply), with a timeout. */
  async request(op, args = {}, timeoutMs = UI_REQUEST_TIMEOUT_MS) {
    if (!this.connected) await this.start('request');
    if (!this.connected) throw new Error(this.state.detail || 'backend not connected');
    const requestId = crypto.randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this._pending.delete(requestId); reject(new Error(`backend did not answer '${op}' within ${timeoutMs / 1000}s`)); }, timeoutMs);
      this._pending.set(requestId, { resolve, reject, timer });
      if (!this.sendEvent('ui_request', { request_id: requestId, op, args })) {
        clearTimeout(timer); this._pending.delete(requestId); reject(new Error('backend not connected'));
      }
    });
  }
}
