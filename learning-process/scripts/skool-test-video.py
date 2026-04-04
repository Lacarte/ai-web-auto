"""
Test video extraction and download from a Skool lesson.
Reuses same tab. Investigates iframes and videoId to find the actual video URL.
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
    print("Waiting for extension...")
    await s.wait_for_connection(timeout=60)
    print("Connected!\n")

    # Navigate to the first lesson with a videoId (same tab)
    lesson_url = "https://www.skool.com/ai-video-creators/classroom/e22d16af?md=5ffde8748ff6467e95168c2b01de3385"
    print(f"=== Navigate to lesson (same tab): {lesson_url} ===")
    await s.navigate(lesson_url)
    await asyncio.sleep(6)

    # Step 1: Investigate all iframes and video elements
    print("\n=== Step 1: Investigate video sources ===")
    video_info = await run_js(s, """
    (function() {
        var result = {
            iframes: [],
            videos: [],
            muxPlayers: [],
            nextDataVideo: null,
            scripts: []
        };

        // Check all iframes
        var iframes = document.querySelectorAll('iframe');
        for (var i = 0; i < iframes.length; i++) {
            var f = iframes[i];
            result.iframes.push({
                src: f.src || f.getAttribute('src') || '',
                width: f.width || f.getAttribute('width') || '',
                height: f.height || f.getAttribute('height') || '',
                className: (f.className || '').substring(0, 80),
                id: f.id || '',
                allow: f.getAttribute('allow') || '',
                parentClass: f.parentElement ? (f.parentElement.className || '').substring(0, 80) : ''
            });
        }

        // Check all video elements
        var videos = document.querySelectorAll('video');
        for (var j = 0; j < videos.length; j++) {
            var v = videos[j];
            result.videos.push({
                src: v.src || '',
                currentSrc: v.currentSrc || '',
                poster: v.poster || '',
                className: (v.className || '').substring(0, 80),
                sources: Array.from(v.querySelectorAll('source')).map(function(s) { return s.src; })
            });
        }

        // Check mux-player
        var muxes = document.querySelectorAll('mux-player');
        for (var k = 0; k < muxes.length; k++) {
            var m = muxes[k];
            result.muxPlayers.push({
                playbackId: m.getAttribute('playback-id'),
                customDomain: m.getAttribute('custom-domain'),
                streamType: m.getAttribute('stream-type'),
                src: m.getAttribute('src') || ''
            });
        }

        // Extract video info from __NEXT_DATA__
        var ndEl = document.getElementById('__NEXT_DATA__');
        if (ndEl) {
            try {
                var nd = JSON.parse(ndEl.textContent);
                var pp = nd.props.pageProps;
                var selMod = pp.selectedModule;
                if (pp.course && pp.course.children) {
                    for (var ci = 0; ci < pp.course.children.length; ci++) {
                        var child = pp.course.children[ci];
                        if (child.course && child.course.id === selMod) {
                            var meta = child.course.metadata || {};
                            result.nextDataVideo = {
                                id: child.course.id,
                                title: meta.title,
                                videoId: meta.videoId || null,
                                videoLink: meta.videoLink || null,
                                videoStream: meta.videoStream || null,
                                videoLenMs: meta.videoLenMs || null,
                                videoThumbnail: meta.videoThumbnail || null,
                                allMetaKeys: Object.keys(meta)
                            };
                            break;
                        }
                    }
                }
            } catch(e) {
                result.nextDataError = e.message;
            }
        }

        // Look for any script tags with video URLs (m3u8, mp4, etc.)
        var scriptTags = document.querySelectorAll('script');
        for (var si = 0; si < scriptTags.length; si++) {
            var content = scriptTags[si].textContent || '';
            if (content.includes('.m3u8') || content.includes('stream.') || content.includes('playback') || content.includes('mux.com')) {
                // Extract just the relevant URL parts
                var m3u8Match = content.match(/https?:[^"'\\s]+\\.m3u8[^"'\\s]*/);
                var muxMatch = content.match(/https?:[^"'\\s]*mux\\.com[^"'\\s]*/);
                var streamMatch = content.match(/https?:[^"'\\s]*stream[^"'\\s]*/);
                if (m3u8Match || muxMatch || streamMatch) {
                    result.scripts.push({
                        m3u8: m3u8Match ? m3u8Match[0] : null,
                        mux: muxMatch ? muxMatch[0] : null,
                        stream: streamMatch ? streamMatch[0] : null
                    });
                }
            }
        }

        return JSON.stringify(result);
    })()
    """)

    vi = json.loads(video_info) if isinstance(video_info, str) else video_info
    os.makedirs("output/discover", exist_ok=True)
    with open("output/discover/skool-video-sources.json", "w", encoding="utf-8") as f:
        json.dump(vi, f, indent=2, ensure_ascii=False)

    print(f"  Iframes: {len(vi.get('iframes', []))}")
    for iframe in vi.get('iframes', []):
        print(f"    src: {iframe.get('src', 'none')[:120]}")
        print(f"    allow: {iframe.get('allow', '')[:80]}")
        print(f"    parent: {iframe.get('parentClass', '')[:60]}")

    print(f"  Videos: {len(vi.get('videos', []))}")
    for v in vi.get('videos', []):
        print(f"    src: {v.get('src', 'none')[:120]}")
        print(f"    currentSrc: {v.get('currentSrc', 'none')[:120]}")

    print(f"  Mux players: {len(vi.get('muxPlayers', []))}")
    for m in vi.get('muxPlayers', []):
        print(f"    playback-id: {m.get('playbackId')}")
        print(f"    custom-domain: {m.get('customDomain')}")

    nd_vid = vi.get('nextDataVideo')
    if nd_vid:
        print(f"  __NEXT_DATA__ video:")
        print(f"    videoId: {nd_vid.get('videoId')}")
        print(f"    videoLink: {nd_vid.get('videoLink')}")
        print(f"    videoStream: {nd_vid.get('videoStream')}")
        print(f"    videoLenMs: {nd_vid.get('videoLenMs')}")
        print(f"    all meta keys: {nd_vid.get('allMetaKeys')}")

    print(f"  Script tag matches: {len(vi.get('scripts', []))}")
    for sc in vi.get('scripts', []):
        if sc.get('m3u8'): print(f"    m3u8: {sc['m3u8'][:120]}")
        if sc.get('mux'): print(f"    mux: {sc['mux'][:120]}")
        if sc.get('stream'): print(f"    stream: {sc['stream'][:120]}")

    # Step 2: Try to find the video URL from the videoId
    video_id = nd_vid.get('videoId') if nd_vid else None
    if video_id:
        print(f"\n=== Step 2: Resolve videoId '{video_id}' ===")

        # Check if any iframe src contains the videoId or a video player URL
        video_url = None
        m3u8_url = None

        for iframe in vi.get('iframes', []):
            src = iframe.get('src', '')
            if video_id in src or 'player' in src.lower() or 'video' in src.lower() or 'loom' in src.lower() or 'youtube' in src.lower() or 'vimeo' in src.lower():
                video_url = src
                break

        if video_url:
            print(f"  Found video iframe: {video_url[:150]}")
        else:
            print("  No video iframe found with videoId")

        # Try common Skool video URL patterns
        # Skool uses their own video hosting: assets might be at stream.video.skool.com
        # or the videoId could be a Skool-internal reference
        print("  Trying known URL patterns...")

        test_urls = await run_js(s, f"""
        (function() {{
            var videoId = '{video_id}';
            var results = [];

            // Pattern 1: Skool stream domain
            var urls = [
                'https://stream.video.skool.com/' + videoId + '.m3u8',
                'https://stream.video.skool.com/' + videoId + '/master.m3u8',
                'https://assets.skool.com/video/' + videoId,
                'https://stream.mux.com/' + videoId + '.m3u8'
            ];

            // Also check if there's a video API we can hit
            // Try fetching the Skool video API
            return JSON.stringify({{urls: urls, videoId: videoId}});
        }})()
        """)
        url_data = json.loads(test_urls) if isinstance(test_urls, str) else test_urls
        print(f"  Candidate URLs to test:")
        for u in url_data.get('urls', []):
            print(f"    {u}")

        # Step 3: Try to fetch a Skool video API endpoint
        print(f"\n=== Step 3: Check Skool video API ===")
        api_result = await run_js(s, f"""
        (function() {{
            var videoId = '{video_id}';
            var result = {{fetched: []}};

            // Try fetching video info from various endpoints
            var endpoints = [
                '/api/video/' + videoId,
                '/api/v1/video/' + videoId
            ];

            // Also look at network requests that might reveal the pattern
            // Check if there's a global video player config
            if (window.__NEXT_DATA__) {{
                var pp = window.__NEXT_DATA__.props.pageProps;
                if (pp.env) {{
                    result.apiUrl = pp.env.API_URL;
                    result.streamKey = pp.env.STREAM_CLIENT_KEY;
                }}
            }}

            // Check for Loom or other embedded players in iframes
            var iframes = document.querySelectorAll('iframe');
            for (var i = 0; i < iframes.length; i++) {{
                var src = iframes[i].src || '';
                if (src && src.length > 10) {{
                    result.fetched.push({{src: src, type: 'iframe'}});
                }}
            }}

            // Check for video.js or similar player init
            if (window.videojs) result.hasVideoJs = true;
            if (window.Hls) result.hasHls = true;
            if (window.mux) result.hasMux = true;

            return JSON.stringify(result);
        }})()
        """)
        api = json.loads(api_result) if isinstance(api_result, str) else api_result
        print(f"  API URL: {api.get('apiUrl')}")
        print(f"  Stream key: {api.get('streamKey')}")
        print(f"  Has video.js: {api.get('hasVideoJs')}")
        print(f"  Has HLS.js: {api.get('hasHls')}")
        print(f"  Has Mux: {api.get('hasMux')}")
        for f_item in api.get('fetched', []):
            print(f"  Iframe src: {f_item.get('src', '')[:150]}")

        with open("output/discover/skool-video-api.json", "w", encoding="utf-8") as f:
            json.dump(api, f, indent=2, ensure_ascii=False)

    # Step 4: Use CDP to intercept network requests and find video stream
    print(f"\n=== Step 4: Check for video stream in page via CDP Runtime ===")
    stream_result = await run_js(s, """
    (function() {
        var result = {urls: []};

        // Check performance entries for video-related requests
        if (window.performance && window.performance.getEntries) {
            var entries = window.performance.getEntries();
            for (var i = 0; i < entries.length; i++) {
                var name = entries[i].name || '';
                if (name.includes('.m3u8') || name.includes('.mp4') || name.includes('.ts') ||
                    name.includes('stream') || name.includes('video') || name.includes('mux') ||
                    name.includes('playback') || name.includes('b-cdn')) {
                    result.urls.push({
                        url: name.substring(0, 200),
                        type: entries[i].initiatorType || '',
                        duration: Math.round(entries[i].duration || 0)
                    });
                }
            }
        }

        return JSON.stringify(result);
    })()
    """)
    streams = json.loads(stream_result) if isinstance(stream_result, str) else stream_result
    print(f"  Performance entries with video/stream: {len(streams.get('urls', []))}")
    for u in streams.get('urls', []):
        print(f"    [{u.get('type')}] {u.get('url')}")

    # Step 5: If we found a downloadable URL, try downloading
    download_url = None

    # Check performance entries for m3u8
    for u in streams.get('urls', []):
        url = u.get('url', '')
        if '.m3u8' in url:
            download_url = url
            break
        if '.mp4' in url and 'stream' in url:
            download_url = url
            break

    # Check iframe sources
    if not download_url:
        for iframe in vi.get('iframes', []):
            src = iframe.get('src', '')
            if 'loom.com' in src or 'youtube.com' in src or 'vimeo.com' in src:
                download_url = src
                break

    if download_url:
        print(f"\n=== Step 5: Attempt download ===")
        print(f"  URL: {download_url[:200]}")

        # Try ffmpeg or yt-dlp
        import subprocess
        os.makedirs("output/test-video", exist_ok=True)
        out_path = "output/test-video/test_lesson.mp4"

        if '.m3u8' in download_url:
            print("  Trying ffmpeg...")
            proc = await asyncio.create_subprocess_exec(
                'ffmpeg', '-y',
                '-headers', 'Referer: https://www.skool.com/\r\nOrigin: https://www.skool.com',
                '-i', download_url,
                '-c', 'copy', '-bsf:a', 'aac_adtstoasc',
                '-t', '30',  # Only first 30 seconds for test
                out_path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            if proc.returncode == 0 and os.path.exists(out_path):
                size = os.path.getsize(out_path) / (1024*1024)
                print(f"  SUCCESS: Downloaded {size:.1f} MB -> {out_path}")
            else:
                print(f"  ffmpeg failed: {stderr.decode()[:300]}")
        elif 'loom.com' in download_url or 'youtube.com' in download_url or 'vimeo.com' in download_url:
            print("  Trying yt-dlp...")
            try:
                proc = await asyncio.create_subprocess_exec(
                    'yt-dlp', '--no-warnings',
                    '-o', out_path,
                    '--merge-output-format', 'mp4',
                    download_url,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
                if proc.returncode == 0 and os.path.exists(out_path):
                    size = os.path.getsize(out_path) / (1024*1024)
                    print(f"  SUCCESS: Downloaded {size:.1f} MB -> {out_path}")
                else:
                    print(f"  yt-dlp failed: {stderr.decode()[:300]}")
            except FileNotFoundError:
                print("  yt-dlp not in PATH")
        else:
            print(f"  Unknown URL type, saving for manual download")
    else:
        print(f"\n=== Step 5: No direct video URL found ===")
        print("  The video may require authentication or iframe inspection.")
        print("  Saved all findings to output/discover/skool-video-sources.json")

    # Save screenshot of the lesson page with video
    resp = await s.screenshot()
    if resp.data.screenshot:
        img = resp.data.screenshot
        if img.startswith("data:"): img = img.split(",", 1)[1]
        with open("output/test-video-lesson.png", "wb") as f2:
            f2.write(base64.b64decode(img))
        print(f"\n  Screenshot: output/test-video-lesson.png")

    print("\n=== VIDEO TEST COMPLETE ===")
    await s.stop()


if __name__ == "__main__":
    asyncio.run(main())
