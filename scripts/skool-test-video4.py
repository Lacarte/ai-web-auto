"""
Final video download test - click play, get M3U8 with token, download via ffmpeg.
Same tab. Captures full ffmpeg output.
"""
import asyncio
import json
import sys
import os
import base64

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

    # Navigate (same tab)
    await s.navigate("https://www.skool.com/ai-video-creators/classroom/e22d16af?md=5ffde8748ff6467e95168c2b01de3385")
    await asyncio.sleep(5)

    # Find and click play button using the proven method (svg_play)
    print("=== Click play button ===")
    play = await run_js(s, """
    (function() {
        var durEl = document.querySelector('[class*="VideoDuration"]');
        if (durEl) {
            var container = durEl;
            for (var k = 0; k < 10; k++) {
                container = container.parentElement;
                if (!container) break;
                var r = container.getBoundingClientRect();
                if (r.width > 300 && r.height > 150) {
                    return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
                }
            }
        }
        return JSON.stringify({x: 0, y: 0});
    })()
    """)
    coords = json.loads(play) if isinstance(play, str) else play
    print(f"  Clicking at ({coords['x']}, {coords['y']})")
    await s.click_cdp(coords['x'], coords['y'])
    await asyncio.sleep(4)

    # Wait for mux-player and capture M3U8 URL
    print("\n=== Capture M3U8 stream URL ===")
    for attempt in range(15):
        await asyncio.sleep(2)
        result = await run_js(s, """
        (function() {
            var mux = document.querySelector('mux-player');
            var urls = [];
            if (window.performance && window.performance.getEntries) {
                var entries = window.performance.getEntries();
                for (var i = 0; i < entries.length; i++) {
                    var name = entries[i].name || '';
                    if (name.includes('.m3u8')) {
                        urls.push(name);
                    }
                }
            }
            return JSON.stringify({
                hasMux: !!mux,
                playbackId: mux ? mux.getAttribute('playback-id') : null,
                customDomain: mux ? mux.getAttribute('custom-domain') : null,
                m3u8Urls: urls
            });
        })()
        """)
        data = json.loads(result) if isinstance(result, str) else result
        if data.get('m3u8Urls') and len(data['m3u8Urls']) > 0:
            print(f"  Mux player: {data.get('hasMux')}")
            print(f"  Playback ID: {data.get('playbackId')}")
            print(f"  Custom domain: {data.get('customDomain')}")
            print(f"  M3U8 URLs found: {len(data['m3u8Urls'])}")
            for u in data['m3u8Urls']:
                print(f"    {u[:200]}")
            break
        print(f"  Attempt {attempt+1}: waiting for video stream...")
    else:
        print("  Timeout waiting for M3U8 stream")
        await s.stop()
        return

    # Use the master M3U8 URL (first one with token)
    m3u8_url = data['m3u8Urls'][0]
    print(f"\n=== Download with ffmpeg ===")
    print(f"  M3U8: {m3u8_url[:150]}...")

    os.makedirs("output/test-video", exist_ok=True)
    out_path = os.path.abspath("output/test-video/test_lesson.mp4")

    proc = await asyncio.create_subprocess_exec(
        'ffmpeg', '-y',
        '-headers', 'Referer: https://www.skool.com/\r\nOrigin: https://www.skool.com\r\n',
        '-i', m3u8_url,
        '-c', 'copy',
        '-bsf:a', 'aac_adtstoasc',
        '-t', '60',
        out_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        stderr_text = stderr.decode('utf-8', errors='replace')

        if proc.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
            size = os.path.getsize(out_path) / (1024 * 1024)
            print(f"\n  SUCCESS: Downloaded {size:.1f} MB")
            print(f"  File: {out_path}")
        else:
            print(f"\n  FAILED (exit code {proc.returncode})")
            # Show last 500 chars of stderr for the actual error
            print(f"  stderr (last 600 chars):")
            print(f"  {stderr_text[-600:]}")

    except asyncio.TimeoutError:
        print("  Timed out after 120s")
        proc.kill()

    print("\n=== DONE ===")
    await s.stop()


if __name__ == "__main__":
    asyncio.run(main())
