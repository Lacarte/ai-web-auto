"""
Test authenticated resource download - use browser cookies via content script fetch.
Also test on a lesson WITH a Loom video.
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

async def main():
    s = WebSocketServer()
    await s.start()
    await s.wait_for_connection(timeout=60)
    print("Connected!\n")

    # Lesson with PDF resource
    url = "https://www.skool.com/the-ai-blueprint/classroom/7a5299dc?md=ad7972ecb1f44f80802cfaa6ad7fc6c4"
    print(f"=== Navigate (same tab) ===")
    await s.navigate(url)
    await asyncio.sleep(6)

    # =========================================================
    # 1. Download PDF via browser fetch (with cookies)
    # =========================================================
    print("\n=== 1. Download PDF via authenticated browser fetch ===")
    pdf_result = await run_js(s, """
    (async function() {
        var nd = document.getElementById('__NEXT_DATA__');
        var data = JSON.parse(nd.textContent);
        var pp = data.props.pageProps;
        var groupId = pp.currentGroup ? pp.currentGroup.id : '';
        var selMod = pp.selectedModule;

        // Find lesson recursively
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
        if (!lesson || !lesson.metadata || !lesson.metadata.resources) {
            return JSON.stringify({error: 'no resources', lessonFound: !!lesson});
        }

        var meta = lesson.metadata;
        var resources = typeof meta.resources === 'string' ? JSON.parse(meta.resources) : meta.resources;
        var results = [];

        for (var i = 0; i < resources.length; i++) {
            var r = resources[i];
            var fileId = r.file_id;
            var fileName = r.file_name || 'resource';
            if (!fileId) { results.push({file: fileName, error: 'no file_id'}); continue; }

            var downloadUrl = 'https://assets.skool.com/f/' + groupId + '/' + fileId;

            // Try fetch with credentials (browser cookies)
            try {
                var resp = await fetch(downloadUrl, {
                    credentials: 'include',
                    headers: {
                        'Accept': '*/*',
                        'Origin': 'https://www.skool.com',
                        'Referer': 'https://www.skool.com/'
                    }
                });

                if (resp.ok) {
                    var blob = await resp.blob();
                    // Convert to base64 to send back
                    var b64 = await new Promise(function(resolve) {
                        var reader = new FileReader();
                        reader.onloadend = function() { resolve(reader.result); };
                        reader.readAsDataURL(blob);
                    });
                    results.push({
                        file: fileName,
                        title: r.title,
                        contentType: resp.headers.get('content-type'),
                        size: blob.size,
                        dataUrl: b64,
                        status: resp.status
                    });
                } else {
                    // Try alternative: Skool API download endpoint
                    var apiUrl = 'https://www.skool.com/api/resources/' + fileId + '/download';
                    var resp2 = await fetch(apiUrl, { credentials: 'include' });
                    if (resp2.ok) {
                        var blob2 = await resp2.blob();
                        var b642 = await new Promise(function(resolve) {
                            var reader = new FileReader();
                            reader.onloadend = function() { resolve(reader.result); };
                            reader.readAsDataURL(blob2);
                        });
                        results.push({
                            file: fileName, title: r.title,
                            contentType: resp2.headers.get('content-type'),
                            size: blob2.size, dataUrl: b642,
                            status: resp2.status, method: 'api'
                        });
                    } else {
                        results.push({
                            file: fileName, title: r.title,
                            error: 'Both methods failed',
                            directStatus: resp.status,
                            apiStatus: resp2.status,
                            apiBody: (await resp2.text()).substring(0, 200)
                        });
                    }
                }
            } catch(e) {
                results.push({file: fileName, error: e.message});
            }
        }

        return JSON.stringify({resources: results, groupId: groupId});
    })()
    """)

    pdf_data = json.loads(pdf_result) if isinstance(pdf_result, str) else pdf_result
    os.makedirs("output/test-download", exist_ok=True)
    print(f"  Results: {len(pdf_data.get('resources', []))}")

    for r in pdf_data.get('resources', []):
        print(f"\n  File: {r.get('file')} ({r.get('title')})")
        if r.get('error'):
            print(f"    ERROR: {r['error']}")
            if r.get('directStatus'): print(f"    Direct: {r['directStatus']}")
            if r.get('apiStatus'): print(f"    API: {r['apiStatus']}")
            if r.get('apiBody'): print(f"    Body: {r['apiBody'][:200]}")
        else:
            print(f"    Status: {r.get('status')} Method: {r.get('method', 'direct')}")
            print(f"    Type: {r.get('contentType')} Size: {r.get('size', 0) / 1024:.1f} KB")
            # Save the file
            data_url = r.get('dataUrl', '')
            if data_url and ',' in data_url:
                b64_part = data_url.split(',', 1)[1]
                file_bytes = base64.b64decode(b64_part)
                out_path = f"output/test-download/{r['file']}"
                with open(out_path, 'wb') as f:
                    f.write(file_bytes)
                print(f"    SAVED: {out_path} ({len(file_bytes) / 1024:.1f} KB)")

    # =========================================================
    # 2. Now test a lesson WITH video - go to first lesson
    # =========================================================
    print("\n\n=== 2. Test Loom video download ===")
    # Navigate to "Bienvenido" lesson which has a Loom video
    vid_url = "https://www.skool.com/the-ai-blueprint/classroom/46ae251f?md=5c97d12519af4f589cc82836c3b09197"
    print(f"  Navigating to: {vid_url}")
    await s.navigate(vid_url)
    await asyncio.sleep(6)

    # Get the Loom URL
    vid_info = await run_js(s, """
    (function() {
        var nd = document.getElementById('__NEXT_DATA__');
        var data = JSON.parse(nd.textContent);
        var pp = data.props.pageProps;
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
        var meta = lesson ? lesson.metadata || {} : {};

        var iframes = [];
        document.querySelectorAll('iframe').forEach(function(f) {
            if (f.src && !f.src.includes('stripe.com')) iframes.push(f.src);
        });

        return JSON.stringify({
            title: meta.title,
            videoLink: meta.videoLink || null,
            videoId: meta.videoId || null,
            iframes: iframes
        });
    })()
    """)

    vi = json.loads(vid_info) if isinstance(vid_info, str) else vid_info
    print(f"  Title: {vi.get('title')}")
    print(f"  Video link: {vi.get('videoLink')}")
    print(f"  Iframes: {vi.get('iframes')}")

    loom_url = vi.get('videoLink') or ''
    if not loom_url:
        for iframe in vi.get('iframes', []):
            if 'loom.com' in iframe:
                loom_url = iframe.split('?')[0].replace('/embed/', '/share/')
                break

    if loom_url:
        if '/embed/' in loom_url:
            loom_url = loom_url.split('?')[0].replace('/embed/', '/share/')
        print(f"  Download URL: {loom_url}")
        out_video = os.path.abspath("output/test-download/bienvenido_video.mp4")
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
                print(f"  yt-dlp failed (exit={proc.returncode})")
                print(f"  stderr: {stderr.decode()[:400]}")
        except FileNotFoundError:
            print("  yt-dlp not in PATH")
        except asyncio.TimeoutError:
            print("  Timeout")
    else:
        print("  No Loom URL found")

    # =========================================================
    # Summary
    # =========================================================
    print(f"\n=== All output files ===")
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
