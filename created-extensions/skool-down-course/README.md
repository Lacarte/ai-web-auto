# SKOOL-DOWN-COURSE

Download entire Skool.com classroom courses - videos, HTML pages, screenshots, and resources.

## Structure

```
skool-down-course/
  skool-down-course-extension/   # Chrome extension (popup UI)
  skool-down-course-backend/     # Python downloader + backend
    sdc_downloader.py            # Main downloader (uses AI Web-Auto)
    sdc_backend.py               # REST API backend (serves the extension)
    sdc_resume_downloads.py      # Verify + resume/repair a downloaded course
    install.bat                  # Create venv + install requirements.txt
    runner.bat                   # Start sdc_backend.py on localhost:9876
```

## Quick Start

### Prerequisites
- Python 3.10+ venv with `requirements.txt` installed (`install.bat`)
- `ffmpeg` and `ffprobe` in PATH (Skool native M3U8 videos, download verification)
- `yt-dlp` in PATH (for YouTube/Loom/Vimeo videos)
- AI Web-Auto extension loaded in Chrome (`ai-web-auto-extension/`)

### Download a course

1. Open Chrome with the AI Web-Auto extension loaded
2. Log into Skool and navigate to any classroom page
3. Run the downloader from the project root:

```
venv\Scripts\python created-extensions\skool-down-course\skool-down-course-backend\sdc_downloader.py "https://www.skool.com/ai-video-creators/classroom"
```

### Or drive it from the Chrome extension

`runner.bat` starts the REST backend that the SKOOL-DOWN-COURSE extension talks to
(it takes no arguments - the course is chosen in the popup):

```
cd created-extensions\skool-down-course\skool-down-course-backend
install.bat     :: first time only
runner.bat      :: serves http://localhost:9876
```

Then load `skool-down-course-extension/` in `chrome://extensions` and use the popup.

### Verify / resume an interrupted download

Long courses can be cut short by a dropped connection or a full disk. This probes every
`.mp4` with `ffprobe`, compares it against the expected duration in `_meta.json`, and
resumes or repairs whatever is short, corrupt, or missing:

```
:: report only - no deletes, no downloads
python sdc_resume_downloads.py "..\..\..\skool-output\MyCourse_2026-04-21" --dry-run

:: actually clean leftovers and resume
python sdc_resume_downloads.py "..\..\..\skool-output\MyCourse_2026-04-21"
```

Options: `--tolerance 0.95` (min actual/expected duration ratio to count as complete),
`--keep-parts` (never delete leftover `.part`/`.ytdl` files).
Skool-native (Mux) videos cannot be resumed by yt-dlp - they are reported and left
untouched, so re-run `sdc_downloader.py` for those.

### Output structure

```
skool-output/
  AI Video Creators Community_2026-04-03/
    menu-list.txt
    course_tree.txt
    STEP 0 // Start here/
      Welcome! Check this first/
        Welcome! Check this first.html      # Full page with inline CSS
        Welcome! Check this first.mp4       # Video download
        Welcome! Check this first_screenshot.png
        Welcome! Check this first_meta.json # Metadata
      How this Community works/
        ...
    Step 1 // Image Generation/
      1.1: Platforms overview/
        ...
```

## What it downloads per lesson

| Asset | Method | Format |
|-------|--------|--------|
| HTML page | Inline CSS + absolute URLs + `<base>` tag | Single `.html` file |
| Video (Skool native) | Click play -> mux-player -> M3U8 stream -> ffmpeg | `.mp4` |
| Video (YouTube) | `videoLink` from `__NEXT_DATA__` -> yt-dlp | `.mp4` |
| Video (Loom) | `videoLink` / iframe embed -> yt-dlp | `.mp4` |
| Video (Vimeo/Wistia) | `videoLink` -> yt-dlp | `.mp4` |
| Screenshot | Viewport capture via CDP | `.png` |
| Metadata | Lesson title, video URL, duration, resources | `_meta.json` |
| Resources (PDFs) | `assets.skool.com/f/{groupId}/{fileId}` | Original format |

## Supported Skool communities (tested)

| Community | Video types | Locked courses |
|-----------|-------------|----------------|
| ai-video-creators | Skool native (Mux) | 5 locked, 12 open |
| the-ai-blueprint | Loom embeds + PDFs | 0 locked, 7 open |
| learn-ai | Mixed Skool + YouTube | 0 locked, 8 open |

## How it works

1. **Scan**: Reads `__NEXT_DATA__` on the classroom page to get all modules from `pageProps.allCourses`
2. **Filter**: Skips locked modules (`!hasAccess && minAccessLevel != null`)
3. **Navigate**: Enters each module via URL (`/{group}/classroom/{slug}`) to discover lessons
4. **Extract**: For each lesson, navigates to it and extracts metadata, HTML, screenshot
5. **Video**: Detects video type from metadata, clicks play for Skool native to get authenticated M3U8
6. **Download**: Uses ffmpeg (M3U8) or yt-dlp (YouTube/Loom/Vimeo) to download
7. **Organize**: Saves everything in `{Module}/{Lesson}/` folder structure
8. **Tree**: Generates `course_tree.txt` with the full directory listing

## Key selectors (proven on live sites)

```
__NEXT_DATA__.props.pageProps.allCourses        # Module list on classroom page
__NEXT_DATA__.props.pageProps.course.children    # Lessons inside a module
metadata.hasAccess / metadata.minAccessLevel     # Lock detection
metadata.videoId                                 # Skool native video
metadata.videoLink                               # External video URL (YouTube/Loom/Vimeo)
mux-player[playback-id][custom-domain]           # Appears after clicking play
performance.getEntries() -> .m3u8                # Authenticated stream URL
[class*="VideoDuration"]                         # Play button location
[class*="ResourceWrapper"]                       # PDF/attachment elements
button > svg[viewBox="0 0 40 40"]                # Mark complete (green)
```
