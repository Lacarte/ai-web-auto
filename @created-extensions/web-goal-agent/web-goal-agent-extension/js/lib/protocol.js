/** Constants shared by the service worker and popup. Must match goal_agent_backend/protocol.py. */

export const PROTOCOL_VERSION = 1;
export const CLIENT_NAME = 'web-goal-agent-extension';
export const EXTENSION_VERSION = chrome.runtime.getManifest().version;
export const NATIVE_HOST = 'com.aiwebauto.web_goal_agent';
export const DEFAULT_CONN = { host: '127.0.0.1', port: 8766, token: '' };

export const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 15000];
export const ENSURE_AFTER_FAILURES = 2;      // reconnect attempts before asking the native host to (re)start
export const HEARTBEAT_MS = 15000;
export const PONG_TIMEOUT_MS = 10000;
export const CONNECT_TIMEOUT_MS = 5000;
export const UI_REQUEST_TIMEOUT_MS = 20000;
export const KEEPALIVE_ALARM = 'wga-keepalive';

export const TERMINAL_STATES = new Set(['succeeded', 'failed', 'stopped']);

export function originOf(url) {
  try {
    const u = new URL(url);
    return (u.protocol === 'http:' || u.protocol === 'https:') ? u.origin.toLowerCase() : '';
  } catch {
    return '';
  }
}
