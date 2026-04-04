# Skool Course Downloader

**Date:** Pre-2026-04
**Status:** Completed
**Goal:** Download entire Skool.com classroom courses — videos, HTML pages, screenshots, and PDF resources — organized by module and lesson.

## What it does
A Chrome extension + Python downloader that bulk-downloads Skool courses:
1. Scans `__NEXT_DATA__` on the classroom page to get all modules
2. Filters out locked modules (`!hasAccess && minAccessLevel != null`)
3. Navigates into each module to discover lessons
4. For each lesson: captures HTML (inline CSS, active links), screenshot, and video
5. Downloads videos: Skool native (Mux M3U8 via ffmpeg), YouTube/Loom/Vimeo (yt-dlp)
6. Extracts PDF/attachment resources
7. Organizes output as `skool-output/{CourseName}/{Module}/{Lesson}/`

## Website automated
Skool.com — any community classroom (tested on ai-video-creators, the-ai-blueprint, learn-ai)

## Architecture
- **Chrome extension** (skool-down-course-extension/) — popup UI for triggering downloads
- **Python downloader** (sdc_downloader.py) — main automation script, connects to AI Web-Auto via WebSocket
- **Python backend** (sdc_backend.py) — optional REST API for the extension
- Uses the AI Web-Auto learning extension as the CDP engine (same tab navigation)

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

## Tested communities
| Community | Video types | Notes |
|-----------|-------------|-------|
| ai-video-creators | Skool native (Mux M3U8) | 5 locked, 12 open modules |
| the-ai-blueprint | Loom embeds + PDFs | 7 open modules |
| learn-ai | Mixed Skool native + YouTube | 8 open modules |

## Files
```
skool-down-course/
  skool-down-course-extension/
    manifest.json
    service-worker.js
    content.js
    popup.js
  skool-down-course-backend/
    sdc_downloader.py      # Main downloader (proven, reliable)
    sdc_backend.py          # REST API backend
    download-runner.bat
  README.md
```
