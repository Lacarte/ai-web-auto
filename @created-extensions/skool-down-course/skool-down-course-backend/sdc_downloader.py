"""
SKOOL-DOWN-COURSE Downloader
Uses the AI Web-Auto WebSocket server to navigate, extract, and download.
This is the PROVEN approach - same method as all successful test scripts.

Usage:
  python sdc_downloader.py https://www.skool.com/ai-video-creators/classroom
"""
import asyncio
import json
import sys
import os
import base64
import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from ai_web_auto_backend.core.server import WebSocketServer
from ai_web_auto_backend.core.models import Command

from loguru import logger

logger.remove()
logger.add(sys.stderr, level="DEBUG", colorize=True,
           format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | <cyan>{message}</cyan>")
logger.add("sdc_downloader.log", rotation="5 MB", level="DEBUG",
           format="{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {message}")

OUTPUT_DIR = Path('skool-output')

# Pause downloads when free space on the output disk drops below this.
# Override with env var SDC_MIN_FREE_GB (float, in GB).
try:
    MIN_FREE_BYTES = int(float(os.environ.get('SDC_MIN_FREE_GB', '1')) * 1024 * 1024 * 1024)
except ValueError:
    MIN_FREE_BYTES = 1024 * 1024 * 1024
DISK_POLL_SECONDS = 5


def _disk_free_bytes(path):
    try:
        target = Path(path)
        while not target.exists() and target.parent != target:
            target = target.parent
        return shutil.disk_usage(str(target)).free
    except Exception as e:
        logger.error(f"[Disk] Check failed for {path}: {e}")
        return 0


async def ensure_disk_space(path, label=''):
    """Block until free space at path is >= MIN_FREE_BYTES."""
    warned = False
    while True:
        free = _disk_free_bytes(path)
        if free >= MIN_FREE_BYTES:
            if warned:
                logger.success(f"[Disk] Space recovered: {free / (1024**3):.2f} GB free - resuming")
            return
        if not warned:
            logger.warning(
                f"[Disk] LOW DISK SPACE: only {free / (1024**3):.2f} GB free at '{path}' "
                f"(need {MIN_FREE_BYTES / (1024**3):.2f} GB). "
                f"Pausing{' before ' + label if label else ''} - free up space to continue."
            )
            warned = True
        await asyncio.sleep(DISK_POLL_SECONDS)


# Retry + failure logging
MAX_DOWNLOAD_ATTEMPTS = 3
FAILED_LOG_NAME = 'failed-to-download.md'


def log_failed_download(out_dir, kind, filename, url, module, course, reason='', extras=None):
    """Append a failure entry to failed-to-download.md in the output dir."""
    try:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / FAILED_LOG_NAME
        new_file = not path.exists()
        with open(path, 'a', encoding='utf-8') as f:
            if new_file:
                f.write('# Failed Downloads\n\n')
                f.write(f'_Files that could not be downloaded after {MAX_DOWNLOAD_ATTEMPTS} '
                        f'retry attempts._\n\n')
            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            f.write(f'## {ts} &mdash; {kind}: {filename}\n\n')
            f.write(f'- **Module / Chapter:** {module or "(unknown)"}\n')
            f.write(f'- **Course / Lesson:** {course or "(unknown)"}\n')
            f.write(f'- **URL:** {url or "(none)"}\n')
            if extras:
                for k, v in extras.items():
                    if v:
                        f.write(f'- **{k}:** {v}\n')
            if reason:
                f.write(f'- **Reason:** {reason}\n')
            f.write('\n')
        logger.error(f"[{kind}] FAILED after {MAX_DOWNLOAD_ATTEMPTS} attempts: '{filename}' "
                     f"(module='{module}', course='{course}') - logged to {FAILED_LOG_NAME}")
    except Exception as e:
        logger.error(f"[Failed-log] Could not write {FAILED_LOG_NAME}: {e}")

# Find yt-dlp and ffmpeg
def _find_tool(name):
    import shutil
    found = shutil.which(name)
    if found:
        return found
    backend_dir = Path(os.path.dirname(os.path.abspath(__file__)))
    for d in [backend_dir / 'venv', backend_dir.parent.parent.parent / 'venv']:
        p = d / 'Scripts' / f'{name}.exe'
        if p.exists():
            return str(p)
    return name

YT_DLP = _find_tool('yt-dlp')
FFMPEG = _find_tool('ffmpeg')


def sanitize(name):
    """Fold accents (o-acute -> o) + strip non-ASCII and illegal filename chars."""
    if not name:
        return ''
    name = unicodedata.normalize('NFKD', name)
    name = name.encode('ascii', 'ignore').decode('ascii')
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    return re.sub(r'\s+', ' ', name).strip().rstrip('. ')[:100]


async def run_js(s, script):
    """Execute JS in the page and return the result."""
    resp = await s.execute_script(script)
    if resp and resp.data and resp.data.extracted_data:
        r = resp.data.extracted_data.get("result", resp.data.extracted_data)
        if isinstance(r, str):
            try:
                return json.loads(r)
            except (json.JSONDecodeError, TypeError):
                return r
        return r
    return None


async def save_screenshot(s, path):
    resp = await s.screenshot()
    if resp.data.screenshot:
        img = resp.data.screenshot
        if img.startswith("data:"):
            img = img.split(",", 1)[1]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(base64.b64decode(img))
        return True
    return False


async def download_video(video_info, output_path, out_dir=None, module_name='', course_name=''):
    """Download video using ffmpeg or yt-dlp with retry + failure logging."""
    output_path = Path(output_path)
    if output_path.exists() and output_path.stat().st_size > 1000:
        # Validate it's actually a video file, not HTML saved with .mp4 extension
        with open(output_path, 'rb') as f:
            header = f.read(20)
        if header.startswith(b'<!DOCTYPE') or header.startswith(b'<html') or header.startswith(b'<HTML'):
            logger.warning(f"Existing file is HTML, not video - re-downloading: {output_path}")
            output_path.unlink()
        else:
            logger.info(f"Video already exists: {output_path}")
            return str(output_path)

    await ensure_disk_space(output_path.parent, label=f"video '{output_path.name}'")

    last_reason = 'unknown'
    for attempt in range(1, MAX_DOWNLOAD_ATTEMPTS + 1):
        try:
            result = await _download_video_once(video_info, output_path, attempt)
            if result:
                return result
            last_reason = 'no strategy produced a valid video file'
        except Exception as e:
            last_reason = f'{type(e).__name__}: {e}'
            logger.error(f"Video attempt {attempt}/{MAX_DOWNLOAD_ATTEMPTS} raised: {e}")
        if attempt < MAX_DOWNLOAD_ATTEMPTS:
            backoff = 2 * attempt
            logger.warning(f"Attempt {attempt}/{MAX_DOWNLOAD_ATTEMPTS} failed for "
                           f"{output_path.name} - retrying in {backoff}s...")
            await asyncio.sleep(backoff)

    # All attempts exhausted - log to failed-to-download.md
    log_dir = out_dir if out_dir else output_path.parent
    src_url = (video_info.get('url') or video_info.get('m3u8Url')
               or video_info.get('stream') or video_info.get('embedUrl') or '')
    log_failed_download(
        log_dir, kind='Video', filename=output_path.name, url=src_url,
        module=module_name, course=course_name or output_path.stem,
        reason=last_reason,
        extras={'Type': video_info.get('type', ''), 'Playback ID': video_info.get('playbackId', '')},
    )
    return None


async def _download_video_once(video_info, output_path, attempt):
    """Single attempt: try each strategy in sequence."""
    m3u8 = video_info.get('m3u8Url') or video_info.get('stream') or ''
    url = video_info.get('url') or ''
    vtype = video_info.get('type') or ''
    embed = video_info.get('embedUrl') or ''

    # Strategy 1: M3U8 via ffmpeg (Skool native)
    if m3u8 and '.m3u8' in m3u8:
        logger.info(f"Downloading M3U8 via ffmpeg: {m3u8[:80]}...")
        try:
            proc = await asyncio.create_subprocess_exec(
                FFMPEG, '-y',
                '-headers', 'Referer: https://www.skool.com/\r\nOrigin: https://www.skool.com\r\n',
                '-i', m3u8, '-c', 'copy', '-bsf:a', 'aac_adtstoasc',
                str(output_path),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
            if proc.returncode == 0 and output_path.exists():
                mb = output_path.stat().st_size / (1024*1024)
                logger.success(f"Video downloaded: {mb:.1f} MB -> {output_path}")
                return str(output_path)
            logger.error(f"ffmpeg failed: {stderr.decode()[-300:]}")
        except FileNotFoundError:
            logger.warning("ffmpeg not in PATH")
        except asyncio.TimeoutError:
            logger.error("ffmpeg timeout")

    # Strategy 2: yt-dlp for external videos
    dl_url = embed or url
    if dl_url and vtype in ('YouTube', 'Loom', 'Vimeo', 'Wistia', 'Video'):
        if '/embed/' in dl_url:
            dl_url = dl_url.split('?')[0].replace('/embed/', '/share/')
        logger.info(f"Downloading via yt-dlp (attempt {attempt}): {dl_url[:80]}...")
        try:
            proc = await asyncio.create_subprocess_exec(
                YT_DLP,
                '--no-warnings',
                '--continue',                       # resume from .part + .ytdl state
                '--retries', '10',                  # whole-download retries
                '--fragment-retries', '20',         # per-fragment retries (HLS)
                '--retry-sleep', 'fragment:exp=1:10',
                '--socket-timeout', '30',
                '--concurrent-fragments', '4',
                '-o', str(output_path),
                '--merge-output-format', 'mp4',
                dl_url,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=1800)
            if proc.returncode == 0 and output_path.exists():
                mb = output_path.stat().st_size / (1024*1024)
                logger.success(f"Video downloaded: {mb:.1f} MB -> {output_path}")
                return str(output_path)
            logger.error(f"yt-dlp failed (attempt {attempt}): {stderr.decode()[-500:]}")
        except FileNotFoundError:
            logger.warning("yt-dlp not in PATH")
        except asyncio.TimeoutError:
            logger.error(f"yt-dlp timeout after 1800s (attempt {attempt}) - next retry will resume")
            try:
                proc.kill()
                await proc.communicate()
            except Exception:
                pass

    # Save URL info (overwritten each attempt) so user can download manually
    info_path = output_path.with_suffix('.video_url.txt')
    info_path.write_text(f"URL: {url}\nStream: {m3u8}\nType: {vtype}\nEmbed: {embed}\n")
    return None


async def download_resource(url, output_path, out_dir=None, module_name='', course_name=''):
    """Download a resource file via aiohttp with retry + failure logging."""
    output_path = Path(output_path)
    if output_path.exists():
        return str(output_path)
    await ensure_disk_space(output_path.parent, label=f"resource '{output_path.name}'")

    from aiohttp import ClientSession, ClientTimeout
    last_reason = 'unknown'
    for attempt in range(1, MAX_DOWNLOAD_ATTEMPTS + 1):
        try:
            async with ClientSession(timeout=ClientTimeout(total=120)) as client:
                async with client.get(url, headers={
                    'Referer': 'https://www.skool.com/',
                    'Origin': 'https://www.skool.com'
                }) as resp:
                    if resp.status == 200:
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        output_path.write_bytes(await resp.read())
                        return str(output_path)
                    last_reason = f'HTTP {resp.status}'
                    logger.error(f"Resource HTTP {resp.status} for {output_path.name} "
                                 f"(attempt {attempt}/{MAX_DOWNLOAD_ATTEMPTS})")
        except Exception as e:
            last_reason = f'{type(e).__name__}: {e}'
            logger.error(f"Resource attempt {attempt}/{MAX_DOWNLOAD_ATTEMPTS} failed: {e}")
        if attempt < MAX_DOWNLOAD_ATTEMPTS:
            await asyncio.sleep(2 * attempt)

    log_dir = out_dir if out_dir else output_path.parent
    log_failed_download(
        log_dir, kind='Resource', filename=output_path.name, url=url,
        module=module_name, course=course_name, reason=last_reason,
    )
    return None


def generate_tree(output_dir):
    lines = []
    output_dir = Path(output_dir)
    def walk(path, prefix=''):
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        for i, entry in enumerate(entries):
            is_last = i == len(entries) - 1
            connector = '+-- ' if is_last else '|-- '
            if entry.is_file():
                size = entry.stat().st_size
                s = f" ({size/(1024*1024):.1f}MB)" if size > 1024*1024 else f" ({size/1024:.0f}KB)"
                lines.append(f"{prefix}{connector}{entry.name}{s}")
            else:
                lines.append(f"{prefix}{connector}{entry.name}/")
                walk(entry, prefix + ('    ' if is_last else '|   '))
    lines.append(f"{output_dir.name}/")
    walk(output_dir)
    return '\n'.join(lines)


# ============================================================
# MAIN DOWNLOAD FLOW
# ============================================================

async def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.skool.com/ai-video-creators/classroom"

    s = WebSocketServer()
    await s.start()
    logger.info("Waiting for Chrome extension (AI Web-Auto) to connect...")
    await s.wait_for_connection(timeout=60)
    logger.info("Connected!")

    # ---- STEP 1: Navigate to classroom ----
    logger.info(f"Navigating to: {url}")
    await s.navigate(url)
    await asyncio.sleep(5)

    # ---- STEP 2: Scan modules from __NEXT_DATA__ ----
    logger.info("Scanning __NEXT_DATA__...")
    scan = await run_js(s, """
    (function() {
        var el = document.getElementById('__NEXT_DATA__');
        if (!el) return JSON.stringify({error: 'no __NEXT_DATA__'});
        var nd = JSON.parse(el.textContent);
        var pp = nd.props && nd.props.pageProps;
        if (!pp) return JSON.stringify({error: 'no pageProps'});
        var group = pp.currentGroup ? pp.currentGroup.name : '';
        var groupId = pp.currentGroup ? pp.currentGroup.id : '';
        var pathParts = window.location.pathname.split('/').filter(Boolean);
        var isModulePage = pathParts.length >= 3 && pathParts[1] === 'classroom' && pathParts[2];

        var modules = [];
        if (isModulePage && pp.course && pp.course.children) {
            // On a module page - single module entry
            var cm = pp.course.metadata || {};
            modules.push({
                id: pp.course.id || pathParts[2],
                slug: pp.course.name || pathParts[2],
                title: cm.title || pp.course.name || pathParts[2],
                url: '/' + group + '/classroom/' + (pp.course.name || pathParts[2])
            });
        } else {
            // On classroom root - scan all modules
            var all = pp.allCourses || [];
            for (var i = 0; i < all.length; i++) {
                var c = all[i];
                var meta = c.metadata || {};
                if (!meta.hasAccess && meta.minAccessLevel != null) continue;
                modules.push({
                    id: c.id, slug: c.name, title: meta.title || c.name,
                    url: '/' + group + '/classroom/' + c.name
                });
            }
        }
        var titleParts = document.title.split(' \\u00b7 ');
        var siteName = (pp.currentGroup && pp.currentGroup.metadata && pp.currentGroup.metadata.name) ||
                       (titleParts.length > 1 ? titleParts[titleParts.length - 1] : titleParts[0]) ||
                       'SkoolCourse';
        return JSON.stringify({group: group, groupId: groupId, modules: modules, siteName: siteName, isModulePage: !!isModulePage});
    })()
    """)

    if not scan or scan.get('error'):
        logger.error(f"Scan failed: {scan}")
        await s.stop()
        return

    modules = scan['modules']
    group_name = scan['group']
    group_id = scan['groupId']
    site_name = sanitize(scan['siteName'])
    is_module_page = scan.get('isModulePage', False)

    if not modules:
        logger.error("No accessible modules found")
        await s.stop()
        return

    logger.info(f"Found {len(modules)} module(s) for '{site_name}' (group={group_name}, modulePage={is_module_page})")

    # Create output dir
    date_str = datetime.now().strftime('%Y-%m-%d')
    out_dir = OUTPUT_DIR / f"{site_name}_{date_str}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Save menu list
    menu = [f"=== {site_name} ===", f"Date: {date_str}", f"Modules: {len(modules)}", ""]
    for i, m in enumerate(modules):
        menu.append(f"{i+1}. {m['title']}")
    (out_dir / 'menu-list.txt').write_text('\n'.join(menu), encoding='utf-8')

    # ---- STEP 3: Process each module ----
    total_lessons = 0
    total_downloaded = 0

    for mi, mod in enumerate(modules):
        logger.info(f"\n{'='*50}")
        logger.info(f"Module {mi+1}/{len(modules)}: {mod['title']}")
        logger.info(f"{'='*50}")

        mod_url = f"https://www.skool.com{mod['url']}"
        current_base = url.split('?')[0].rstrip('/')
        target_base = mod_url.split('?')[0].rstrip('/')
        if current_base != target_base or mi > 0:
            logger.info(f"Navigating to: {mod_url}")
            await s.navigate(mod_url)
            await asyncio.sleep(5)
        else:
            logger.info("Already on module page, skipping navigation")

        # Extract lessons
        lessons = await run_js(s, """
        (function() {
            var el = document.getElementById('__NEXT_DATA__');
            if (!el) return JSON.stringify([]);
            var nd = JSON.parse(el.textContent);
            var pp = nd.props && nd.props.pageProps;
            if (!pp || !pp.course || !pp.course.children) return JSON.stringify([]);
            var slug = '""" + mod['slug'] + """';
            var gName = '""" + group_name + """';
            var lessons = [];
            var children = pp.course.children;
            for (var i = 0; i < children.length; i++) {
                var child = children[i];
                if (!child.course) continue;
                var meta = child.course.metadata || {};
                if (child.children && child.children.length > 0) {
                    for (var j = 0; j < child.children.length; j++) {
                        var sub = child.children[j];
                        if (!sub.course) continue;
                        var sm = sub.course.metadata || {};
                        if (sm.hasAccess === false) continue;
                        lessons.push({
                            id: sub.course.id, name: sm.title || sub.course.name,
                            href: '/' + gName + '/classroom/' + slug + '?md=' + sub.course.id,
                            videoLink: sm.videoLink || null, videoId: sm.videoId || null,
                            videoStream: sm.videoStream || null, videoLenMs: sm.videoLenMs || null,
                            hasResources: !!sm.resources, resources: sm.resources || null
                        });
                    }
                } else {
                    if (meta.hasAccess === false) continue;
                    lessons.push({
                        id: child.course.id, name: meta.title || child.course.name,
                        href: '/' + gName + '/classroom/' + slug + '?md=' + child.course.id,
                        videoLink: meta.videoLink || null, videoId: meta.videoId || null,
                        videoStream: meta.videoStream || null, videoLenMs: meta.videoLenMs || null,
                        hasResources: !!meta.resources, resources: meta.resources || null
                    });
                }
            }
            return JSON.stringify(lessons);
        })()
        """)

        if not lessons:
            logger.info(f"No lessons in {mod['title']}, skipping")
            continue

        logger.info(f"Found {len(lessons)} lessons")
        total_lessons += len(lessons)

        # Process each lesson
        for li, lesson in enumerate(lessons):
            logger.info(f"\n  Lesson {li+1}/{len(lessons)}: {lesson['name']}")

            mod_dir = out_dir / f"{mi}- {sanitize(mod['title'])}"
            lesson_dir = mod_dir / f"{li + 1}- {sanitize(lesson['name'])}"
            lesson_dir.mkdir(parents=True, exist_ok=True)
            safe_name = sanitize(lesson['name'])

            # Navigate to lesson
            lesson_url = f"https://www.skool.com{lesson['href']}"
            await s.navigate(lesson_url)
            await asyncio.sleep(4)

            # Screenshot
            ss_path = lesson_dir / f"{safe_name}_screenshot.png"
            if await save_screenshot(s, str(ss_path)):
                logger.info(f"    Screenshot: {ss_path.name}")

            # HTML capture
            html_result = await run_js(s, """
            (function() {
                var clone = document.documentElement.cloneNode(true);
                clone.querySelectorAll('script').forEach(function(s) { s.remove(); });
                var allCSS = '';
                try {
                    var sheets = document.styleSheets;
                    for (var i = 0; i < sheets.length; i++) {
                        try {
                            var rules = sheets[i].cssRules;
                            for (var j = 0; j < rules.length; j++) allCSS += rules[j].cssText + '\\n';
                        } catch(e) {}
                    }
                } catch(e) {}
                document.querySelectorAll('style').forEach(function(t) { allCSS += t.textContent + '\\n'; });
                clone.querySelectorAll('link[rel="stylesheet"]').forEach(function(l) { l.remove(); });
                var style = document.createElement('style');
                style.textContent = allCSS;
                var head = clone.querySelector('head');
                if (head) head.appendChild(style);
                var base = document.createElement('base');
                base.href = location.origin;
                if (head) head.insertBefore(base, head.firstChild);
                clone.querySelectorAll('a[href]').forEach(function(a) {
                    var h = a.getAttribute('href');
                    if (h && h.charAt(0) === '/') {
                        try { a.setAttribute('href', new URL(h, location.href).href); } catch(e) {}
                    }
                });
                clone.querySelectorAll('img').forEach(function(img) {
                    var src = img.getAttribute('src');
                    if (src && src.charAt(0) === '/') {
                        try { img.setAttribute('src', new URL(src, location.href).href); } catch(e) {}
                    }
                });
                return '<!DOCTYPE html>\\n<html lang="en">' + clone.innerHTML + '</html>';
            })()
            """)
            if html_result and isinstance(html_result, str) and html_result.startswith('<!DOCTYPE'):
                html_path = lesson_dir / f"{safe_name}.html"
                html_path.write_text(html_result, encoding='utf-8')
                logger.info(f"    HTML: {html_path.name} ({len(html_result)//1024}KB)")

            # Video
            video_info = {}
            video_link = lesson.get('videoLink') or ''
            video_id = lesson.get('videoId') or ''

            if video_link:
                vl = video_link.lower()
                vtype = 'YouTube' if 'youtube' in vl or 'youtu.be' in vl else \
                        'Loom' if 'loom' in vl else \
                        'Vimeo' if 'vimeo' in vl else \
                        'Wistia' if 'wistia' in vl else 'Video'
                video_info = {'url': video_link, 'type': vtype, 'title': lesson['name']}

                # Get embed URL from iframe
                iframe_src = await run_js(s, """
                (function() {
                    var iframes = document.querySelectorAll('iframe');
                    for (var i = 0; i < iframes.length; i++) {
                        var src = iframes[i].src || '';
                        if (src.indexOf('youtube') !== -1 || src.indexOf('loom') !== -1 ||
                            src.indexOf('vimeo') !== -1 || src.indexOf('wistia') !== -1) return src;
                    }
                    return '';
                })()
                """)
                if iframe_src:
                    video_info['embedUrl'] = iframe_src

            elif video_id:
                # Skool native video - click play to get M3U8
                logger.info(f"    Skool native video - clicking play...")
                play_pos = await run_js(s, """
                (function() {
                    var dur = document.querySelector('[class*="VideoDuration"]');
                    if (dur) {
                        var el = dur;
                        for (var i = 0; i < 10; i++) {
                            el = el.parentElement;
                            if (!el) break;
                            var r = el.getBoundingClientRect();
                            if (r.width > 300 && r.height > 150)
                                return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
                        }
                    }
                    var cover = document.querySelector('[class*="CoverImage"]');
                    if (cover) {
                        var r = cover.getBoundingClientRect();
                        return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
                    }
                    return JSON.stringify(null);
                })()
                """)

                if play_pos and play_pos.get('x'):
                    await s.click_cdp(play_pos['x'], play_pos['y'])
                    await asyncio.sleep(4)

                    # Poll for M3U8
                    for attempt in range(12):
                        await asyncio.sleep(2)
                        mux = await run_js(s, """
                        (function() {
                            var mux = document.querySelector('mux-player');
                            if (!mux) return JSON.stringify(null);
                            var urls = [];
                            var entries = performance.getEntries();
                            for (var i = 0; i < entries.length; i++) {
                                if (entries[i].name.indexOf('.m3u8') !== -1) urls.push(entries[i].name);
                            }
                            return JSON.stringify({
                                playbackId: mux.getAttribute('playback-id'),
                                customDomain: mux.getAttribute('custom-domain') || 'video.skool.com',
                                m3u8Url: urls.length > 0 ? urls[0] : ''
                            });
                        })()
                        """)
                        if mux and mux.get('m3u8Url'):
                            video_info = {
                                'type': 'Skool', 'title': lesson['name'],
                                'm3u8Url': mux['m3u8Url'], 'stream': mux['m3u8Url'],
                                'playbackId': mux['playbackId']
                            }
                            logger.info(f"    M3U8 captured: {mux['m3u8Url'][:80]}...")
                            break
                        if mux and mux.get('playbackId'):
                            m3u8 = f"https://{mux['customDomain']}/{mux['playbackId']}.m3u8"
                            video_info = {
                                'type': 'Skool', 'title': lesson['name'],
                                'm3u8Url': m3u8, 'stream': m3u8,
                                'playbackId': mux['playbackId']
                            }
                            break

            if video_info:
                vid_path = lesson_dir / f"{safe_name}.mp4"
                result = await download_video(video_info, str(vid_path),
                                              out_dir=out_dir,
                                              module_name=mod['title'],
                                              course_name=lesson['name'])
                if result:
                    total_downloaded += 1

            # Resources
            if lesson.get('resources'):
                try:
                    res_list = lesson['resources']
                    if isinstance(res_list, str):
                        res_list = json.loads(res_list)
                    if isinstance(res_list, list):
                        for r in res_list:
                            file_id = r.get('file_id')
                            fname = r.get('file_name') or r.get('title', 'resource')
                            if file_id and group_id:
                                dl_url = f"https://assets.skool.com/f/{group_id}/{file_id}"
                                res_path = lesson_dir / sanitize(fname)
                                result = await download_resource(dl_url, str(res_path),
                                                                 out_dir=out_dir,
                                                                 module_name=mod['title'],
                                                                 course_name=lesson['name'])
                                if result:
                                    logger.info(f"    Resource: {fname}")
                except Exception as e:
                    logger.error(f"    Resource error: {e}")

            # Save metadata
            meta_path = lesson_dir / f"{safe_name}_meta.json"
            # videoLenMs comes from Skool's own metadata; persist it under both
            # names so sdc_resume_downloads.py can verify the downloaded .mp4
            # against the expected length (sdc_backend.py writes videoDurationMs).
            duration_ms = lesson.get('videoLenMs') or 0
            meta_path.write_text(json.dumps({
                'title': lesson['name'], 'videoLink': video_link,
                'videoId': video_id, 'videoInfo': video_info,
                'videoDurationMs': duration_ms, 'videoLenMs': duration_ms,
                'url': lesson_url
            }, indent=2, ensure_ascii=False), encoding='utf-8')

    # ---- STEP 4: Generate tree ----
    tree = generate_tree(out_dir)
    (out_dir / 'course_tree.txt').write_text(tree, encoding='utf-8')

    logger.info(f"\n{'='*50}")
    logger.info(f"DOWNLOAD COMPLETE")
    logger.info(f"Modules: {len(modules)}")
    logger.info(f"Lessons: {total_lessons}")
    logger.info(f"Videos downloaded: {total_downloaded}")
    logger.info(f"Output: {out_dir}")
    logger.info(f"{'='*50}")
    logger.info(f"\n{tree}")

    await s.stop()


if __name__ == '__main__':
    asyncio.run(main())
