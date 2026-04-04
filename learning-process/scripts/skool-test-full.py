"""
Full test of SKOOL-DOWN-COURSE extension logic.
Reuses the active tab for all navigation (no new tabs).
"""
import asyncio
import json
import sys
import os
import base64

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_web_auto_backend.core.server import WebSocketServer

# Force UTF-8 output on Windows
sys.stdout.reconfigure(encoding='utf-8', errors='replace')


async def run_js(s, script):
    resp = await s.execute_script(script)
    if resp and resp.data and resp.data.extracted_data:
        return resp.data.extracted_data.get("result", resp.data.extracted_data)
    return None


async def save_screenshot(s, name):
    resp = await s.screenshot()
    if resp.data.screenshot:
        img_data = resp.data.screenshot
        if img_data.startswith("data:"):
            img_data = img_data.split(",", 1)[1]
        os.makedirs("output", exist_ok=True)
        path = f"output/{name}.png"
        with open(path, "wb") as f:
            f.write(base64.b64decode(img_data))
        print(f"  Screenshot saved: {path}")
        return True
    return False


async def main():
    s = WebSocketServer()
    await s.start()
    print("Waiting for extension...")
    await s.wait_for_connection(timeout=60)
    print("Connected!\n")

    # =========================================================
    # TEST 1: Classroom page - scan modules from __NEXT_DATA__
    # =========================================================
    print("=" * 60)
    print("TEST 1: Scan classroom page (same tab)")
    print("=" * 60)
    await s.navigate("https://www.skool.com/ai-video-creators/classroom")
    await asyncio.sleep(5)
    await save_screenshot(s, "test-1-classroom")

    scan = await run_js(s, """
    (function() {
        var el = document.getElementById('__NEXT_DATA__');
        if (!el) return JSON.stringify({ok: false, error: 'no __NEXT_DATA__'});
        var nd = JSON.parse(el.textContent);
        var pp = nd.props.pageProps;
        var group = pp.currentGroup ? pp.currentGroup.name : '';
        var groupId = pp.currentGroup ? pp.currentGroup.id : '';
        var all = pp.allCourses || [];
        var modules = [];
        for (var i = 0; i < all.length; i++) {
            var c = all[i];
            var meta = c.metadata || {};
            modules.push({
                id: c.id,
                slug: c.name,
                title: meta.title || c.name,
                url: '/' + group + '/classroom/' + c.name
            });
        }
        return JSON.stringify({ok: true, group: group, groupId: groupId, modules: modules});
    })()
    """)
    scan_data = json.loads(scan) if isinstance(scan, str) else scan

    if scan_data.get('ok'):
        print(f"  PASS: Found {len(scan_data['modules'])} modules for group '{scan_data['group']}'")
        for m in scan_data['modules'][:5]:
            print(f"    - {m['title']} (slug={m['slug']})")
        if len(scan_data['modules']) > 5:
            print(f"    ... and {len(scan_data['modules']) - 5} more")
    else:
        print(f"  FAIL: {scan_data.get('error')}")
        await s.stop()
        return

    # =========================================================
    # TEST 2: Navigate into module (same tab)
    # =========================================================
    print("\n" + "=" * 60)
    print("TEST 2: Navigate into 'STEP 0 // Start here' (same tab)")
    print("=" * 60)

    # Pick STEP 0 module
    target_mod = None
    for m in scan_data['modules']:
        if 'Start here' in m['title'] or 'STEP 0' in m['title']:
            target_mod = m
            break
    if not target_mod:
        target_mod = scan_data['modules'][1] if len(scan_data['modules']) > 1 else scan_data['modules'][0]

    module_url = f"https://www.skool.com{target_mod['url']}"
    print(f"  Navigating to: {module_url}")
    await s.navigate(module_url)
    await asyncio.sleep(5)
    await save_screenshot(s, "test-2-module")

    # Extract lessons (handle FLAT structure)
    lessons_result = await run_js(s, """
    (function() {
        var el = document.getElementById('__NEXT_DATA__');
        if (!el) return JSON.stringify({ok: false, error: 'no __NEXT_DATA__'});
        var nd = JSON.parse(el.textContent);
        var pp = nd.props.pageProps;
        if (!pp.course || !pp.course.children) return JSON.stringify({ok: false, error: 'no course.children'});

        var groupId = pp.currentGroup ? pp.currentGroup.id : '';
        var group = pp.currentGroup ? pp.currentGroup.name : '';
        var lessons = [];

        for (var i = 0; i < pp.course.children.length; i++) {
            var child = pp.course.children[i];
            if (!child.course) continue;
            var meta = child.course.metadata || {};

            if (child.children && child.children.length > 0) {
                // NESTED
                for (var j = 0; j < child.children.length; j++) {
                    var sub = child.children[j];
                    if (!sub.course) continue;
                    var sm = sub.course.metadata || {};
                    lessons.push({
                        id: sub.course.id,
                        slug: sub.course.name,
                        title: sm.title || sub.course.name,
                        videoId: sm.videoId || null,
                        videoLink: sm.videoLink || null,
                        hasResources: !!sm.resources,
                        type: 'nested'
                    });
                }
            } else {
                // FLAT (verified structure)
                lessons.push({
                    id: child.course.id,
                    slug: child.course.name,
                    title: meta.title || child.course.name,
                    videoId: meta.videoId || null,
                    videoLink: meta.videoLink || null,
                    hasResources: !!meta.resources,
                    type: 'flat'
                });
            }
        }

        // Also verify DOM selectors
        var dom = {
            draggables: document.querySelectorAll('[data-rbd-draggable-id]').length,
            lessonLinks: document.querySelectorAll('a[href*="/classroom/"]').length,
            courseMenuTitle: null,
            muxPlayers: document.querySelectorAll('mux-player').length
        };
        var t = document.querySelector('[class*="CourseMenuTopTitleText"]');
        dom.courseMenuTitle = t ? t.textContent.trim() : null;

        return JSON.stringify({ok: true, lessons: lessons, dom: dom, groupId: groupId, group: group});
    })()
    """)
    les_data = json.loads(lessons_result) if isinstance(lessons_result, str) else lessons_result

    if les_data.get('ok'):
        lessons = les_data['lessons']
        dom = les_data['dom']
        print(f"  PASS: Found {len(lessons)} lessons")
        print(f"  DOM: draggables={dom['draggables']}, links={dom['lessonLinks']}, title='{dom['courseMenuTitle']}'")
        for l in lessons:
            vid = f" videoId={l['videoId']}" if l.get('videoId') else ""
            vid += f" videoLink={l['videoLink'][:40]}" if l.get('videoLink') else ""
            res = " +resources" if l.get('hasResources') else ""
            print(f"    [{l['type']}] {l['title']}{vid}{res}")
    else:
        print(f"  FAIL: {les_data.get('error')}")
        await s.stop()
        return

    # =========================================================
    # TEST 3: Navigate into a lesson (same tab)
    # =========================================================
    print("\n" + "=" * 60)
    print("TEST 3: Navigate into first lesson (same tab)")
    print("=" * 60)

    first_lesson = lessons[0]
    lesson_url = f"https://www.skool.com/{les_data['group']}/classroom/{target_mod['slug']}?md={first_lesson['id']}"
    print(f"  Navigating to: {lesson_url}")
    await s.navigate(lesson_url)
    await asyncio.sleep(5)
    await save_screenshot(s, "test-3-lesson")

    lesson_check = await run_js(s, """
    (function() {
        var el = document.getElementById('__NEXT_DATA__');
        if (!el) return JSON.stringify({ok: false, error: 'no __NEXT_DATA__'});
        var nd = JSON.parse(el.textContent);
        var pp = nd.props.pageProps;

        var result = {
            ok: true,
            url: window.location.href,
            title: document.title,
            selectedModule: pp.selectedModule || null,
            muxPlayers: document.querySelectorAll('mux-player').length,
            videos: document.querySelectorAll('video').length,
            iframes: document.querySelectorAll('iframe').length,
            muxPlaybackId: null,
            muxCustomDomain: null,
            markCompleteBtns: 0
        };

        // Check mux-player attributes
        var mux = document.querySelector('mux-player');
        if (mux) {
            result.muxPlaybackId = mux.getAttribute('playback-id');
            result.muxCustomDomain = mux.getAttribute('custom-domain');
        }

        // Check mark-complete buttons
        var btns = document.querySelectorAll('button');
        for (var i = 0; i < btns.length; i++) {
            var svg = btns[i].querySelector('svg');
            if (svg) {
                var vb = svg.getAttribute('viewBox');
                if (vb === '0 0 40 40' || (vb === '0 0 24 24' && svg.querySelectorAll('path').length >= 2)) {
                    result.markCompleteBtns++;
                }
            }
        }

        // Check for course content area
        var content = document.querySelector('[class*="CourseContent"], [class*="ModuleContent"], article, main');
        result.hasContentArea = !!content;

        return JSON.stringify(result);
    })()
    """)
    lc = json.loads(lesson_check) if isinstance(lesson_check, str) else lesson_check

    if lc.get('ok'):
        print(f"  URL: {lc['url']}")
        print(f"  Title: {lc['title']}")
        print(f"  Selected module: {lc['selectedModule']}")
        print(f"  Mux players: {lc['muxPlayers']}")
        print(f"  Mux playback-id: {lc['muxPlaybackId']}")
        print(f"  Mux custom-domain: {lc['muxCustomDomain']}")
        print(f"  Videos: {lc['videos']}")
        print(f"  Iframes: {lc['iframes']}")
        print(f"  Mark complete buttons: {lc['markCompleteBtns']}")
        print(f"  Has content area: {lc['hasContentArea']}")

        if lc['selectedModule']:
            print(f"  PASS: Lesson page loaded correctly")
        else:
            print(f"  WARN: selectedModule is null")
    else:
        print(f"  FAIL: {lc.get('error')}")

    # =========================================================
    # SUMMARY
    # =========================================================
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    print(f"  Test 1 (Scan modules):    {'PASS' if scan_data.get('ok') else 'FAIL'} - {len(scan_data.get('modules', []))} modules")
    print(f"  Test 2 (Module lessons):  {'PASS' if les_data.get('ok') else 'FAIL'} - {len(lessons)} lessons")
    print(f"  Test 3 (Lesson page):     {'PASS' if lc.get('selectedModule') else 'FAIL'} - selectedModule={'yes' if lc.get('selectedModule') else 'no'}")
    print(f"\n  Screenshots: output/test-1-classroom.png, test-2-module.png, test-3-lesson.png")

    await s.stop()


if __name__ == "__main__":
    asyncio.run(main())
