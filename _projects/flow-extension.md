# Flow Automator Extension

**Date:** Pre-2026-04
**Status:** Completed
**Goal:** Automate Google Labs Flow (ImageFX) — type a prompt, configure settings (mode, ratio, count), generate images/videos, and auto-download results.

## What it does
A standalone Chrome extension that automates the full image/video generation workflow on Google Labs Flow (labs.google/fx/tools/flow/):
1. Finds or opens a Flow tab
2. Dismisses landing page banners and creates a new project
3. Types a prompt into the contentEditable input
4. Opens the settings panel (Radix UI)
5. Selects mode (Image/Video), aspect ratio, and count
6. Clicks Create and polls for generated images
7. Auto-downloads results via direct URL extraction

## Website automated
Google Labs Flow (ImageFX) — https://labs.google/fx/tools/flow/

## Architecture
- **Service worker** (background.js) — CDP automation engine, settings management, image download
- **Popup** (popup.html/popup.js) — UI for prompt, mode, ratio, count, auto-download toggle
- Single `debugger.attach` for the entire generation flow, detached before polling

## Key technical patterns

### Radix UI Tabs
Flow uses Radix UI for its settings panel. Tabs are selected using `[role="tab"][id$="-trigger-VALUE"]` selectors with `data-state="active"` verification. CDP trusted clicks are required — `.click()` doesn't work on Radix.

### Centralized Selector Map
All selectors stored in a single `SELECTORS` object at the top of background.js for easy maintenance:
```
promptInput: XPath to contentEditable div
mode: {Image: '[role="tab"][id$="-trigger-IMAGE"]', Video: ...}
ratio: {16:9: '-trigger-LANDSCAPE', 9:16: '-trigger-PORTRAIT', ...}
count: {1: '-trigger-1', 2: '-trigger-2', ...}
```

### Image Polling
After clicking Create, polls every 3 seconds (up to 180s) for `img` elements with `media.getMediaUrlRedirect` URLs. Downloads via `chrome.downloads.download()` with `conflictAction: 'uniquify'`.

### CDP Keepalive
Uses `chrome.alarms.create('keepalive', {periodInMinutes: 0.25})` to prevent MV3 service worker from dying.

## Proven selectors
| Element | Selector |
|---------|----------|
| Prompt input | `/html/body/div[1]/div[1]/div[5]/div/div/div[1]/div` (XPath, contentEditable) |
| Mode tabs | `[role="tab"][id$="-trigger-IMAGE"]` / `-VIDEO` |
| Ratio tabs | `[role="tab"][id$="-trigger-PORTRAIT"]` etc. |
| Count tabs | `[role="tab"][id$="-trigger-1"]` through `-4` |
| Create button | Button containing `arrow_forward` + `Create` text |
| Generated images | `img[width>100]` with `media.getMediaUrlRedirect` src |

## Files
```
flow-extension/
  manifest.json
  js/background.js      # CDP automation + settings + download
  js/popup.js            # Popup UI logic
  html/popup.html
  css/popup.css
```
