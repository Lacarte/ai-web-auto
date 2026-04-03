/**
 * Routes messages between WebSocket, content scripts, and popup.
 */

export class MessageRouter {
  constructor(wsClient) {
    this.ws = wsClient;
    this._taskLog = [];
  }

  async handleContentMessage(payload, tab) {
    // Content script reporting an event
    if (payload.type === 'event') {
      this.ws.sendEvent(payload.event, { ...payload.data, tabId: tab?.id });
      return { ok: true };
    }
    // Content script returning a command result
    if (payload.type === 'response') {
      this.ws.sendResponse(payload.correlation_id, payload.status, payload.data, payload.error);
      return { ok: true };
    }
    return { ok: false, error: 'Unknown payload type' };
  }

  getStatus() {
    return {
      connected: this.ws.connected,
      taskLog: this._taskLog.slice(-50),
    };
  }

  logTask(entry) {
    this._taskLog.push({ ...entry, timestamp: new Date().toISOString() });
    if (this._taskLog.length > 200) this._taskLog.shift();
  }
}
