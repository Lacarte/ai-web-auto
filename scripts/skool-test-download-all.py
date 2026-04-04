"""
Test full download: video (Loom) + assets (PDF) from a specific lesson.
https://www.skool.com/the-ai-blueprint/classroom/7a5299dc?md=ad7972ecb1f44f80802cfaa6ad7fc6c4
Same tab.
"""
import asyncio, json, sys, os, base64
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from python.core.server import WebSocketServer
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
    print(f"=== Navigate to lesson (same tab) ===")
    print(f"  {url}")
    await s.navigate(url)
    await asyncio.sleep(6)
    await save_ss(s, "1-lesson-page")

    # =========================================================
    # 1. Extract all metadata: video, resources, attachments
    # =========================================================
    print("\n=== 1. Extract lesson metadata ===")
    meta_result = await run_js(s, """
    (function() {
        var nd = document.getElementById('__NEXT_DATA__');
        if (!nd) return JSON.stringify({error: 'no __NEXT_DATA__'});
        var data = JSON.parse(nd.textContent);
        var pp = data.props.pageProps;
        var selMod = pp.selectedModule;
        var groupId = pp.currentGroup ? pp.currentGroup.id : '';
        var group = pp.currentGroup ? pp.currentGroup.name : '';

        var result = {
            group: group,
            groupId: groupId,
            selectedModule: selMod,
            lessonMeta: null,
            iframes: [],
            domResources: []
        };

        // Find selected lesson in course.children
        if (pp.course && pp.course.children) {
            for (var i = 0; i < pp.course.children.length; i++) {
                var child = pp.course.children[i];
                if (child.course && child.course.id === selMod) {
                    var meta = child.course.metadata || {};
                    result.lessonMeta = {
                        id: child.course.id,
                        name: child.course.name,
                        title: meta.title,
                        videoId: meta.videoId || null,
                        videoLink: meta.videoLink || null,
                        videoStream: meta.videoStream || null,
                        videoLenMs: meta.videoLenMs || null,
                        videoThumbnail: meta.videoThumbnail || null,
                        resources: null,
                        description: meta.desc || meta.description || null,
                        allMetaKeys: Object.keys(meta)
                    };

                    // Parse resources
                    if (meta.resources) {
                        try {
                            var res = typeof meta.resources === 'string' ? JSON.parse(meta.resources) : meta.resources;
                            result.lessonMeta.resources = res;
                        } catch(e) {
                            result.lessonMeta.resourcesRaw = String(meta.resources).substring(0, 500);
                        }
                    }
                    break;
                }
            }
        }

        // Check iframes (non-Stripe)
        var iframes = document.querySelectorAll('iframe');
        for (var j = 0; j < iframes.length; j++) {
            var src = iframes[j].src || '';
            if (!src.includes('stripe.com') && src.length > 10) {
                result.iframes.push(src.substring(0, 200));
            }
        }

        // Check DOM for resource/attachment links
        // The XPath the user gave points to a PDF icon area
        var fileLinks = document.querySelectorAll('a[href*="assets.skool.com"], a[download], a[href*="/f/"]');
        for (var k = 0; k < fileLinks.length; k++) {
            result.domResources.push({
                href: fileLinks[k].getAttribute('href'),
                text: fileLinks[k].textContent.trim().substring(0, 80),
                download: fileLinks[k].getAttribute('download') || null
            });
        }

        // Also check for file attachment elements with the PDF icon pattern
        var attachments = document.querySelectorAll('[class*="FileIcon"], [class*="Attachment"], [class*="Resource"], [class*="resource"]');
        result.attachmentElements = [];
        for (var m = 0; m < attachments.length; m++) {
            var el = attachments[m];
            var parent = el.closest('a') || el.parentElement;
            result.attachmentElements.push({
                class: (el.className || '').substring(0, 80),
                tag: el.tagName,
                parentTag: parent ? parent.tagName : null,
                parentHref: parent && parent.tagName === 'A' ? parent.getAttribute('href') : null,
                text: (parent || el).textContent.trim().substring(0, 100)
            });
        }

        // Check the specific XPath the user provided
        var xpath = '/html/body/div[1]/div/div[1]/div[3]/div/div/div[2]/div/div/div[2]/div[2]/div[2]/div/div';
        var xpathEl = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
        if (xpathEl) {
            var xpathParent = xpathEl.closest('a');
            result.xpathElement = {
                tag: xpathEl.tagName,
                class: (xpathEl.className || '').substring(0, 100),
                text: xpathEl.textContent.trim().substring(0, 100),
                innerHTML: xpathEl.innerHTML.substring(0, 300),
                parentLink: xpathParent ? xpathParent.getAttribute('href') : null,
                parentText: xpathParent ? xpathParent.textContent.trim().substring(0, 100) : null
            };
            // Walk up looking for an <a> tag
            var walker = xpathEl;
            for (var w = 0; w < 15; w++) {
                walker = walker.parentElement;
                if (!walker) break;
                if (walker.tagName === 'A' && walker.href) {
                    result.xpathElement.closestAnchorHref = walker.getAttribute('href');
                    result.xpathElement.closestAnchorText = walker.textContent.trim().substring(0, 100);
                    break;
                }
            }
        }

        return JSON.stringify(result);
    })()
    """)

    meta = json.loads(meta_result) if isinstance(meta_result, str) else meta_result
    os.makedirs("output/discover", exist_ok=True)
    with open("output/discover/blueprint-lesson-full.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    lm = meta.get('lessonMeta', {})
    print(f"  Group: {meta.get('group')} (id={meta.get('groupId')})")
    print(f"  Title: {lm.get('title')}")
    print(f"  Video link: {lm.get('videoLink')}")
    print(f"  Video ID: {lm.get('videoId')}")
    print(f"  Meta keys: {lm.get('allMetaKeys')}")
    print(f"  Iframes: {meta.get('iframes')}")

    resources = lm.get('resources')
    print(f"\n  Resources from __NEXT_DATA__:")
    if resources:
        if isinstance(resources, list):
            for i, r in enumerate(resources):
                print(f"    [{i}] {json.dumps(r, ensure_ascii=False)[:200]}")
        else:
            print(f"    {str(resources)[:300]}")
    else:
        print(f"    None")
        print(f"    Raw: {lm.get('resourcesRaw', 'N/A')}")

    print(f"\n  DOM resource links: {meta.get('domResources')}")
    print(f"  Attachment elements: {meta.get('attachmentElements')}")
    print(f"  XPath element: {meta.get('xpathElement')}")

    # =========================================================
    # 2. Download video (Loom)
    # =========================================================
    video_link = lm.get('videoLink') or ''
    loom_iframe = [u for u in meta.get('iframes', []) if 'loom.com' in u]

    if video_link or loom_iframe:
        dl_url = video_link or loom_iframe[0]
        # Convert embed to share URL for yt-dlp
        if '/embed/' in dl_url:
            dl_url = dl_url.split('?')[0].replace('/embed/', '/share/')

        print(f"\n=== 2. Download video (Loom) ===")
        print(f"  URL: {dl_url[:120]}")

        out_video = os.path.abspath("output/test-download/lesson_video.mp4")
        try:
            proc = await asyncio.create_subprocess_exec(
                'yt-dlp', '--no-warnings',
                '-o', out_video,
                '--merge-output-format', 'mp4',
                dl_url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode == 0 and os.path.exists(out_video):
                size = os.path.getsize(out_video) / (1024 * 1024)
                print(f"  SUCCESS: {size:.1f} MB -> {out_video}")
            else:
                print(f"  yt-dlp exit={proc.returncode}")
                print(f"  stdout: {stdout.decode()[:300]}")
                print(f"  stderr: {stderr.decode()[:300]}")
        except FileNotFoundError:
            print("  yt-dlp not in PATH - skipping video download")
        except asyncio.TimeoutError:
            print("  Timeout")
    else:
        print("\n=== 2. No video link found ===")

    # =========================================================
    # 3. Download resources/assets (PDFs etc.)
    # =========================================================
    print(f"\n=== 3. Download resources/assets ===")

    group_id = meta.get('groupId', '')
    downloaded = []

    # From __NEXT_DATA__ resources
    if resources and isinstance(resources, list):
        for r in resources:
            file_id = r.get('file_id')
            file_name = r.get('file_name') or r.get('title', 'resource')
            title = r.get('title', '')
            if file_id and group_id:
                dl_url = f"https://assets.skool.com/f/{group_id}/{file_id}"
                print(f"  Downloading: {title} ({file_name})")
                print(f"    URL: {dl_url}")
                out_path = f"output/test-download/{file_name}"
                try:
                    timeout = ClientTimeout(total=60)
                    async with ClientSession(timeout=timeout) as client:
                        async with client.get(dl_url, headers={
                            'Referer': 'https://www.skool.com/',
                            'Origin': 'https://www.skool.com'
                        }) as resp:
                            print(f"    Status: {resp.status}, Content-Type: {resp.content_type}")
                            if resp.status == 200:
                                data_bytes = await resp.read()
                                with open(out_path, 'wb') as f:
                                    f.write(data_bytes)
                                size = len(data_bytes) / 1024
                                print(f"    SUCCESS: {size:.1f} KB -> {out_path}")
                                downloaded.append(out_path)
                            else:
                                body = await resp.text()
                                print(f"    FAILED: {body[:200]}")
                except Exception as e:
                    print(f"    ERROR: {e}")
            else:
                print(f"  Skipped: {title} (no file_id or groupId)")

    # Also try DOM resource links
    for dr in meta.get('domResources', []):
        href = dr.get('href', '')
        if href and 'assets.skool.com' in href:
            fname = href.split('/')[-1] or 'resource'
            out_path = f"output/test-download/{fname}"
            if out_path not in downloaded:
                print(f"  DOM link: {href[:100]}")
                try:
                    timeout = ClientTimeout(total=60)
                    async with ClientSession(timeout=timeout) as client:
                        async with client.get(href if href.startswith('http') else f"https://www.skool.com{href}") as resp:
                            if resp.status == 200:
                                with open(out_path, 'wb') as f:
                                    f.write(await resp.read())
                                print(f"    SUCCESS -> {out_path}")
                                downloaded.append(out_path)
                except Exception as e:
                    print(f"    ERROR: {e}")

    # Try the XPath element's parent link
    xpath_el = meta.get('xpathElement', {})
    anchor_href = xpath_el.get('closestAnchorHref') or xpath_el.get('parentLink')
    if anchor_href and anchor_href not in [d.get('href') for d in meta.get('domResources', [])]:
        print(f"  XPath anchor: {anchor_href}")

    print(f"\n  Total downloaded: {len(downloaded)} files")
    for d in downloaded:
        size = os.path.getsize(d) / 1024
        print(f"    {d} ({size:.1f} KB)")

    # Final listing
    print(f"\n=== Output files ===")
    for f_name in os.listdir("output/test-download"):
        fpath = os.path.join("output/test-download", f_name)
        size = os.path.getsize(fpath)
        if size > 1024 * 1024:
            print(f"  {f_name} ({size / (1024*1024):.1f} MB)")
        else:
            print(f"  {f_name} ({size / 1024:.1f} KB)")

    print("\n=== DONE ===")
    await s.stop()

asyncio.run(main())
