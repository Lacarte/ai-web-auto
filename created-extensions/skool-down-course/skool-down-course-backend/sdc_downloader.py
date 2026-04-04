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
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from ai_web_auto_backend.core.server import WebSocketServer
from ai_web_auto_backend.core.models import Command

try:
    from loguru import logger
except ImportError:
    import logging as logger

OUTPUT_DIR = Path('skool-output')


def sanitize(name):
    return re.sub(r'[<>:"/\\|?*]', '_', name).strip()[:100]


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


async def download_video(video_info, output_path):
    """Download video using ffmpeg or yt-dlp."""
    output_path = Path(output_path)
    if output_path.exists() and output_path.stat().st_size > 1000:
        logger.info(f"Video already exists: {output_path}")
        return str(output_path)

    m3u8 = video_info.get('m3u8Url') or video_info.get('stream') or ''
    url = video_info.get('url') or ''
    vtype = video_info.get('type') or ''
    embed = video_info.get('embedUrl') or ''

    # Strategy 1: M3U8 via ffmpeg (Skool native)
    if m3u8 and '.m3u8' in m3u8:
        logger.info(f"Downloading M3U8 via ffmpeg: {m3u8[:80]}...")
        try:
            proc = await asyncio.create_subprocess_exec(
                'ffmpeg', '-y',
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
        logger.info(f"Downloading via yt-dlp: {dl_url[:80]}...")
        try:
            proc = await asyncio.create_subprocess_exec(
                'yt-dlp', '--no-warnings', '-o', str(output_path),
                '--merge-output-format', 'mp4', dl_url,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
            if proc.returncode == 0 and output_path.exists():
                mb = output_path.stat().st_size / (1024*1024)
                logger.success(f"Video downloaded: {mb:.1f} MB -> {output_path}")
                return str(output_path)
            logger.error(f"yt-dlp failed: {stderr.decode()[:300]}")
        except FileNotFoundError:
            logger.warning("yt-dlp not in PATH")
        except asyncio.TimeoutError:
            logger.error("yt-dlp timeout")

    # Save URL info for manual download
    info_path = output_path.with_suffix('.video_url.txt')
    info_path.write_text(f"URL: {url}\nStream: {m3u8}\nType: {vtype}\nEmbed: {embed}\n")
    return None


async def download_resource(url, output_path):
    """Download a resource file via aiohttp."""
    output_path = Path(output_path)
    if output_path.exists():
        return str(output_path)
    try:
        from aiohttp import ClientSession, ClientTimeout
        async with ClientSession(timeout=ClientTimeout(total=120)) as client:
            async with client.get(url, headers={
                'Referer': 'https://www.skool.com/',
                'Origin': 'https://www.skool.com'
            }) as resp:
                if resp.status == 200:
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(await resp.read())
                    return str(output_path)
    except Exception as e:
        logger.error(f"Resource download failed: {e}")
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
    logger.info("Scanning modules...")
    scan = await run_js(s, """
    (function() {
        var el = document.getElementById('__NEXT_DATA__');
        if (!el) return JSON.stringify({error: 'no __NEXT_DATA__'});
        var nd = JSON.parse(el.textContent);
        var pp = nd.props && nd.props.pageProps;
        if (!pp) return JSON.stringify({error: 'no pageProps'});
        var group = pp.currentGroup ? pp.currentGroup.name : '';
        var groupId = pp.currentGroup ? pp.currentGroup.id : '';
        var all = pp.allCourses || [];
        var modules = [];
        for (var i = 0; i < all.length; i++) {
            var c = all[i];
            var meta = c.metadata || {};
            if (!meta.hasAccess && meta.minAccessLevel != null) continue;
            modules.push({
                id: c.id, slug: c.name, title: meta.title || c.name,
                url: '/' + group + '/classroom/' + c.name
            });
        }
        var titleParts = document.title.split(' \\u00b7 ');
        var siteName = (pp.currentGroup && pp.currentGroup.metadata && pp.currentGroup.metadata.name) ||
                       (titleParts.length > 1 ? titleParts[titleParts.length - 1] : titleParts[0]) ||
                       'SkoolCourse';
        return JSON.stringify({group: group, groupId: groupId, modules: modules, siteName: siteName});
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
    logger.info(f"Found {len(modules)} modules for '{site_name}' (group={group_name})")

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
        await s.navigate(mod_url)
        await asyncio.sleep(5)

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

            mod_dir = out_dir / sanitize(mod['title'])
            lesson_dir = mod_dir / sanitize(lesson['name'])
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
                result = await download_video(video_info, str(vid_path))
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
                                result = await download_resource(dl_url, str(res_path))
                                if result:
                                    logger.info(f"    Resource: {fname}")
                except Exception as e:
                    logger.error(f"    Resource error: {e}")

            # Save metadata
            meta_path = lesson_dir / f"{safe_name}_meta.json"
            meta_path.write_text(json.dumps({
                'title': lesson['name'], 'videoLink': video_link,
                'videoId': video_id, 'videoInfo': video_info,
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
