# Flow Automator

Chrome extension that automates [Google Labs Flow (ImageFX)](https://labs.google/fx/tools/flow/) for image and video generation with configurable settings.

## Quick Start

1. Open `chrome://extensions` in Chrome
2. Enable **Developer mode** (top right)
3. Click **Load unpacked** and select this folder (`flow-extension/`)
4. Open [https://labs.google/fx/tools/flow/](https://labs.google/fx/tools/flow/) and sign in with your Google account
5. Click the extension icon, type a prompt, configure settings, and click **Generate**

## Features

- **Mode**: Image or Video
- **Aspect ratio**: 16:9, 4:3, 1:1, 3:4, 9:16 (default)
- **Count**: 1–4 images per generation (default 2)
- **Auto-download**: automatically saves generated images
- Handles landing page banners and new project creation

## How It Works

```
Extension popup  -->  background.js (service worker)
                          |
                     CDP automation on Flow tab
                          |
                     1. Find or open Flow tab
                     2. Dismiss banners / create new project
                     3. Type prompt (contentEditable input)
                     4. Open settings panel
                     5. Select mode (Image/Video)
                     6. Select ratio (Radix UI tabs)
                     7. Select count (1-4)
                     8. Close settings, click Create
                     9. Poll for generated images
                    10. Auto-download via image URLs
```

## Proven Selectors

| Element | Selector | Notes |
|---------|----------|-------|
| Prompt input | `/html/body/div[1]/div[1]/div[5]/div/div/div[1]/div` | XPath, contentEditable div |
| Mode tabs | `[role="tab"][id$="-trigger-IMAGE"]` / `-VIDEO` | Radix UI tab triggers |
| Ratio tabs | `[role="tab"][id$="-trigger-PORTRAIT"]` etc. | LANDSCAPE, SQUARE, PORTRAIT variants |
| Count tabs | `[role="tab"][id$="-trigger-1"]` through `-4` | |
| Settings panel | XPath to settings button | Opens on click, closes on Escape |
| Create button | Button containing `arrow_forward` + `Create` text | |
| Generated images | `img[width>100]` with `media.getMediaUrlRedirect` src | |

## Popup Options

| Setting | Options | Default |
|---------|---------|---------|
| Mode | Image, Video | Image |
| Ratio | 16:9, 4:3, 1:1, 3:4, 9:16 | 9:16 |
| Count | x1, x2, x3, x4 | x2 |
| Auto-download | on/off | on |

## Key Technical Details

- **Settings panel** uses Radix UI tabs with `[id$="-trigger-VALUE"]` selectors and `data-state="active"` verification.
- **CDP click** is required for all interactions (trusted events needed for Radix UI).
- **Single `debugger.attach`** for the entire generation flow, detached before polling for images.
- **Landing page handling**: automatically dismisses promotional banners and clicks "New project" if no prompt input is visible.
- **Image polling**: checks for `img` elements with `media.getMediaUrlRedirect` URLs every 3 seconds, up to 180 seconds.

## File Structure

```
flow-extension/
├── manifest.json          # Chrome extension manifest (MV3)
├── js/
│   ├── background.js      # CDP automation + settings + download
│   └── popup.js           # Popup UI logic with button groups
├── html/
│   └── popup.html         # Popup: prompt, mode, ratio, count, auto-download
└── css/
    └── popup.css           # Popup styling with button group toggles
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Nothing happens | Make sure you're signed into Google on `labs.google/fx` |
| "Finding Flow tab..." stalls | Open the Flow URL manually first, then retry |
| Settings not applying | Settings panel might not have opened — check if Radix tabs are visible |
| No images detected (timeout) | Generation can take 30-120s for video; increase patience or check the tab |
| Downloads not starting | Check `chrome://downloads` — images download via `conflictAction: uniquify` |
| Service worker dies | The keepalive alarm (every 15s) should prevent this; reload extension |
| Extension icon missing | Re-load in `chrome://extensions` |
