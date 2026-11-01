// Keepalive: pings the service worker every 20s to reset its idle timer.
// Must live in its own file - MV3's default extension-page CSP (script-src 'self')
// blocks inline <script>, so this never ran while it was embedded in offscreen.html.
setInterval(function () {
  chrome.runtime.sendMessage({ action: 'keepalive' }).catch(function () {
    // Service worker is asleep or restarting; the next tick will reach it.
  });
}, 20000);
