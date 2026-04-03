# Meta AI Image Generator

Chrome extension that automates [Meta AI](https://www.meta.ai/) image generation: select aspect ratio, type a prompt, wait for generation, and download all images.

## Quick Start

1. Open `chrome://extensions` in Chrome
2. Enable **Developer mode** (top right)
3. Click **Load unpacked** and select this folder (`meta-ai-extension/`)
4. Open [https://www.meta.ai/](https://www.meta.ai/) and make sure you're logged in
5. Click the extension icon, type a prompt, and click **Generate**

## Features

- Select aspect ratio: **1:1**, **9:16** (default), **16:9**
- Type any image prompt
- Auto-waits for generation (polls for download buttons, no fixed timers)
- Auto-downloads all 4 generated images via direct URL extraction

## How It Works

```
Extension popup  -->  background.js (service worker)
                          |
                     CDP automation on meta.ai tab
                          |
                     1. Click "Create image" tab
                     2. Select ratio (Radix Select dropdown)
                     3. Type prompt (contentEditable div)
                     4. Click Send
                     5. Poll for generation completion
                     6. Extract img src URLs from fbcdn CDN
                     7. chrome.downloads.download() each image
```

## Proven Selectors

| Element | Selector | Notes |
|---------|----------|-------|
| Create image tab | `//button[contains(.,"Create image")]` | XPath, bottom of input area |
| Ratio trigger | `//button[contains(.,"9:16") or ...]` | Shows current ratio text |
| Ratio options | `[role="option"]` matched by `textContent` | Radix Select, dynamic IDs |
| Prompt input | `//div[@contenteditable="true"]` | ProseMirror div, NOT the hidden textarea |
| Send | `//button[@aria-label="Send"]` | |
| Generation done | `button[aria-label="Download"]` count > 0 | |
| Image URLs | `img[naturalWidth>200]` from `fbcdn.net` | Direct CDN links, 810x1440 for 9:16 |

## Key Technical Details

- **Input** is a `contentEditable` div (`#prompt-textarea` equivalent). A hidden `<textarea>` exists with `width=0` — do not target it.
- **CSP blocks `eval`** — uses `chrome.scripting.executeScript` with real function refs (MAIN world).
- **Download buttons** have `opacity-0` + `pointer-events-none` by default (Tailwind CSS). Clicking them via CDP or JS `.click()` doesn't trigger Meta AI's React download handler. Instead, the extension **extracts `<img>` src URLs** directly from the DOM and uses `chrome.downloads.download()`.
- **Ratio dropdown** uses Radix UI Select with dynamic IDs. Options are matched by `[role="option"]` + `textContent`, never by ID.
- **4 images** are generated per prompt.

## File Structure

```
meta-ai-extension/
├── manifest.json          # Chrome extension manifest (MV3)
├── js/
│   ├── background.js      # CDP automation + image download
│   └── popup.js           # Popup UI logic
├── html/
│   └── popup.html         # Popup: prompt input, ratio select, generate button
└── css/
    └── popup.css           # Popup styling
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Nothing happens on Generate | Make sure meta.ai is open and logged in |
| Wrong ratio selected | Extension clicks the Radix dropdown — check if UI changed |
| Images not downloading | Check `chrome://downloads` — files are saved via downloads API |
| Extension not loading | Check for errors in `chrome://extensions`, reload |
| "Cannot find prompt input" | meta.ai may have updated their DOM — re-inspect with the learning extension |
