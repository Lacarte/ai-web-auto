"""
Test download video + assets from blueprint lesson. Handles nested children.
Same tab.
"""
import asyncio, json, sys, os, base64
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_web_auto_python.core.server import WebSocketServer
from aiohttp import ClientSession, ClientTimeout
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

async def run_js(s, script):
    resp = await s.execute_script(script)
    if resp and resp.data and resp.data.extracted_data:
        return resp.data.extracted_data.get("result", resp.data.extracted_data)
    return None

async def save_ss(s, name):
    resp = await s.screenshot()
    if resp.data.screenshot:
        img = resp.data.screenshot
        if img.startswith("data:"): img = img.split(",", 1)[1]
        os.makedirs("output/test-download", exist_ok=True)
        with open(f"output/test-download/{name}.png", "wb") as f:
            f.write(base64.b64decode(img))
        print(f"  Screenshot: output/test-download/{name}.png")

async def main():
    s = WebSocketServer()
    await s.start()
    await s.wait_for_connection(timeout=60)
    print("Connected!\n")

    url = "https://www.skool.com/the-ai-blueprint/classroom/7a5299dc?md=ad7972ecb1f44f80802cfaa6ad7fc6c4"
    print(f"=== Navigate (same tab) ===")
    await s.navigate(url)
    await asyncio.sleep(6)
    await save_ss(s, "1-page")

    # =========================================================
    # 1. Deep extract - search ALL children recursively
    # =========================================================
    print("\n=== 1. Extract metadata (recursive search) ===")
    meta_result = await run_js(s, """
    (function() {
        var nd = document.getElementById('__NEXT_DATA__');
        if (!nd) return JSON.stringify({error: 'no __NEXT_DATA__'});
        var data = JSON.parse(nd.textContent);
        var pp = data.props.pageProps;
        var selMod = pp.selectedModule;
        var groupId = pp.currentGroup ? pp.currentGroup.id : '';
        var group = pp.currentGroup ? pp.currentGroup.name : '';

        // Recursive search for the selected module
        function findLesson(children, targetId) {
            if (!children) return null;
            for (var i = 0; i < children.length; i++) {
                var child = children[i];
                if (child.course && child.course.id === targetId) return child.course;
                if (child.children) {
                    var found = findLesson(child.children, targetId);
                    if (found) return found;
                }
            }
            return null;
        }

        var lessonCourse = pp.course ? findLesson(pp.course.children || [], selMod) : null;
        var lessonMeta = null;
        if (lessonCourse) {
            var meta = lessonCourse.metadata || {};
            lessonMeta = {
                id: lessonCourse.id,
                name: lessonCourse.name,
                title: meta.title,
                videoId: meta.videoId || null,
                videoLink: meta.videoLink || null,
                videoStream: meta.videoStream || null,
                videoLenMs: meta.videoLenMs || null,
                resources: null,
                allMetaKeys: Object.keys(meta)
            };
            if (meta.resources) {
                try {
                    lessonMeta.resources = typeof meta.resources === 'string' ? JSON.parse(meta.resources) : meta.resources;
                } catch(e) {
                    lessonMeta.resourcesRaw = String(meta.resources).substring(0, 500);
                }
            }
        }

        // Check iframes
        var iframes = [];
        document.querySelectorAll('iframe').forEach(function(f) {
            if (!f.src.includes('stripe.com') && f.src.length > 10) iframes.push(f.src.substring(0, 200));
        });

        // Check resource/file download elements in DOM
        // Resources in Skool use a click handler, not a direct <a> tag
        // Look for ResourceWrapper and find the actual download URL pattern
        var resourceElements = [];
        var resWrappers = document.querySelectorAll('[class*="ResourceWrapper"], [class*="resource-item"]');
        for (var r = 0; r < resWrappers.length; r++) {
            var wrapper = resWrappers[r];
            var label = wrapper.querySelector('[class*="ResourceLabel"]');
            var icon = wrapper.querySelector('[class*="FileIcon"]');
            var link = wrapper.querySelector('a');
            var clickable = wrapper.querySelector('[role="button"], button, [class*="clickable"]');

            resourceElements.push({
                text: (label || wrapper).textContent.trim().substring(0, 80),
                hasLink: !!link,
                linkHref: link ? link.getAttribute('href') : null,
                hasButton: !!clickable,
                rect: (function() { var r2 = wrapper.getBoundingClientRect(); return {x: Math.round(r2.x), y: Math.round(r2.y), w: Math.round(r2.width), h: Math.round(r2.height)}; })(),
                classes: wrapper.className.substring(0, 100)
            });
        }

        // Also look for download links in general
        var downloadLinks = [];
        document.querySelectorAll('a').forEach(function(a) {
            var href = a.getAttribute('href') || '';
            if (href.includes('assets.skool.com') || href.includes('/f/') || a.hasAttribute('download')) {
                downloadLinks.push({href: href, text: a.textContent.trim().substring(0, 60)});
            }
        });

        // Structure info
        var courseStructure = null;
        if (pp.course && pp.course.children) {
            courseStructure = {
                childrenCount: pp.course.children.length,
                structure: pp.course.children.map(function(c) {
                    return {
                        id: c.course ? c.course.id : null,
                        title: c.course && c.course.metadata ? c.course.metadata.title : null,
                        hasSubChildren: !!(c.children && c.children.length > 0),
                        subCount: c.children ? c.children.length : 0
                    };
                })
            };
        }

        return JSON.stringify({
            group: group, groupId: groupId, selectedModule: selMod,
            lessonMeta: lessonMeta,
            iframes: iframes,
            resourceElements: resourceElements,
            downloadLinks: downloadLinks,
            courseStructure: courseStructure
        });
    })()
    """)

    meta = json.loads(meta_result) if isinstance(meta_result, str) else meta_result
    with open("output/discover/blueprint-lesson-full2.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    lm = meta.get('lessonMeta') or {}
    print(f"  Group: {meta.get('group')} (id={meta.get('groupId')})")
    print(f"  Selected: {meta.get('selectedModule')}")
    print(f"  Title: {lm.get('title', 'NOT FOUND')}")
    print(f"  Video link: {lm.get('videoLink')}")
    print(f"  Video ID: {lm.get('videoId')}")
    print(f"  Meta keys: {lm.get('allMetaKeys')}")
    print(f"  Iframes: {meta.get('iframes')}")

    # Show course structure
    cs = meta.get('courseStructure')
    if cs:
        print(f"\n  Course structure ({cs['childrenCount']} top-level children):")
        for c in cs.get('structure', []):
            nested = f" -> {c['subCount']} sub-children" if c.get('hasSubChildren') else " (flat)"
            sel = " <<< SELECTED" if c.get('id') == meta.get('selectedModule') else ""
            print(f"    {c.get('title')}{nested}{sel}")

    resources = lm.get('resources')
    print(f"\n  Resources from __NEXT_DATA__:")
    if resources and isinstance(resources, list):
        for i, r in enumerate(resources):
            print(f"    [{i}] title={r.get('title')} file={r.get('file_name')} id={r.get('file_id')}")
            print(f"         type={r.get('file_content_type')} keys={list(r.keys())}")
    else:
        print(f"    {resources}")

    print(f"\n  DOM resource elements: {len(meta.get('resourceElements', []))}")
    for re_item in meta.get('resourceElements', []):
        print(f"    '{re_item.get('text')}' hasLink={re_item.get('hasLink')} link={re_item.get('linkHref')} pos=({re_item['rect']['x']},{re_item['rect']['y']})")

    print(f"  DOM download links: {meta.get('downloadLinks')}")

    # =========================================================
    # 2. Download video
    # =========================================================
    video_link = lm.get('videoLink') or ''
    video_id = lm.get('videoId') or ''
    loom_iframes = [u for u in meta.get('iframes', []) if 'loom.com' in u]

    if video_link or loom_iframes:
        dl_url = video_link or loom_iframes[0]
        if '/embed/' in dl_url:
            dl_url = dl_url.split('?')[0].replace('/embed/', '/share/')
        print(f"\n=== 2. Download video ===")
        print(f"  URL: {dl_url[:120]}")
        out_video = os.path.abspath("output/test-download/lesson_video.mp4")
        try:
            proc = await asyncio.create_subprocess_exec(
                'yt-dlp', '--no-warnings', '-o', out_video, '--merge-output-format', 'mp4', dl_url,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode == 0 and os.path.exists(out_video):
                size = os.path.getsize(out_video) / (1024 * 1024)
                print(f"  SUCCESS: {size:.1f} MB -> {out_video}")
            else:
                print(f"  yt-dlp failed (exit={proc.returncode}): {stderr.decode()[:300]}")
        except FileNotFoundError:
            print("  yt-dlp not in PATH")
        except asyncio.TimeoutError:
            print("  Timeout")
    elif video_id:
        print(f"\n=== 2. Skool native video (need to click play) ===")
        print(f"  videoId: {video_id} - would need CDP click + mux-player capture")
    else:
        print(f"\n=== 2. No video on this lesson ===")

    # =========================================================
    # 3. Download resources (PDFs etc.)
    # =========================================================
    print(f"\n=== 3. Download resources ===")
    group_id = meta.get('groupId', '')
    downloaded = []

    if resources and isinstance(resources, list):
        for r in resources:
            file_id = r.get('file_id')
            file_name = r.get('file_name') or r.get('title', 'resource')
            title = r.get('title', file_name)
            if file_id and group_id:
                dl_url = f"https://assets.skool.com/f/{group_id}/{file_id}"
                print(f"  Downloading: '{title}' ({file_name})")
                print(f"    URL: {dl_url}")
                out_path = os.path.abspath(f"output/test-download/{file_name}")
                try:
                    timeout = ClientTimeout(total=60)
                    async with ClientSession(timeout=timeout) as client:
                        async with client.get(dl_url, headers={
                            'Referer': 'https://www.skool.com/',
                            'Origin': 'https://www.skool.com'
                        }) as resp:
                            ct = resp.content_type or ''
                            print(f"    Status: {resp.status} Content-Type: {ct}")
                            if resp.status == 200:
                                data_bytes = await resp.read()
                                with open(out_path, 'wb') as f_out:
                                    f_out.write(data_bytes)
                                size = len(data_bytes) / 1024
                                print(f"    SUCCESS: {size:.1f} KB -> {out_path}")
                                downloaded.append(out_path)
                            elif resp.status == 302 or resp.status == 301:
                                redirect = resp.headers.get('Location', '')
                                print(f"    Redirect -> {redirect[:150]}")
                            else:
                                body = await resp.text()
                                print(f"    FAILED: {body[:200]}")
                except Exception as e:
                    print(f"    ERROR: {e}")
    else:
        # No resources in metadata - try clicking the resource element
        res_els = meta.get('resourceElements', [])
        if res_els:
            print(f"  No resources in metadata, but {len(res_els)} DOM elements found.")
            print(f"  Trying to click resource at ({res_els[0]['rect']['x']},{res_els[0]['rect']['y']})...")
            rx = res_els[0]['rect']['x'] + res_els[0]['rect']['w'] // 2
            ry = res_els[0]['rect']['y'] + res_els[0]['rect']['h'] // 2
            await s.click_cdp(rx, ry)
            await asyncio.sleep(3)
            await save_ss(s, "2-after-resource-click")

            # Check if a download started or a new link appeared
            post_click = await run_js(s, """
            (function() {
                var links = [];
                document.querySelectorAll('a').forEach(function(a) {
                    var href = a.getAttribute('href') || '';
                    if (href.includes('assets.skool.com') || href.includes('/f/') || a.hasAttribute('download')) {
                        links.push({href: href, text: a.textContent.trim().substring(0, 60)});
                    }
                });
                return JSON.stringify({newLinks: links, url: window.location.href});
            })()
            """)
            pc = json.loads(post_click) if isinstance(post_click, str) else post_click
            print(f"  After click - download links: {pc.get('newLinks')}")
        else:
            print("  No resources found in metadata or DOM")

    # =========================================================
    # Summary
    # =========================================================
    print(f"\n=== Output files ===")
    if os.path.exists("output/test-download"):
        for f_name in sorted(os.listdir("output/test-download")):
            fpath = os.path.join("output/test-download", f_name)
            size = os.path.getsize(fpath)
            if size > 1024 * 1024:
                print(f"  {f_name} ({size / (1024*1024):.1f} MB)")
            else:
                print(f"  {f_name} ({size / 1024:.1f} KB)")

    print("\n=== DONE ===")
    await s.stop()

asyncio.run(main())
