# Meta AI Image Generator Extension

**Date:** Pre-2026-04
**Status:** Completed
**Goal:** Automate Meta AI image generation — select aspect ratio, type prompt, generate 4 images, and auto-download them.

## What it does
A standalone Chrome extension that automates image generation on meta.ai:
1. Clicks "Create image" tab
2. Selects aspect ratio via Radix Select dropdown
3. Types prompt into contentEditable div
4. Clicks Send
5. Polls for generation completion (download buttons appearing)
6. Extracts image URLs from fbcdn CDN and downloads all 4 images

## Website automated
Meta AI — https://www.meta.ai/

## Architecture
- **Service worker** (background.js) — CDP automation, image URL extraction, download
- **Popup** (popup.html/popup.js) — UI for prompt and ratio selection
- Single debugger.attach/detach lifecycle

## Key technical challenges solved

### Hidden Download Buttons (Tailwind CSS)
Meta AI's download buttons have `opacity-0` and `pointer-events-none` by default (Tailwind `group-hover`). CDP `mouseMoved` does NOT trigger Tailwind group-hover. Clicking hidden buttons via CDP or JS `.click()` doesn't fire Meta's React download handler.

**Solution:** Skip buttons entirely. Extract `<img>` src URLs directly from the DOM (fbcdn.net CDN, filtered by `naturalWidth > 200`) and use `chrome.downloads.download()`.

### Radix Select Dropdown (Dynamic IDs)
The ratio dropdown uses Radix UI Select with dynamic IDs (`radix-_r_4f_`, etc). Never match by ID.

**Solution:** Match options by `[role="option"]` + `textContent.trim()`. Click the trigger button (shows current ratio text) to open, then find the matching option.

### ContentEditable vs Hidden Textarea
Meta AI has a visible contentEditable div AND a hidden textarea (width=0). The hidden one is for accessibility — targeting it breaks input.

**Solution:** Always target `//div[@contenteditable="true"]`, verify `width > 0` and `height > 0`.

### CSP Blocks eval
Meta AI has strict CSP. `new Function()` and `eval()` are blocked.

**Solution:** Use `chrome.scripting.executeScript` with real function refs in MAIN world.

## Proven selectors
| Element | Selector |
|---------|----------|
| Create image tab | `//button[contains(.,"Create image")]` |
| Ratio trigger | `//button[contains(.,"9:16") or contains(.,"1:1") or ...]` |
| Ratio options | `[role="option"]` matched by textContent |
| Prompt input | `//div[@contenteditable="true"]` (NOT hidden textarea) |
| Send button | `//button[@aria-label="Send"]` |
| Generation done | `button[aria-label="Download"]` count > 0 |
| Image URLs | `img[naturalWidth>200]` from fbcdn.net |

## Files
```
meta-ai-extension/
  manifest.json
  js/background.js      # CDP automation + image download
  js/popup.js            # Popup UI logic
  html/popup.html
  css/popup.css
```
