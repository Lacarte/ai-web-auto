"""
SKOOL-DOWN-COURSE - Python Backend
====================================
REST API server that receives course data from the Chrome extension,
organizes it into chapter/course folder structure, downloads videos,
stitches screenshots, and generates a course tree file.

Run: python sdc_backend.py
Listens on http://localhost:9876
"""

import asyncio
import base64
import io
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from aiohttp import web, ClientSession, ClientTimeout
from loguru import logger

# Remove default stderr handler, add colored one
logger.remove()
logger.add(sys.stderr, level="DEBUG", colorize=True,
           format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | <cyan>{message}</cyan>")
LOG_DIR = os.path.dirname(os.path.abspath(__file__))
logger.add(os.path.join(LOG_DIR, "sdc_backend.log"), rotation="5 MB", level="DEBUG",
           format="{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {message}")

# ============================================================
# CONFIG
# ============================================================

HOST = '0.0.0.0'
PORT = 9876
OUTPUT_DIR = Path('./skool-output')

# ============================================================
# SESSION STATE
# ============================================================

session_state = {
    'sessionId': None,
    'courseName': '',
    'modules': [],
    'courses': [],
    'options': {},
    'nextDataMap': {},
    'total': 0,
    'completed': 0,
    'startFrom': 1,
    'currentCourse': '',
    'downloadLog': [],
    'outputDir': None,
}


def sanitize_filename(name):
    """Make a string safe for use as a file/folder name."""
    # Strip emojis and special Unicode symbols
    name = re.sub(r'[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE00-\uFE0F\u200D\u20E3\U000E0020-\U000E007F]', '', name)
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name[:100]


def get_output_dir():
    """Get or create the output directory for this session."""
    if session_state['outputDir']:
        return Path(session_state['outputDir'])

    course_name = sanitize_filename(session_state['courseName'] or 'SkoolCourse')
    date_str = datetime.now().strftime('%Y-%m-%d')
    out = OUTPUT_DIR / f"{course_name}_{date_str}"
    out.mkdir(parents=True, exist_ok=True)
    session_state['outputDir'] = str(out)
    return out


def get_course_dir(module_name, course_name, module_index=None, lesson_index=None):
    """Create chapter/course subfolder structure with numbered prefixes."""
    out = get_output_dir()
    safe_module = sanitize_filename(module_name)
    safe_course = sanitize_filename(course_name)
    if module_index is not None:
        safe_module = f"{module_index}- {safe_module}"
    if lesson_index is not None:
        safe_course = f"{lesson_index + 1}- {safe_course}"
    chapter_dir = out / safe_module
    course_dir = chapter_dir / safe_course
    course_dir.mkdir(parents=True, exist_ok=True)
    return course_dir


# ============================================================
# VIDEO DOWNLOAD
# ============================================================

async def download_video_m3u8(video_info, course_dir, course_name):
    """Download video from M3U8/HLS stream using ffmpeg or direct segments."""
    m3u8_url = video_info.get('m3u8Url') or video_info.get('stream') or ''
    video_url = video_info.get('url') or ''
    video_type = video_info.get('type', '')
    safe_name = sanitize_filename(course_name)
    output_path = course_dir / f"{safe_name}.mp4"

    if output_path.exists():
        logger.info(f"[Video] Already exists: {output_path}")
        return str(output_path)

    # Strategy 1: M3U8 via ffmpeg (Skool native, Loom)
    if m3u8_url and (m3u8_url.endswith('.m3u8') or 'm3u8' in m3u8_url):
        logger.info(f"[Video] Downloading M3U8: {m3u8_url[:80]}...")
        try:
            proc = await asyncio.create_subprocess_exec(
                'ffmpeg', '-y',
                '-headers', f'Referer: https://www.skool.com/\r\nOrigin: https://www.skool.com',
                '-i', m3u8_url,
                '-c', 'copy',
                '-bsf:a', 'aac_adtstoasc',
                str(output_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
            if proc.returncode == 0 and output_path.exists():
                size_mb = output_path.stat().st_size / (1024 * 1024)
                logger.success(f"[Video] Downloaded: {output_path} ({size_mb:.1f} MB)")
                return str(output_path)
            else:
                logger.error(f"[Video] ffmpeg failed: {stderr.decode()[:200]}")
        except asyncio.TimeoutError:
            logger.error(f"[Video] ffmpeg timeout for {m3u8_url}")
        except FileNotFoundError:
            logger.warning("[Video] ffmpeg not found in PATH - skipping M3U8 download")

    # Strategy 2: External video via yt-dlp (YouTube, Vimeo, Loom, Wistia)
    # Use embedUrl if available (direct iframe src), fall back to videoLink
    dl_url = video_info.get('embedUrl') or video_url
    if dl_url and video_type in ('YouTube', 'Vimeo', 'Loom', 'Wistia', 'Video'):
        logger.info(f"[Video] Trying yt-dlp for {video_type}: {dl_url[:80]}...")
        try:
            proc = await asyncio.create_subprocess_exec(
                'yt-dlp',
                '--no-warnings',
                '-o', str(output_path),
                '--merge-output-format', 'mp4',
                dl_url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
            if proc.returncode == 0 and output_path.exists():
                size_mb = output_path.stat().st_size / (1024 * 1024)
                logger.success(f"[Video] Downloaded: {output_path} ({size_mb:.1f} MB)")
                return str(output_path)
            else:
                logger.error(f"[Video] yt-dlp failed: {stderr.decode()[:200]}")
        except asyncio.TimeoutError:
            logger.error(f"[Video] yt-dlp timeout for {video_url}")
        except FileNotFoundError:
            logger.warning("[Video] yt-dlp not found in PATH - skipping video download")

    # Strategy 3: Construct Mux M3U8 from playback ID
    playback_id = video_info.get('playbackId')
    custom_domain = video_info.get('customDomain')
    if playback_id:
        domain = custom_domain or 'stream.mux.com'
        mux_url = f"https://{domain}/{playback_id}.m3u8"
        logger.info(f"[Video] Trying Mux stream: {mux_url}")
        try:
            proc = await asyncio.create_subprocess_exec(
                'ffmpeg', '-y',
                '-i', mux_url,
                '-c', 'copy',
                '-bsf:a', 'aac_adtstoasc',
                str(output_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
            if proc.returncode == 0 and output_path.exists():
                size_mb = output_path.stat().st_size / (1024 * 1024)
                logger.success(f"[Video] Downloaded: {output_path} ({size_mb:.1f} MB)")
                return str(output_path)
        except (asyncio.TimeoutError, FileNotFoundError):
            pass

    # Strategy 4: Direct download of video URL
    if video_url and video_url.startswith('http'):
        logger.info(f"[Video] Direct download: {video_url[:80]}...")
        try:
            timeout = ClientTimeout(total=300)
            async with ClientSession(timeout=timeout) as client:
                async with client.get(video_url, headers={
                    'Referer': 'https://www.skool.com/',
                    'Origin': 'https://www.skool.com'
                }) as resp:
                    if resp.status == 200:
                        with open(output_path, 'wb') as f:
                            async for chunk in resp.content.iter_chunked(65536):
                                f.write(chunk)
                        if output_path.exists() and output_path.stat().st_size > 1000:
                            size_mb = output_path.stat().st_size / (1024 * 1024)
                            logger.success(f"[Video] Downloaded: {output_path} ({size_mb:.1f} MB)")
                            return str(output_path)
        except Exception as e:
            logger.error(f"[Video] Direct download failed: {e}")

    logger.warning(f"[Video] Could not download video for: {course_name}")
    # Save video URL info for manual download
    info_path = course_dir / f"{safe_name}_video_url.txt"
    with open(info_path, 'w', encoding='utf-8') as f:
        f.write(f"Video URL: {video_url}\n")
        f.write(f"Stream URL: {m3u8_url}\n")
        f.write(f"Type: {video_type}\n")
        f.write(f"Playback ID: {playback_id or 'N/A'}\n")
    return None


async def download_resource(url, course_dir, filename):
    """Download a resource attachment."""
    if not url:
        return None
    safe_name = sanitize_filename(filename or 'resource')
    output_path = course_dir / safe_name
    if output_path.exists():
        return str(output_path)
    try:
        timeout = ClientTimeout(total=120)
        async with ClientSession(timeout=timeout) as client:
            async with client.get(url) as resp:
                if resp.status == 200:
                    with open(output_path, 'wb') as f:
                        async for chunk in resp.content.iter_chunked(65536):
                            f.write(chunk)
                    return str(output_path)
    except Exception as e:
        logger.error(f"[Resource] Download failed for {filename}: {e}")
    return None


# ============================================================
# SCREENSHOT STITCHING
# ============================================================

def stitch_screenshots_pillow(screenshots_b64, total_height, viewport_width, viewport_height):
    """Stitch multiple viewport screenshots into one tall image using Pillow."""
    try:
        from PIL import Image

        images = []
        for b64 in screenshots_b64:
            # Strip data URL prefix
            if ',' in b64:
                b64 = b64.split(',', 1)[1]
            img_data = base64.b64decode(b64)
            img = Image.open(io.BytesIO(img_data))
            images.append(img)

        if not images:
            return None

        # Calculate final dimensions
        width = images[0].width
        height = total_height if total_height else sum(img.height for img in images)

        # Create canvas
        canvas = Image.new('RGB', (width, height), (255, 255, 255))

        y_offset = 0
        for i, img in enumerate(images):
            # Last image may need cropping if it overlaps
            if i == len(images) - 1 and y_offset + img.height > height:
                crop_top = img.height - (height - y_offset)
                img = img.crop((0, crop_top, img.width, img.height))
            canvas.paste(img, (0, y_offset))
            y_offset += viewport_height if viewport_height else img.height

        # Save to base64
        buf = io.BytesIO()
        canvas.save(buf, format='PNG', optimize=True)
        b64_result = base64.b64encode(buf.getvalue()).decode('ascii')
        return f"data:image/png;base64,{b64_result}"
    except ImportError:
        logger.warning("[Screenshot] Pillow not available for stitching")
        return None
    except Exception as e:
        logger.error(f"[Screenshot] Stitch error: {e}")
        return None


# ============================================================
# TREE GENERATOR
# ============================================================

def generate_tree(output_dir):
    """Generate a tree structure text file of the output directory."""
    lines = []
    output_dir = Path(output_dir)

    def walk(path, prefix=''):
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        for i, entry in enumerate(entries):
            is_last = i == len(entries) - 1
            connector = '└── ' if is_last else '├── '
            if entry.is_file():
                size = entry.stat().st_size
                if size > 1024 * 1024:
                    size_str = f" ({size / (1024*1024):.1f} MB)"
                elif size > 1024:
                    size_str = f" ({size / 1024:.1f} KB)"
                else:
                    size_str = f" ({size} B)"
                lines.append(f"{prefix}{connector}{entry.name}{size_str}")
            else:
                lines.append(f"{prefix}{connector}{entry.name}/")
                extension = '    ' if is_last else '│   '
                walk(entry, prefix + extension)

    lines.append(f"{output_dir.name}/")
    walk(output_dir)
    return '\n'.join(lines)


# ============================================================
# API ROUTES
# ============================================================

async def handle_health(request):
    return web.json_response({'status': 'ok', 'version': '1.0.0'})


async def handle_init_session(request):
    """Initialize a download session with course structure."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({'success': False, 'error': 'Invalid JSON'}, status=400)

    session_id = f"sdc_{int(time.time())}"
    session_state.update({
        'sessionId': session_id,
        'courseName': data.get('courseName', 'SkoolCourse'),
        'modules': data.get('modules', []),
        'courses': data.get('courses', []),
        'options': data.get('options', {}),
        'nextDataMap': data.get('nextDataMap', {}),
        'total': len(data.get('courses', [])),
        'completed': 0,
        'startFrom': data.get('options', {}).get('startFrom', 1),
        'currentCourse': '',
        'downloadLog': [],
        'outputDir': None,
    })

    out = get_output_dir()

    # Save course menu list
    menu_lines = [
        f"=== {session_state['courseName']} - Course Menu ===",
        f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Total courses: {session_state['total']}",
        ''
    ]
    for module in session_state['modules']:
        menu_lines.append(f"\n## {module.get('title') or module.get('name', 'Unknown')}")
        for c in module.get('courses', []):
            menu_lines.append(f"  - {c.get('title') or c.get('name', 'Unknown')}")

    (out / 'menu-list.txt').write_text('\n'.join(menu_lines), encoding='utf-8')

    logger.info(f"\n{'='*60}")
    logger.info(f"Session: {session_id}")
    logger.info(f"Course: {session_state['courseName']}")
    logger.info(f"Modules: {len(session_state['modules'])}")
    logger.info(f"Courses: {session_state['total']}")
    logger.info(f"Output: {out}")
    logger.info(f"{'='*60}\n")

    return web.json_response({'success': True, 'sessionId': session_id, 'outputDir': str(out)})


async def handle_course_progress(request):
    """Track which course is currently being processed."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({'success': False}, status=400)

    session_state['currentCourse'] = data.get('courseName', '')
    status = data.get('status', '')
    lesson_count = data.get('lessonCount', 0)

    # When a module starts, add its lesson count to the total
    if status == 'module_start' and lesson_count > 0:
        session_state['total'] += lesson_count
        logger.info(f"[MODULE] {data.get('courseName', '?')} - {lesson_count} lessons (total now: {session_state['total']})")
    else:
        logger.info(f"[{status.upper()}] {data.get('courseName', '?')}")

    return web.json_response({'success': True})


async def handle_course_data(request):
    """Receive and save course data from the extension."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({'success': False, 'error': 'Invalid JSON'}, status=400)

    course_name = data.get('courseName', 'Unknown')
    module_name = data.get('moduleName', 'Unknown Module')
    course_index = data.get('courseIndex', 0)
    module_index = data.get('moduleIndex')
    lesson_index = data.get('lessonIndex')
    meta = data.get('meta', {})
    html = data.get('html')
    screenshot_data = data.get('screenshotDataUrl')
    video_info = data.get('videoInfo')

    logger.info(f"\n  Processing course {course_index + 1}: {course_name}")
    logger.info(f"Module: {module_name}")

    course_dir = get_course_dir(module_name, course_name, module_index, lesson_index)
    safe_name = sanitize_filename(course_name)
    log_entry = {
        'courseIndex': course_index,
        'courseName': course_name,
        'moduleName': module_name,
        'files': []
    }

    # 1. Save HTML
    if html:
        html_path = course_dir / f"{safe_name}.html"
        html_path.write_text(html, encoding='utf-8')
        log_entry['files'].append(str(html_path))
        logger.info(f"HTML: {html_path}")

    # 2. Save screenshot
    if screenshot_data:
        try:
            if ',' in screenshot_data:
                b64_data = screenshot_data.split(',', 1)[1]
            else:
                b64_data = screenshot_data
            img_bytes = base64.b64decode(b64_data)
            screenshot_path = course_dir / f"{safe_name}_screenshot.png"
            screenshot_path.write_bytes(img_bytes)
            log_entry['files'].append(str(screenshot_path))
            logger.info(f"Screenshot: {screenshot_path}")
        except Exception as e:
            logger.error(f"Screenshot save error: {e}")

    # 3. Download video
    if video_info:
        video_path = await download_video_m3u8(video_info, course_dir, course_name)
        if video_path:
            log_entry['files'].append(video_path)
            log_entry['videoFile'] = video_path
        log_entry['videoUrl'] = video_info.get('url', '')
        log_entry['videoType'] = video_info.get('type', '')

    # 4. Download resources/attachments
    resources = meta.get('resources', [])
    if resources:
        for res in resources:
            dl_url = res.get('downloadUrl')
            fname = res.get('fileName') or res.get('title', 'resource')
            if dl_url:
                res_path = await download_resource(dl_url, course_dir, fname)
                if res_path:
                    log_entry['files'].append(res_path)
                    logger.info(f"Resource: {res_path}")

    # 5. Save metadata JSON
    meta_path = course_dir / f"{safe_name}_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding='utf-8')

    # Update session state
    session_state['completed'] += 1
    session_state['downloadLog'].append(log_entry)
    session_state['currentCourse'] = course_name

    pct = round((session_state['completed'] / max(session_state['total'], 1)) * 100)
    logger.success(f"Done! ({session_state['completed']}/{session_state['total']} = {pct}%)")

    return web.json_response({'success': True, 'courseDir': str(course_dir)})


async def handle_progress(request):
    """Return current download progress."""
    return web.json_response({
        'sessionId': session_state['sessionId'],
        'total': session_state['total'],
        'completed': session_state['completed'],
        'startFrom': session_state['startFrom'],
        'currentCourse': session_state['currentCourse'],
    })


async def handle_stitch_screenshots(request):
    """Stitch multiple viewport screenshots into a single tall image."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({'success': False, 'error': 'Invalid JSON'}, status=400)

    screenshots = data.get('screenshots', [])
    total_height = data.get('totalHeight', 0)
    viewport_width = data.get('viewportWidth', 1920)
    viewport_height = data.get('viewportHeight', 1080)

    result = stitch_screenshots_pillow(screenshots, total_height, viewport_width, viewport_height)
    if result:
        return web.json_response({'success': True, 'dataUrl': result})
    return web.json_response({'success': False, 'error': 'Stitch failed'})


async def handle_download_complete(request):
    """Called when the extension finishes processing all courses."""
    out = get_output_dir()

    # Generate tree file
    tree_text = generate_tree(out)
    tree_path = out / 'course_tree.txt'
    tree_path.write_text(tree_text, encoding='utf-8')
    logger.info(f"\nTree file: {tree_path}")

    # Generate download manifest
    manifest_lines = [
        f"=== SKOOL DOWNLOAD MANIFEST ===",
        f"Course: {session_state['courseName']}",
        f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Downloaded: {session_state['completed']} / {session_state['total']} courses",
        f"Output: {out}",
        ''
    ]
    for entry in session_state['downloadLog']:
        manifest_lines.append(f"\n[{entry['courseIndex'] + 1}] {entry['courseName']}")
        manifest_lines.append(f"    Module: {entry['moduleName']}")
        for f in entry.get('files', []):
            manifest_lines.append(f"    > {Path(f).name}")
        if entry.get('videoUrl'):
            manifest_lines.append(f"    Video URL: {entry['videoUrl']}")
        if entry.get('videoType'):
            manifest_lines.append(f"    Video Type: {entry['videoType']}")

    manifest_path = out / 'download_manifest.txt'
    manifest_path.write_text('\n'.join(manifest_lines), encoding='utf-8')

    # Save full log JSON
    log_path = out / 'download_log.json'
    log_path.write_text(json.dumps(session_state['downloadLog'], indent=2, ensure_ascii=False), encoding='utf-8')

    logger.info(f"\n{'='*60}")
    logger.success(f"DOWNLOAD COMPLETE")
    logger.info(f"Courses: {session_state['completed']}/{session_state['total']}")
    logger.info(f"Output: {out}")
    logger.info(f"Tree: {tree_path}")
    logger.info(f"Manifest: {manifest_path}")
    logger.info(f"{'='*60}\n")

    # Print the tree
    logger.info(tree_text)

    return web.json_response({
        'success': True,
        'completed': session_state['completed'],
        'total': session_state['total'],
        'outputDir': str(out),
        'treeFile': str(tree_path)
    })


# ============================================================
# CORS MIDDLEWARE
# ============================================================

@web.middleware
async def cors_middleware(request, handler):
    if request.method == 'OPTIONS':
        response = web.Response(status=200)
    else:
        try:
            response = await handler(request)
        except web.HTTPException as ex:
            response = ex

    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response


# ============================================================
# MAIN
# ============================================================

def create_app():
    app = web.Application(
        middlewares=[cors_middleware],
        client_max_size=100 * 1024 * 1024  # 100MB max request (for screenshots)
    )

    app.router.add_get('/health', handle_health)
    app.router.add_post('/init-session', handle_init_session)
    app.router.add_post('/course-progress', handle_course_progress)
    app.router.add_post('/course-data', handle_course_data)
    app.router.add_get('/progress', handle_progress)
    app.router.add_post('/stitch-screenshots', handle_stitch_screenshots)
    app.router.add_post('/download-complete', handle_download_complete)

    return app


if __name__ == '__main__':
    logger.info(f"""
==============================================
  SKOOL-DOWN-COURSE - Python Backend
  Listening on http://localhost:{PORT}

  1. Load extension in Chrome
  2. Navigate to Skool classroom page
  3. Click Scan in extension popup
  4. Click Start Download

  Output: {str(OUTPUT_DIR)}
==============================================
""")
    app = create_app()
    web.run_app(app, host=HOST, port=PORT, print=None)
