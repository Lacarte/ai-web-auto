"""
Test video download - scroll to trigger video player load, then resolve videoId via API.
Reuses same tab.
"""
import asyncio
import json
import sys
import os
import base64

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

    lesson_url = "https://www.skool.com/ai-video-creators/classroom/e22d16af?md=5ffde8748ff6467e95168c2b01de3385"
    print(f"=== Navigate to lesson (same tab) ===")
    await s.navigate(lesson_url)
    await asyncio.sleep(5)

    # Scroll down to trigger lazy video load
    print("\n=== Scroll to video area ===")
    for i in range(5):
        await s.scroll("down", 500)
        await asyncio.sleep(1)

    # Scroll back to top
    await run_js(s, "window.scrollTo(0, 0)")
    await asyncio.sleep(2)

    # Now check for video elements again
    print("\n=== Check for video player after scroll ===")
    check = await run_js(s, """
    (function() {
        var result = {
            videos: document.querySelectorAll('video').length,
            muxPlayers: document.querySelectorAll('mux-player').length,
            blobVideos: document.querySelectorAll('video[src^="blob:"]').length,
            allIframes: [],
            videoElements: []
        };

        // Check ALL iframes more carefully
        var iframes = document.querySelectorAll('iframe');
        for (var i = 0; i < iframes.length; i++) {
            var src = iframes[i].src || '';
            if (!src.includes('stripe.com')) {
                result.allIframes.push({
                    src: src.substring(0, 200),
                    w: iframes[i].offsetWidth,
                    h: iframes[i].offsetHeight
                });
            }
        }

        // Check for any video element anywhere
        var vids = document.querySelectorAll('video');
        for (var j = 0; j < vids.length; j++) {
            result.videoElements.push({
                src: (vids[j].src || '').substring(0, 200),
                currentSrc: (vids[j].currentSrc || '').substring(0, 200),
                poster: (vids[j].poster || '').substring(0, 200),
                readyState: vids[j].readyState,
                paused: vids[j].paused,
                w: vids[j].offsetWidth,
                h: vids[j].offsetHeight
            });
        }

        // Look for mux-player or any custom video elements
        var customs = document.querySelectorAll('mux-player, media-controller, video-js, .video-player, [class*="video"], [class*="Video"], [class*="player"], [class*="Player"]');
        result.customVideoElements = [];
        for (var k = 0; k < Math.min(customs.length, 10); k++) {
            var el = customs[k];
            result.customVideoElements.push({
                tag: el.tagName.toLowerCase(),
                class: (el.className || '').substring(0, 80),
                id: el.id || '',
                w: el.offsetWidth,
                h: el.offsetHeight,
                innerHTML: el.innerHTML.substring(0, 200)
            });
        }

        return JSON.stringify(result);
    })()
    """)
    data = json.loads(check) if isinstance(check, str) else check
    print(f"  Videos: {data.get('videos')}, Mux: {data.get('muxPlayers')}, Blob: {data.get('blobVideos')}")
    print(f"  Non-Stripe iframes: {len(data.get('allIframes', []))}")
    for f in data.get('allIframes', []):
        print(f"    {f.get('w')}x{f.get('h')} src={f.get('src')}")
    print(f"  Video elements: {len(data.get('videoElements', []))}")
    for v in data.get('videoElements', []):
        print(f"    {v.get('w')}x{v.get('h')} src={v.get('src')} state={v.get('readyState')}")
    print(f"  Custom video elements: {len(data.get('customVideoElements', []))}")
    for c in data.get('customVideoElements', []):
        print(f"    <{c.get('tag')}> class='{c.get('class')}' {c.get('w')}x{c.get('h')}")
        if c.get('innerHTML'):
            print(f"      html: {c.get('innerHTML')[:150]}")

    # Try the Skool API to resolve videoId
    print("\n=== Try Skool API to resolve videoId ===")
    api_result = await run_js(s, """
    (async function() {
        var videoId = '8d8f150aacb340319df4650025818fa6';
        var results = {};

        // Try api2.skool.com endpoints
        var endpoints = [
            'https://api2.skool.com/video/' + videoId,
            'https://api2.skool.com/v1/video/' + videoId,
            'https://api2.skool.com/videos/' + videoId,
            'https://api2.skool.com/stream/' + videoId
        ];

        for (var i = 0; i < endpoints.length; i++) {
            try {
                var resp = await fetch(endpoints[i], {
                    credentials: 'include',
                    headers: {
                        'Accept': 'application/json',
                        'Origin': 'https://www.skool.com',
                        'Referer': 'https://www.skool.com/'
                    }
                });
                var text = await resp.text();
                results[endpoints[i]] = {
                    status: resp.status,
                    body: text.substring(0, 500)
                };
            } catch(e) {
                results[endpoints[i]] = { error: e.message };
            }
        }

        // Also try stream.video.skool.com with the videoId
        var streamUrls = [
            'https://stream.video.skool.com/' + videoId + '.m3u8',
            'https://stream.video.skool.com/' + videoId + '/high.m3u8'
        ];
        for (var j = 0; j < streamUrls.length; j++) {
            try {
                var resp2 = await fetch(streamUrls[j], {
                    credentials: 'include',
                    headers: {
                        'Origin': 'https://www.skool.com',
                        'Referer': 'https://www.skool.com/'
                    }
                });
                results[streamUrls[j]] = {
                    status: resp2.status,
                    contentType: resp2.headers.get('content-type'),
                    body: (await resp2.text()).substring(0, 500)
                };
            } catch(e) {
                results[streamUrls[j]] = { error: e.message };
            }
        }

        // Try Mux with the stream key
        var muxUrl = 'https://stream.mux.com/' + videoId + '.m3u8';
        try {
            var resp3 = await fetch(muxUrl);
            results[muxUrl] = {
                status: resp3.status,
                contentType: resp3.headers.get('content-type'),
                body: (await resp3.text()).substring(0, 500)
            };
        } catch(e) {
            results[muxUrl] = { error: e.message };
        }

        return JSON.stringify(results);
    })()
    """)

    api = json.loads(api_result) if isinstance(api_result, str) else api_result
    os.makedirs("output/discover", exist_ok=True)
    with open("output/discover/skool-video-api-results.json", "w", encoding="utf-8") as f:
        json.dump(api, f, indent=2, ensure_ascii=False)

    m3u8_found = None
    for url, resp in api.items():
        status = resp.get('status', 'error')
        body = resp.get('body', resp.get('error', ''))[:150]
        ct = resp.get('contentType', '')
        print(f"  [{status}] {url[:80]}")
        if body:
            print(f"    -> {body}")
        if status == 200 and ('.m3u8' in url or 'mpegurl' in ct.lower()):
            m3u8_found = url
            print(f"    *** M3U8 FOUND! ***")

    if m3u8_found:
        print(f"\n=== Attempt ffmpeg download: {m3u8_found} ===")
        os.makedirs("output/test-video", exist_ok=True)
        out_path = "output/test-video/test_lesson.mp4"
        proc = await asyncio.create_subprocess_exec(
            'ffmpeg', '-y',
            '-headers', 'Referer: https://www.skool.com/\r\nOrigin: https://www.skool.com',
            '-i', m3u8_found,
            '-c', 'copy', '-bsf:a', 'aac_adtstoasc',
            '-t', '30',
            out_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode == 0 and os.path.exists(out_path):
            size = os.path.getsize(out_path) / (1024*1024)
            print(f"  SUCCESS: Downloaded {size:.1f} MB -> {out_path}")
        else:
            print(f"  ffmpeg failed: {stderr.decode()[:400]}")
    else:
        print("\n  No M3U8 stream found via direct API calls.")
        print("  The video player may load dynamically - check screenshot for visual state.")

    # Final screenshot
    resp = await s.screenshot()
    if resp.data.screenshot:
        img = resp.data.screenshot
        if img.startswith("data:"): img = img.split(",", 1)[1]
        with open("output/test-video-lesson2.png", "wb") as f2:
            f2.write(base64.b64decode(img))
        print(f"  Screenshot: output/test-video-lesson2.png")

    print("\n=== DONE ===")
    await s.stop()


if __name__ == "__main__":
    asyncio.run(main())
