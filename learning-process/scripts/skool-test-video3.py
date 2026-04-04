"""
Click the play button on Skool lesson video, wait for player to load,
then capture the stream URL and download.
Same tab throughout.
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


async def save_ss(s, name):
    resp = await s.screenshot()
    if resp.data.screenshot:
        img = resp.data.screenshot
        if img.startswith("data:"): img = img.split(",", 1)[1]
        with open(f"output/{name}.png", "wb") as f:
            f.write(base64.b64decode(img))
        print(f"  Screenshot: output/{name}.png")


async def main():
    s = WebSocketServer()
    await s.start()
    await s.wait_for_connection(timeout=60)
    print("Connected!\n")

    # Navigate to lesson (same tab)
    print("=== Navigate to lesson ===")
    await s.navigate("https://www.skool.com/ai-video-creators/classroom/e22d16af?md=5ffde8748ff6467e95168c2b01de3385")
    await asyncio.sleep(5)

    # Find and click the play button / video thumbnail
    print("\n=== Find video play button ===")
    play_info = await run_js(s, """
    (function() {
        // Look for the video cover/play button area
        // From screenshot: it's a thumbnail with a play icon
        var result = {found: false};

        // Method 1: Look for CoverImage or play button styled components
        var covers = document.querySelectorAll(
            '[class*="CoverImage"], [class*="coverImage"], [class*="VideoPlayer"], ' +
            '[class*="videoPlayer"], [class*="VideoWrapper"], [class*="videoWrapper"], ' +
            '[class*="PlayButton"], [class*="playButton"], [class*="play-button"]'
        );
        for (var i = 0; i < covers.length; i++) {
            var rect = covers[i].getBoundingClientRect();
            if (rect.width > 100 && rect.height > 100) {
                result.found = true;
                result.method = 'class_match';
                result.x = Math.round(rect.x + rect.width / 2);
                result.y = Math.round(rect.y + rect.height / 2);
                result.w = Math.round(rect.width);
                result.h = Math.round(rect.height);
                result.class = covers[i].className.substring(0, 100);
                result.tag = covers[i].tagName;
                break;
            }
        }

        // Method 2: Look for SVG play icon
        if (!result.found) {
            var svgs = document.querySelectorAll('svg');
            for (var j = 0; j < svgs.length; j++) {
                var paths = svgs[j].querySelectorAll('path');
                // Play icon typically has a triangle path
                if (paths.length <= 3) {
                    var parent = svgs[j].parentElement;
                    var pRect = parent ? parent.getBoundingClientRect() : null;
                    // Check if it's in the video area (right side of the page, large enough)
                    if (pRect && pRect.width > 50 && pRect.x > 200) {
                        var text = svgs[j].parentElement.textContent.trim();
                        if (text.length < 5) { // Play buttons don't have much text
                            result.found = true;
                            result.method = 'svg_play';
                            result.x = Math.round(pRect.x + pRect.width / 2);
                            result.y = Math.round(pRect.y + pRect.height / 2);
                            result.w = Math.round(pRect.width);
                            result.h = Math.round(pRect.height);
                            break;
                        }
                    }
                }
            }
        }

        // Method 3: Look for the video duration element and find its parent container
        if (!result.found) {
            var durEl = document.querySelector('[class*="VideoDuration"]');
            if (durEl) {
                // Walk up to find the video container
                var container = durEl;
                for (var k = 0; k < 10; k++) {
                    container = container.parentElement;
                    if (!container) break;
                    var cRect = container.getBoundingClientRect();
                    if (cRect.width > 300 && cRect.height > 150) {
                        result.found = true;
                        result.method = 'duration_parent';
                        result.x = Math.round(cRect.x + cRect.width / 2);
                        result.y = Math.round(cRect.y + cRect.height / 2);
                        result.w = Math.round(cRect.width);
                        result.h = Math.round(cRect.height);
                        result.class = container.className.substring(0, 100);
                        break;
                    }
                }
            }
        }

        return JSON.stringify(result);
    })()
    """)
    pi = json.loads(play_info) if isinstance(play_info, str) else play_info
    print(f"  Found: {pi.get('found')}, Method: {pi.get('method')}")
    print(f"  Position: ({pi.get('x')}, {pi.get('y')}), Size: {pi.get('w')}x{pi.get('h')}")

    if pi.get('found'):
        print(f"\n=== Click video at ({pi['x']}, {pi['y']}) ===")
        await s.click_cdp(pi['x'], pi['y'])
        await asyncio.sleep(3)
        await save_ss(s, "test-video-after-click")

        # Wait a bit for the video player to initialize
        print("\n=== Wait for video player to load ===")
        for attempt in range(10):
            await asyncio.sleep(2)
            check = await run_js(s, """
            (function() {
                var result = {
                    videos: document.querySelectorAll('video').length,
                    muxPlayers: document.querySelectorAll('mux-player').length,
                    blobVideos: document.querySelectorAll('video[src^="blob:"]').length,
                    iframesNonStripe: 0
                };
                var iframes = document.querySelectorAll('iframe');
                for (var i = 0; i < iframes.length; i++) {
                    if (!iframes[i].src.includes('stripe.com')) {
                        result.iframesNonStripe++;
                        result.iframeSrc = iframes[i].src.substring(0, 200);
                    }
                }
                // Check for mux-player attributes
                var mux = document.querySelector('mux-player');
                if (mux) {
                    result.playbackId = mux.getAttribute('playback-id');
                    result.customDomain = mux.getAttribute('custom-domain');
                }
                // Check video src
                var vid = document.querySelector('video');
                if (vid) {
                    result.videoSrc = (vid.src || '').substring(0, 200);
                    result.videoCurrentSrc = (vid.currentSrc || '').substring(0, 200);
                }
                return JSON.stringify(result);
            })()
            """)
            cd = json.loads(check) if isinstance(check, str) else check
            print(f"  Attempt {attempt+1}: videos={cd.get('videos')} mux={cd.get('muxPlayers')} blob={cd.get('blobVideos')} iframes={cd.get('iframesNonStripe')}")

            if cd.get('muxPlayers', 0) > 0:
                print(f"    Mux playback-id: {cd.get('playbackId')}")
                print(f"    Mux custom-domain: {cd.get('customDomain')}")
                break
            if cd.get('videos', 0) > 0:
                print(f"    Video src: {cd.get('videoSrc')}")
                print(f"    Video currentSrc: {cd.get('videoCurrentSrc')}")
                break
            if cd.get('iframesNonStripe', 0) > 0:
                print(f"    Iframe src: {cd.get('iframeSrc')}")
                break

        await save_ss(s, "test-video-player-loaded")

        # Now check performance entries for the actual stream URL
        print("\n=== Check network requests for stream URL ===")
        net_result = await run_js(s, """
        (function() {
            var urls = [];
            if (window.performance && window.performance.getEntries) {
                var entries = window.performance.getEntries();
                for (var i = 0; i < entries.length; i++) {
                    var name = entries[i].name || '';
                    if (name.includes('.m3u8') || name.includes('.ts') || name.includes('.mp4') ||
                        name.includes('b-cdn.net') || name.includes('mux.com') || name.includes('stream.video') ||
                        name.includes('playback') || name.includes('.mpd')) {
                        urls.push(name.substring(0, 300));
                    }
                }
            }
            return JSON.stringify({urls: urls});
        })()
        """)
        net = json.loads(net_result) if isinstance(net_result, str) else net_result
        print(f"  Stream-related network requests: {len(net.get('urls', []))}")
        m3u8_url = None
        for u in net.get('urls', []):
            print(f"    {u[:200]}")
            if '.m3u8' in u and not m3u8_url:
                m3u8_url = u

        if m3u8_url:
            print(f"\n=== Download video via ffmpeg ===")
            print(f"  M3U8: {m3u8_url[:150]}")
            os.makedirs("output/test-video", exist_ok=True)
            out_path = "output/test-video/test_lesson.mp4"
            try:
                proc = await asyncio.create_subprocess_exec(
                    'ffmpeg', '-y',
                    '-headers', 'Referer: https://www.skool.com/\r\nOrigin: https://www.skool.com',
                    '-i', m3u8_url,
                    '-c', 'copy', '-bsf:a', 'aac_adtstoasc',
                    '-t', '30',
                    out_path,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
                if proc.returncode == 0 and os.path.exists(out_path):
                    size = os.path.getsize(out_path) / (1024 * 1024)
                    print(f"  SUCCESS: {size:.1f} MB -> {out_path}")
                else:
                    print(f"  ffmpeg failed: {stderr.decode()[:400]}")
            except FileNotFoundError:
                print("  ffmpeg not in PATH")
            except asyncio.TimeoutError:
                print("  ffmpeg timed out")
        else:
            print("\n  No M3U8 found in network requests yet.")

    print("\n=== DONE ===")
    await s.stop()


if __name__ == "__main__":
    asyncio.run(main())
