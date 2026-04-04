"""
Final download test: Loom video via yt-dlp + PDF resource.
Same tab.
"""
import asyncio, json, sys, os, base64
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_web_auto_python.core.server import WebSocketServer
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
    os.makedirs("output/test-download", exist_ok=True)

    # =========================================================
    # 1. Download Loom video
    # =========================================================
    print("=" * 60)
    print("1. Download Loom video")
    print("=" * 60)
    await s.navigate("https://www.skool.com/the-ai-blueprint/classroom/46ae251f?md=5c97d12519af4f589cc82836c3b09197")
    await asyncio.sleep(6)

    loom_url = "https://www.loom.com/share/3a7130b7fea748bdb508a3284902ef94"
    out_video = os.path.abspath("output/test-download/bienvenido.mp4")
    print(f"  URL: {loom_url}")
    print(f"  Output: {out_video}")

    proc = await asyncio.create_subprocess_exec(
        'yt-dlp', '--no-warnings', '-o', out_video, '--merge-output-format', 'mp4', loom_url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    if proc.returncode == 0 and os.path.exists(out_video):
        size = os.path.getsize(out_video) / (1024 * 1024)
        print(f"  SUCCESS: {size:.1f} MB")
    else:
        print(f"  FAILED (exit={proc.returncode})")
        print(f"  stdout: {stdout.decode()[:300]}")
        print(f"  stderr: {stderr.decode()[:300]}")

    # =========================================================
    # 2. Download PDF resource
    # =========================================================
    print("\n" + "=" * 60)
    print("2. Download PDF resource")
    print("=" * 60)
    await s.navigate("https://www.skool.com/the-ai-blueprint/classroom/7a5299dc?md=ad7972ecb1f44f80802cfaa6ad7fc6c4")
    await asyncio.sleep(6)

    # Get the resource URL and try downloading via the page's own session
    # Skool resources need auth cookies - use the page's fetch with a workaround:
    # fetch to blob -> read as arrayBuffer -> send as base64
    pdf_result = await run_js(s, """
    (async function() {
        var nd = document.getElementById('__NEXT_DATA__');
        var data = JSON.parse(nd.textContent);
        var pp = data.props.pageProps;
        var groupId = pp.currentGroup ? pp.currentGroup.id : '';

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
        var resources = meta.resources ? (typeof meta.resources === 'string' ? JSON.parse(meta.resources) : meta.resources) : [];

        if (!resources.length) return JSON.stringify({error: 'no resources'});

        var r = resources[0];
        var fileId = r.file_id;
        var fileName = r.file_name || 'resource.pdf';
        var url = 'https://assets.skool.com/f/' + groupId + '/' + fileId;

        // Method 1: Use XMLHttpRequest with withCredentials (avoids CORS preflight for simple requests)
        try {
            var result = await new Promise(function(resolve, reject) {
                var xhr = new XMLHttpRequest();
                xhr.open('GET', url, true);
                xhr.withCredentials = true;
                xhr.responseType = 'blob';
                xhr.onload = function() {
                    if (xhr.status === 200) {
                        var reader = new FileReader();
                        reader.onloadend = function() {
                            resolve({ok: true, dataUrl: reader.result, size: xhr.response.size, method: 'xhr'});
                        };
                        reader.readAsDataURL(xhr.response);
                    } else {
                        resolve({ok: false, status: xhr.status, method: 'xhr'});
                    }
                };
                xhr.onerror = function() { resolve({ok: false, error: 'xhr error', method: 'xhr'}); };
                xhr.send();
            });
            if (result.ok) return JSON.stringify({file: fileName, size: result.size, dataUrl: result.dataUrl, method: result.method});
        } catch(e) {}

        // Method 2: Use Skool's internal API - /api/group/{groupId}/resource/{fileId}
        var apiUrls = [
            '/api/group/' + groupId + '/resource/' + fileId,
            '/api/v1/group/' + groupId + '/resource/' + fileId + '/download'
        ];
        for (var i = 0; i < apiUrls.length; i++) {
            try {
                var resp = await fetch(apiUrls[i], {credentials: 'include'});
                if (resp.ok) {
                    var ct = resp.headers.get('content-type') || '';
                    if (ct.includes('json')) {
                        var json = await resp.json();
                        // API might return a signed download URL
                        if (json.url || json.downloadUrl) {
                            var dlUrl = json.url || json.downloadUrl;
                            var dlResp = await fetch(dlUrl);
                            if (dlResp.ok) {
                                var blob = await dlResp.blob();
                                var b64 = await new Promise(function(resolve) {
                                    var rd = new FileReader();
                                    rd.onloadend = function() { resolve(rd.result); };
                                    rd.readAsDataURL(blob);
                                });
                                return JSON.stringify({file: fileName, size: blob.size, dataUrl: b64, method: 'api-redirect'});
                            }
                        }
                        return JSON.stringify({file: fileName, apiResponse: json, method: 'api-json'});
                    } else {
                        var blob2 = await resp.blob();
                        var b642 = await new Promise(function(resolve) {
                            var rd = new FileReader();
                            rd.onloadend = function() { resolve(rd.result); };
                            rd.readAsDataURL(blob2);
                        });
                        return JSON.stringify({file: fileName, size: blob2.size, dataUrl: b642, method: 'api-direct'});
                    }
                }
            } catch(e) {}
        }

        return JSON.stringify({error: 'all methods failed', file: fileName, url: url});
    })()
    """)

    pdf_data = json.loads(pdf_result) if isinstance(pdf_result, str) else pdf_result
    if pdf_data.get('dataUrl'):
        print(f"  File: {pdf_data.get('file')}")
        print(f"  Size: {pdf_data.get('size', 0) / 1024:.1f} KB")
        print(f"  Method: {pdf_data.get('method')}")
        # Save
        data_url = pdf_data['dataUrl']
        b64_part = data_url.split(',', 1)[1] if ',' in data_url else data_url
        file_bytes = base64.b64decode(b64_part)
        out_path = f"output/test-download/{pdf_data['file']}"
        with open(out_path, 'wb') as f:
            f.write(file_bytes)
        print(f"  SAVED: {out_path} ({len(file_bytes) / 1024:.1f} KB)")
    elif pdf_data.get('apiResponse'):
        print(f"  API returned JSON: {json.dumps(pdf_data['apiResponse'])[:300]}")
    else:
        print(f"  FAILED: {pdf_data}")
        # Last resort: just trigger the native click download
        print("  Trying native CDP click on resource element...")
        pos = await run_js(s, """
        (function() {
            var w = document.querySelector('[class*="ResourceWrapper"]');
            if (!w) return JSON.stringify({error: 'no wrapper'});
            var r = w.getBoundingClientRect();
            window.scrollTo(0, r.y - 200);
            return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2 - window.scrollY)});
        })()
        """)
        p = json.loads(pos) if isinstance(pos, str) else pos
        if p.get('x'):
            await asyncio.sleep(1)
            await s.click_cdp(p['x'], p['y'])
            await asyncio.sleep(3)
            print(f"  Clicked at ({p['x']}, {p['y']}) - check browser downloads folder")

    # =========================================================
    # Summary
    # =========================================================
    print(f"\n{'='*60}")
    print("OUTPUT FILES")
    print("=" * 60)
    for f_name in sorted(os.listdir("output/test-download")):
        fpath = os.path.join("output/test-download", f_name)
        size = os.path.getsize(fpath)
        if size > 1024 * 1024:
            print(f"  {f_name} ({size / (1024*1024):.1f} MB)")
        else:
            print(f"  {f_name} ({size / 1024:.1f} KB)")

    await s.stop()

asyncio.run(main())
