"""
Test resource download via service worker (bypasses CORS) and video via direct Loom URL.
Same tab.
"""
import asyncio, json, sys, os, base64
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from python.core.server import WebSocketServer
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

async def run_js(s, script):
    resp = await s.execute_script(script)
    if resp and resp.data and resp.data.extracted_data:
        return resp.data.extracted_data.get("result", resp.data.extracted_data)
    return None

async def main():
    s = WebSocketServer()
    await s.start()
    await s.wait_for_connection(timeout=60)
    print("Connected!\n")

    # =========================================================
    # TEST 1: PDF resource download
    # =========================================================
    url = "https://www.skool.com/the-ai-blueprint/classroom/7a5299dc?md=ad7972ecb1f44f80802cfaa6ad7fc6c4"
    print(f"=== Navigate to lesson with PDF (same tab) ===")
    await s.navigate(url)
    await asyncio.sleep(6)

    # Method A: Try chrome.downloads.download() from service worker
    # The content script sends message to SW which uses chrome.downloads
    print("\n=== Method A: Try chrome.runtime.sendMessage to trigger download ===")
    download_result = await run_js(s, """
    (async function() {
        var nd = document.getElementById('__NEXT_DATA__');
        var data = JSON.parse(nd.textContent);
        var pp = data.props.pageProps;
        var groupId = pp.currentGroup ? pp.currentGroup.id : '';
        var selMod = pp.selectedModule;

        function findLesson(children, id) {
            if (!children) return null;
            for (var i = 0; i < children.length; i++) {
                if (children[i].course && children[i].course.id === id) return children[i].course;
                if (children[i].children) {
                    var found = findLesson(children[i].children, id);
                    if (found) return found;
                }
            }
            return null;
        }

        var lesson = findLesson(pp.course.children, selMod);
        if (!lesson || !lesson.metadata) return JSON.stringify({error: 'no lesson'});
        var meta = lesson.metadata;
        var resources = typeof meta.resources === 'string' ? JSON.parse(meta.resources) : meta.resources;
        if (!resources || !resources.length) return JSON.stringify({error: 'no resources'});

        var r = resources[0];
        var fileId = r.file_id;
        var fileName = r.file_name || 'resource.pdf';
        var downloadUrl = 'https://assets.skool.com/f/' + groupId + '/' + fileId;

        return JSON.stringify({
            title: meta.title,
            fileName: fileName,
            fileId: fileId,
            downloadUrl: downloadUrl,
            groupId: groupId,
            contentType: r.file_content_type
        });
    })()
    """)
    dl_info = json.loads(download_result) if isinstance(download_result, str) else download_result

    if dl_info.get('error'):
        print(f"  Error: {dl_info}")
    else:
        print(f"  Lesson: {dl_info.get('title')}")
        print(f"  File: {dl_info.get('fileName')} (type: {dl_info.get('contentType')})")
        print(f"  URL: {dl_info.get('downloadUrl')}")

    # Method B: Use executeScript to fetch from the page context with cookies
    # The key insight: assets.skool.com blocks CORS from www.skool.com
    # But the browser's own navigation/download works fine
    # Solution: Use an <a download> element or window.open
    print("\n=== Method B: Trigger browser download via <a> click ===")
    trigger_result = await run_js(s, """
    (function() {
        var downloadUrl = '""" + dl_info.get('downloadUrl', '') + """';
        var fileName = '""" + dl_info.get('fileName', 'resource') + """';

        // Create a hidden anchor and click it
        var a = document.createElement('a');
        a.href = downloadUrl;
        a.download = fileName;
        a.target = '_blank';
        a.style.display = 'none';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);

        return JSON.stringify({triggered: true, url: downloadUrl, file: fileName});
    })()
    """)
    print(f"  Result: {trigger_result}")
    await asyncio.sleep(3)

    # Method C: Use the Skool API endpoint that the page itself uses
    print("\n=== Method C: Check how Skool page downloads resources ===")
    api_check = await run_js(s, """
    (function() {
        // Look for the resource click handler in the DOM
        var wrapper = document.querySelector('[class*="ResourceWrapper"]');
        if (!wrapper) return JSON.stringify({error: 'no ResourceWrapper'});

        // Check all event listeners or onclick handlers
        var result = {
            wrapperTag: wrapper.tagName,
            wrapperRole: wrapper.getAttribute('role'),
            wrapperTabindex: wrapper.getAttribute('tabindex'),
            wrapperCursor: window.getComputedStyle(wrapper).cursor,
            innerElements: []
        };

        // Check inner elements for links
        wrapper.querySelectorAll('*').forEach(function(el) {
            if (el.tagName === 'A' || el.onclick || el.getAttribute('role') === 'button') {
                result.innerElements.push({
                    tag: el.tagName,
                    href: el.getAttribute('href'),
                    role: el.getAttribute('role'),
                    class: (el.className || '').substring(0, 60)
                });
            }
        });

        // The ResourceWrapper itself is likely the clickable element
        // Skool probably uses window.open() or location.href in the click handler
        // Let's check if clicking it opens a new URL
        result.wrapperClasses = wrapper.className.substring(0, 100);

        // Check parent elements
        var parent = wrapper.parentElement;
        if (parent) {
            result.parentTag = parent.tagName;
            result.parentHref = parent.getAttribute && parent.getAttribute('href');
            result.parentRole = parent.getAttribute && parent.getAttribute('role');
        }

        return JSON.stringify(result);
    })()
    """)
    api_data = json.loads(api_check) if isinstance(api_check, str) else api_check
    print(f"  Wrapper: <{api_data.get('wrapperTag')}> cursor={api_data.get('wrapperCursor')} role={api_data.get('wrapperRole')}")
    print(f"  Inner elements: {api_data.get('innerElements')}")
    print(f"  Parent: <{api_data.get('parentTag')}> href={api_data.get('parentHref')}")

    # Method D: Click the resource element directly via CDP - this should trigger the native download
    print("\n=== Method D: CDP click the resource element ===")
    click_pos = await run_js(s, """
    (function() {
        var wrapper = document.querySelector('[class*="ResourceWrapper"]');
        if (!wrapper) return JSON.stringify({error: 'no wrapper'});
        var rect = wrapper.getBoundingClientRect();
        return JSON.stringify({
            x: Math.round(rect.x + rect.width/2),
            y: Math.round(rect.y + rect.height/2),
            w: Math.round(rect.width),
            h: Math.round(rect.height)
        });
    })()
    """)
    pos = json.loads(click_pos) if isinstance(click_pos, str) else click_pos
    if pos.get('x'):
        print(f"  Clicking resource at ({pos['x']}, {pos['y']})")
        # Scroll to make it visible first
        await run_js(s, f"window.scrollTo(0, {pos['y'] - 200})")
        await asyncio.sleep(1)
        await s.click_cdp(pos['x'], pos['y'] - (await run_js(s, "window.scrollY") or 0))
        await asyncio.sleep(3)

        # Check if download started
        print("  Waiting for download...")
        await asyncio.sleep(2)
        print("  (Check your browser's download folder for the PDF)")

    # =========================================================
    # TEST 2: Loom video - check if yt-dlp exists or try alternative
    # =========================================================
    print("\n\n=== TEST 2: Check video download tools ===")

    # Check what tools are available
    for tool in ['yt-dlp', 'ffmpeg', 'youtube-dl']:
        try:
            proc = await asyncio.create_subprocess_exec(
                tool, '--version',
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            print(f"  {tool}: {stdout.decode().strip()[:50]}")
        except (FileNotFoundError, asyncio.TimeoutError):
            print(f"  {tool}: NOT FOUND")

    # Navigate to Loom lesson
    vid_url = "https://www.skool.com/the-ai-blueprint/classroom/46ae251f?md=5c97d12519af4f589cc82836c3b09197"
    print(f"\n  Navigating to Loom lesson: {vid_url}")
    await s.navigate(vid_url)
    await asyncio.sleep(6)

    loom_info = await run_js(s, """
    (function() {
        var iframes = [];
        document.querySelectorAll('iframe').forEach(function(f) {
            if (f.src && !f.src.includes('stripe.com')) iframes.push(f.src);
        });
        var nd = document.getElementById('__NEXT_DATA__');
        var data = JSON.parse(nd.textContent);
        var pp = data.props.pageProps;
        function findLesson(children, id) {
            if (!children) return null;
            for (var i = 0; i < children.length; i++) {
                if (children[i].course && children[i].course.id === id) return children[i].course;
                if (children[i].children) { var f = findLesson(children[i].children, id); if (f) return f; }
            }
            return null;
        }
        var lesson = findLesson(pp.course.children, pp.selectedModule);
        var meta = lesson ? lesson.metadata || {} : {};
        return JSON.stringify({videoLink: meta.videoLink, iframes: iframes});
    })()
    """)
    li = json.loads(loom_info) if isinstance(loom_info, str) else loom_info
    loom_url = li.get('videoLink') or ''
    print(f"  Video link: {loom_url}")
    print(f"  Iframes: {li.get('iframes')}")

    if loom_url:
        # Try yt-dlp
        out_video = os.path.abspath("output/test-download/bienvenido.mp4")
        try:
            proc = await asyncio.create_subprocess_exec(
                'yt-dlp', '--no-warnings', '-o', out_video, '--merge-output-format', 'mp4', loom_url,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode == 0 and os.path.exists(out_video):
                size = os.path.getsize(out_video) / (1024 * 1024)
                print(f"  VIDEO SUCCESS: {size:.1f} MB -> {out_video}")
            else:
                print(f"  yt-dlp failed: {stderr.decode()[:300]}")
        except FileNotFoundError:
            print("  yt-dlp not available - install with: pip install yt-dlp")
        except asyncio.TimeoutError:
            print("  Timeout")

    # Final listing
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
