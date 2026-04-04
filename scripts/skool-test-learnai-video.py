"""
Download both video types from learn-ai:
1. Skool native (Welcome to Your Future) - click play -> mux-player -> M3U8 -> ffmpeg
2. YouTube (The Opportunity of a Lifetime) - videoLink -> yt-dlp
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
    os.makedirs("output/test-download", exist_ok=True)

    # =========================================================
    # 1. Skool native video
    # =========================================================
    print("=" * 60)
    print("1. Skool native video: Welcome to Your Future")
    print("=" * 60)
    await s.navigate("https://www.skool.com/learn-ai/classroom/2aaae143?md=11cc536199c94c1cbd792db79f46bd1d")
    await asyncio.sleep(6)

    # Click the play button
    play_pos = await run_js(s, """
    (function() {
        var dur = document.querySelector('[class*="VideoDuration"]');
        if (dur) {
            var el = dur;
            for (var i = 0; i < 10; i++) {
                el = el.parentElement;
                if (!el) break;
                var r = el.getBoundingClientRect();
                if (r.width > 300 && r.height > 150) {
                    return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
                }
            }
        }
        // Fallback: look for cover image
        var cover = document.querySelector('[class*="CoverImage"], [class*="VideoPlayer"]');
        if (cover) {
            var r = cover.getBoundingClientRect();
            return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
        }
        return JSON.stringify({x: 0, y: 0});
    })()
    """)
    pos = json.loads(play_pos) if isinstance(play_pos, str) else play_pos
    print(f"  Clicking play at ({pos['x']}, {pos['y']})")
    await s.click_cdp(pos['x'], pos['y'])
    await asyncio.sleep(4)

    # Wait for mux-player and capture M3U8
    m3u8_url = None
    for attempt in range(15):
        await asyncio.sleep(2)
        check = await run_js(s, """
        (function() {
            var mux = document.querySelector('mux-player');
            var urls = [];
            if (window.performance) {
                var entries = window.performance.getEntries();
                for (var i = 0; i < entries.length; i++) {
                    if (entries[i].name.includes('.m3u8')) urls.push(entries[i].name);
                }
            }
            return JSON.stringify({
                hasMux: !!mux,
                playbackId: mux ? mux.getAttribute('playback-id') : null,
                customDomain: mux ? mux.getAttribute('custom-domain') : null,
                m3u8: urls
            });
        })()
        """)
        cd = json.loads(check) if isinstance(check, str) else check
        if cd.get('m3u8') and len(cd['m3u8']) > 0:
            m3u8_url = cd['m3u8'][0]
            print(f"  Mux player: playback-id={cd.get('playbackId')}, domain={cd.get('customDomain')}")
            print(f"  M3U8: {m3u8_url[:120]}...")
            break
        print(f"  Attempt {attempt+1}: waiting...")

    if m3u8_url:
        out = os.path.abspath("output/test-download/welcome_to_your_future.mp4")
        print(f"  Downloading via ffmpeg...")
        proc = await asyncio.create_subprocess_exec(
            'ffmpeg', '-y',
            '-headers', 'Referer: https://www.skool.com/\r\nOrigin: https://www.skool.com\r\n',
            '-i', m3u8_url,
            '-c', 'copy', '-bsf:a', 'aac_adtstoasc',
            out,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        if proc.returncode == 0 and os.path.exists(out):
            size = os.path.getsize(out) / (1024 * 1024)
            print(f"  SUCCESS: {size:.1f} MB -> {out}")
        else:
            print(f"  FAILED: {stderr.decode()[-400:]}")
    else:
        print("  No M3U8 found")

    # =========================================================
    # 2. YouTube video
    # =========================================================
    print("\n" + "=" * 60)
    print("2. YouTube video: The Opportunity of a Lifetime")
    print("=" * 60)
    await s.navigate("https://www.skool.com/learn-ai/classroom/2aaae143")
    await asyncio.sleep(4)

    # Get the YouTube URL from __NEXT_DATA__
    yt_url = await run_js(s, """
    (function() {
        var nd = document.getElementById('__NEXT_DATA__');
        var data = JSON.parse(nd.textContent);
        var pp = data.props.pageProps;
        if (!pp.course || !pp.course.children) return JSON.stringify({error: 'no children'});
        for (var i = 0; i < pp.course.children.length; i++) {
            var c = pp.course.children[i];
            if (!c.course || !c.course.metadata) continue;
            var vl = c.course.metadata.videoLink || '';
            if (vl.includes('youtube') || vl.includes('youtu.be')) {
                return JSON.stringify({
                    title: c.course.metadata.title,
                    videoLink: vl,
                    id: c.course.id
                });
            }
        }
        return JSON.stringify({error: 'no youtube lesson'});
    })()
    """)
    yt = json.loads(yt_url) if isinstance(yt_url, str) else yt_url
    print(f"  Title: {yt.get('title')}")
    print(f"  YouTube URL: {yt.get('videoLink')}")

    if yt.get('videoLink'):
        out = os.path.abspath("output/test-download/opportunity_of_a_lifetime.mp4")
        print(f"  Downloading via yt-dlp...")
        proc = await asyncio.create_subprocess_exec(
            'yt-dlp', '--no-warnings',
            '-o', out,
            '--merge-output-format', 'mp4',
            yt['videoLink'],
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
        if proc.returncode == 0 and os.path.exists(out):
            size = os.path.getsize(out) / (1024 * 1024)
            print(f"  SUCCESS: {size:.1f} MB -> {out}")
        else:
            print(f"  FAILED (exit={proc.returncode})")
            print(f"  stderr: {stderr.decode()[:400]}")

    # =========================================================
    # Summary
    # =========================================================
    print(f"\n{'='*60}")
    print("OUTPUT FILES")
    print("=" * 60)
    for f in sorted(os.listdir("output/test-download")):
        fp = os.path.join("output/test-download", f)
        size = os.path.getsize(fp)
        if size > 1024 * 1024:
            print(f"  {f} ({size/(1024*1024):.1f} MB)")
        else:
            print(f"  {f} ({size/1024:.1f} KB)")

    await s.stop()

asyncio.run(main())
