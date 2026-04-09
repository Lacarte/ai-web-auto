# Skool Course Downloader

**Date:** Pre-2026-04, updated 2026-04-05
**Status:** Completed
**Goal:** Download entire Skool.com classroom courses — videos, HTML pages, screenshots, and PDF resources — organized by module and lesson.

## What it does
A Chrome extension + Python downloader that bulk-downloads Skool courses:
1. Scans `__NEXT_DATA__` on the classroom page to get all modules (or single module from module page URL)
2. Filters out locked modules (`!hasAccess && minAccessLevel != null`)
3. Navigates into each module to discover lessons
4. For each lesson: captures HTML (inline CSS, active links), screenshot, and video
5. Downloads videos: Skool native (Mux M3U8 via ffmpeg), YouTube/Loom/Vimeo (yt-dlp)
6. Extracts PDF/attachment resources
7. Organizes output as `skool-output/{CourseName}/{N- Module}/{N- Lesson}/` with numbered prefixes

## Website automated
Skool.com — any community classroom (tested on ai-video-creators, the-ai-blueprint, learn-ai)

## Architecture
- **Chrome extension** (skool-down-course-extension/) — popup UI for scan/download, service worker orchestrates navigation + extraction
- **Python backend** (sdc_backend.py) — REST API that receives data from extension, saves files, downloads videos
- **Python downloader** (sdc_downloader.py) — standalone script, connects to AI Web-Auto via WebSocket (alternative to extension flow)
- Extension service worker kept alive during downloads via offscreen document + alarms keepalive
- Page is reloaded before scan to ensure fresh `__NEXT_DATA__`

### Two download paths
1. **Extension flow:** Popup scan → service worker navigates/extracts → POST to backend → backend saves files + videos
2. **Script flow:** `sdc_downloader.py` via AI Web-Auto WebSocket → same navigation/extraction but all in Python

## Key technical challenges solved

### Skool Native Video (Mux)
Skool hosts videos via Mux player. The M3U8 stream URL is authenticated and only available after clicking play.

**Solution:** Click the play button (found via `[class*="VideoDuration"]` parent), wait for `mux-player[playback-id]` to appear, then extract the M3U8 URL from `performance.getEntries()`. Download with ffmpeg.

### Locked Course Detection
Some courses are locked behind level requirements.

**Solution:** Check `hasAccess` and `minAccessLevel` from `__NEXT_DATA__.props.pageProps.allCourses`. Skip modules where `!hasAccess && minAccessLevel != null`.

### Multiple Video Providers
Lessons can have Skool native, YouTube, Loom, or Vimeo videos.

**Solution:** Check `metadata.videoId` (Skool native) and `metadata.videoLink` (external). For external videos, use yt-dlp. For Skool native, use the click-play-extract-M3U8-ffmpeg pipeline.

### Full HTML Capture
Lessons need to be saved as self-contained HTML files.

**Solution:** Capture full page HTML with inline CSS (computed styles), convert relative URLs to absolute, add `<base>` tag. Single `.html` file per lesson.

### Stale __NEXT_DATA__ after client-side navigation
Next.js keeps the `<script id="__NEXT_DATA__">` tag from SSR but doesn't update it on client-side route changes. Navigating between classroom root and module pages via Skool's UI leaves stale data.

**Solution:** Reload the page before scanning to force fresh SSR. On module pages, also check `window.__NEXT_DATA__` runtime object which Next.js router keeps in sync.

### MV3 Service Worker termination
Chrome kills MV3 service workers after ~30s of inactivity. The download loop gets terminated mid-process.

**Solution:** Create an offscreen document (`offscreen.html`) that pings the service worker every 20s via `chrome.runtime.sendMessage`. Combined with `chrome.alarms` keepalive every 15s.

### Emoji filenames on Windows
Lesson titles with emojis (e.g. "📌 Sprint Setup") cause file creation errors on Windows.

**Solution:** Strip emojis from all `sanitize` functions using Unicode range regex `[\U0001F000-\U0001FFFF\u2600-\u27BF...]`.

## Proven selectors
| Element | Selector |
|---------|----------|
| Module list | `__NEXT_DATA__.props.pageProps.allCourses` |
| Lesson list | `__NEXT_DATA__.props.pageProps.course.children` |
| Lock detection | `hasAccess` + `minAccessLevel` |
| Skool native video | `mux-player[playback-id][custom-domain]` |
| External video | `metadata.videoLink` |
| Play button | `[class*="VideoDuration"]` parent |
| M3U8 URL | `performance.getEntries()` filtered by `.m3u8` |
| Resources | `[class*="ResourceWrapper"]` |
| Mark complete | `button > svg[viewBox="0 0 40 40"]` |

## Numbered folder output
Modules get `{index}- ` prefix (starting from 0), lessons get `{index}- ` prefix (starting from 1), restarting per module:
```
skool-output/CourseName_2026-04-05/
  0- AI Influencer challenge/
    1- Lesson 1. AI Character + Motion Control/
    2- Important rule/
    3- Sprint Setup & Rules/
  1- STEP 0 Start here/
    1- Welcome! Check this first/
    2- How this Community works/
```

## Tested communities
| Community | Video types | Notes |
|-----------|-------------|-------|
| ai-video-creators | Skool native (Mux M3U8) | 3 locked, 14 open modules, 84 lessons |
| the-ai-blueprint | Loom embeds + PDFs | 7 open modules |
| learn-ai | Mixed Skool native + YouTube | 8 open modules |

## Files
```
skool-down-course/
  skool-down-course-extension/
    manifest.json
    service-worker.js       # Download orchestrator, CDP click, offscreen keepalive
    content.js
    popup.js                # Scan + UI, page reload before scan
    offscreen.html          # Keepalive for service worker
  skool-down-course-backend/
    sdc_downloader.py       # Standalone downloader via WebSocket
    sdc_backend.py          # REST API backend for extension flow
    runner.bat              # Start backend server
  README.md
```
