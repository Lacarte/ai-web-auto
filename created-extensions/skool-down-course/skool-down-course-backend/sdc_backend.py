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

# Find yt-dlp and ffmpeg - check PATH first, then known venv locations
def _find_tool(name):
    """Find executable by name, checking PATH and known venv locations."""
    import shutil
    found = shutil.which(name)
    if found:
        return found
    # Check project root venv and local backend venv
    backend_dir = Path(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        backend_dir / 'venv' / 'Scripts' / f'{name}.exe',
        backend_dir.parent.parent.parent / 'venv' / 'Scripts' / f'{name}.exe',
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return name  # fallback to bare name

YT_DLP = _find_tool('yt-dlp')
FFMPEG = _find_tool('ffmpeg')
logger.info(f"yt-dlp: {YT_DLP}")
logger.info(f"ffmpeg: {FFMPEG}")

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
        # Validate it's actually a video file, not HTML saved with .mp4 extension
        with open(output_path, 'rb') as f:
            header = f.read(20)
        if header.startswith(b'<!DOCTYPE') or header.startswith(b'<html') or header.startswith(b'<HTML'):
            logger.warning(f"[Video] Existing file is HTML, not video - re-downloading: {output_path}")
            output_path.unlink()
        else:
            logger.info(f"[Video] Already exists: {output_path}")
            return str(output_path)

    # Strategy 1: M3U8 via ffmpeg (Skool native, Loom)
    if m3u8_url and (m3u8_url.endswith('.m3u8') or 'm3u8' in m3u8_url):
        logger.info(f"[Video] Downloading M3U8: {m3u8_url[:80]}...")
        try:
            proc = await asyncio.create_subprocess_exec(
                FFMPEG, '-y',
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
                YT_DLP,
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
            logger.warning(f"[Video] yt-dlp not found at '{YT_DLP}' - run: pip install yt-dlp")

    # Strategy 3: Construct Mux M3U8 from playback ID
    playback_id = video_info.get('playbackId')
    custom_domain = video_info.get('customDomain')
    if playback_id:
        domain = custom_domain or 'stream.mux.com'
        mux_url = f"https://{domain}/{playback_id}.m3u8"
        logger.info(f"[Video] Trying Mux stream: {mux_url}")
        try:
            proc = await asyncio.create_subprocess_exec(
                FFMPEG, '-y',
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

    # Strategy 4: Direct download of video URL (only for actual video content)
    if video_url and video_url.startswith('http'):
        # Skip direct download for YouTube/Loom/Vimeo URLs - they return HTML pages, not video
        skip_direct = any(d in video_url.lower() for d in ['youtube.com', 'youtu.be', 'loom.com', 'vimeo.com', 'wistia.com'])
        if skip_direct:
            logger.warning(f"[Video] Skipping direct download for {video_type} URL (requires yt-dlp): {video_url[:80]}")
        else:
            logger.info(f"[Video] Direct download: {video_url[:80]}...")
            try:
                timeout = ClientTimeout(total=300)
                async with ClientSession(timeout=timeout) as client:
                    async with client.get(video_url, headers={
                        'Referer': 'https://www.skool.com/',
                        'Origin': 'https://www.skool.com'
                    }) as resp:
                        content_type = resp.headers.get('Content-Type', '')
                        if resp.status == 200 and 'text/html' not in content_type:
                            with open(output_path, 'wb') as f:
                                async for chunk in resp.content.iter_chunked(65536):
                                    f.write(chunk)
                            if output_path.exists() and output_path.stat().st_size > 1000:
                                size_mb = output_path.stat().st_size / (1024 * 1024)
                                logger.success(f"[Video] Downloaded: {output_path} ({size_mb:.1f} MB)")
                                return str(output_path)
                        elif 'text/html' in content_type:
                            logger.warning(f"[Video] Direct download returned HTML, not video - skipping")
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
# COMMUNITY POSTS
# ============================================================

def generate_community_html(posts, group_url):
    """Generate a single HTML file with all community posts and comments."""
    from html import escape

    html_parts = [f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Community Posts - {escape(group_url)}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; color: #333; line-height: 1.6; padding: 20px; max-width: 900px; margin: 0 auto; }}
  h1 {{ font-size: 24px; margin-bottom: 8px; color: #1a1a2e; }}
  .meta {{ color: #888; font-size: 13px; margin-bottom: 30px; }}
  .post {{ background: #fff; border-radius: 12px; padding: 24px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  .post-header {{ display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }}
  .post-header img {{ width: 40px; height: 40px; border-radius: 50%; object-fit: cover; }}
  .post-author {{ font-weight: 600; font-size: 14px; }}
  .post-date {{ font-size: 12px; color: #888; }}
  .post-title {{ font-size: 18px; font-weight: 700; margin-bottom: 8px; color: #1a1a2e; }}
  .post-content {{ white-space: pre-wrap; margin-bottom: 12px; font-size: 14px; }}
  .post-image {{ max-width: 100%; border-radius: 8px; margin-bottom: 12px; }}
  .post-stats {{ display: flex; gap: 16px; font-size: 12px; color: #888; padding-top: 8px; border-top: 1px solid #eee; }}
  .comments {{ margin-top: 16px; padding-top: 12px; border-top: 1px solid #eee; }}
  .comments-label {{ font-size: 13px; font-weight: 600; color: #666; margin-bottom: 10px; }}
  .comment {{ padding: 10px 0; border-bottom: 1px solid #f0f0f0; }}
  .comment:last-child {{ border-bottom: none; }}
  .comment-author {{ font-weight: 600; font-size: 13px; }}
  .comment-time {{ font-size: 11px; color: #aaa; margin-left: 8px; }}
  .comment-content {{ font-size: 13px; margin-top: 4px; white-space: pre-wrap; }}
  .comment-raw {{ font-size: 13px; margin-top: 8px; }}
  .comment-raw img {{ max-width: 100%; border-radius: 6px; }}
  .pinned {{ border-left: 3px solid #7c5cff; }}
  .no-comments {{ font-size: 12px; color: #aaa; font-style: italic; }}
  .post-number {{ font-size: 11px; color: #bbb; float: right; }}
</style>
</head>
<body>
<h1>Community Posts</h1>
<p class="meta">Source: {escape(group_url)} &middot; {len(posts)} posts &middot; Downloaded {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
"""]

    for i, post in enumerate(posts):
        pinned_cls = ' pinned' if post.get('pinned') else ''
        title = escape(post.get('title', ''))
        content = escape(post.get('content', ''))
        author = escape(post.get('authorName', 'Unknown'))
        date = post.get('createdAt', '')[:10]
        upvotes = post.get('upvotes', 0)
        comment_count = post.get('commentCount', 0)
        image = post.get('imagePreview', '')
        author_pic = post.get('authorPicture', '')

        html_parts.append(f'<div class="post{pinned_cls}">')
        html_parts.append(f'<span class="post-number">#{i+1}</span>')
        html_parts.append('<div class="post-header">')
        if author_pic:
            html_parts.append(f'<img src="{escape(author_pic)}" alt="">')
        html_parts.append(f'<div><div class="post-author">{author}</div><div class="post-date">{date}</div></div>')
        html_parts.append('</div>')

        if title:
            html_parts.append(f'<div class="post-title">{title}</div>')
        if content:
            html_parts.append(f'<div class="post-content">{content}</div>')
        if image:
            html_parts.append(f'<img class="post-image" src="{escape(image)}" loading="lazy">')

        html_parts.append(f'<div class="post-stats"><span>{upvotes} upvotes</span><span>{comment_count} comments</span></div>')

        # Comments
        comments = post.get('comments', [])
        raw_html = post.get('rawHtml', '')
        if comments:
            html_parts.append(f'<div class="comments"><div class="comments-label">Comments ({len(comments)})</div>')
            for c in comments:
                c_author = escape(c.get('author', ''))
                c_time = escape(c.get('time', ''))
                c_content = c.get('content', '')
                c_html = c.get('html', '')
                html_parts.append('<div class="comment">')
                html_parts.append(f'<span class="comment-author">{c_author}</span>')
                if c_time:
                    html_parts.append(f'<span class="comment-time">{c_time}</span>')
                if c_html and not c_content:
                    html_parts.append(f'<div class="comment-raw">{c_html}</div>')
                elif c_content:
                    html_parts.append(f'<div class="comment-content">{escape(c_content)}</div>')
                html_parts.append('</div>')
            html_parts.append('</div>')
        elif raw_html:
            html_parts.append(f'<div class="comments"><div class="comments-label">Comments</div>')
            html_parts.append(f'<div class="comment-raw">{raw_html}</div>')
            html_parts.append('</div>')
        elif comment_count == 0:
            html_parts.append('<div class="no-comments">No comments</div>')

        html_parts.append('</div>')

    html_parts.append('</body></html>')
    return '\n'.join(html_parts)


async def handle_community_data(request):
    """Receive community posts and generate HTML file."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({'success': False, 'error': 'Invalid JSON'}, status=400)

    posts = data.get('posts', [])
    group_url = data.get('groupUrl', '')

    if not posts:
        return web.json_response({'success': False, 'error': 'No posts'})

    out = get_output_dir()
    community_dir = out / 'community'
    community_dir.mkdir(parents=True, exist_ok=True)

    # Generate HTML
    html = generate_community_html(posts, group_url)
    html_path = community_dir / 'community_posts.html'
    html_path.write_text(html, encoding='utf-8')

    # Also save raw JSON for reference
    json_path = community_dir / 'community_posts.json'
    json_path.write_text(json.dumps(posts, indent=2, ensure_ascii=False), encoding='utf-8')

    logger.success(f"[Community] Saved {len(posts)} posts to {community_dir}")
    logger.info(f"  HTML: {html_path} ({len(html)//1024} KB)")

    return web.json_response({
        'success': True,
        'postCount': len(posts),
        'htmlPath': str(html_path)
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
    app.router.add_post('/community-data', handle_community_data)

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
