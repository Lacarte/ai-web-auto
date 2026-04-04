"""
Learn https://www.skool.com/learn-ai/classroom structure.
Same tab throughout.
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

async def save_ss(s, name):
    resp = await s.screenshot()
    if resp.data.screenshot:
        img = resp.data.screenshot
        if img.startswith("data:"): img = img.split(",", 1)[1]
        os.makedirs("output", exist_ok=True)
        with open(f"output/{name}.png", "wb") as f:
            f.write(base64.b64decode(img))
        print(f"  Screenshot: output/{name}.png")

async def main():
    s = WebSocketServer()
    await s.start()
    await s.wait_for_connection(timeout=60)
    print("Connected!\n")

    # =========================================================
    # 1. Classroom page
    # =========================================================
    print("=" * 60)
    print("1. Scan classroom")
    print("=" * 60)
    await s.navigate("https://www.skool.com/learn-ai/classroom")
    await asyncio.sleep(6)
    await save_ss(s, "learnai-1-classroom")

    scan = await run_js(s, """
    (function() {
        var nd = document.getElementById('__NEXT_DATA__');
        if (!nd) return JSON.stringify({error: 'no __NEXT_DATA__'});
        var data = JSON.parse(nd.textContent);
        var pp = data.props.pageProps;
        var group = pp.currentGroup ? pp.currentGroup.name : '';
        var groupId = pp.currentGroup ? pp.currentGroup.id : '';
        var allCourses = pp.allCourses || [];

        var modules = [];
        for (var i = 0; i < allCourses.length; i++) {
            var c = allCourses[i];
            var meta = c.metadata || {};
            modules.push({
                id: c.id, slug: c.name,
                title: meta.title || c.name,
                hasAccess: meta.hasAccess,
                minAccessLevel: meta.minAccessLevel,
                numModules: meta.numModules,
                metaKeys: Object.keys(meta)
            });
        }

        // DOM cards
        var cards = document.querySelectorAll('[class*="CourseContent"]');
        var domCards = [];
        for (var j = 0; j < cards.length; j++) {
            var text = cards[j].textContent.trim();
            domCards.push({
                text: text.substring(0, 100),
                isLocked: text.includes('Unlock at') || text.includes('Locked')
            });
        }

        return JSON.stringify({
            group: group, groupId: groupId,
            courseRoute: pp.settings ? pp.settings.courseRoute : null,
            totalModules: allCourses.length,
            modules: modules, domCards: domCards
        });
    })()
    """)
    data = json.loads(scan) if isinstance(scan, str) else scan
    os.makedirs("output/discover", exist_ok=True)
    with open("output/discover/learnai-classroom.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"  Group: {data.get('group')} (id={data.get('groupId')})")
    print(f"  Course route: {data.get('courseRoute')}")
    print(f"  Total modules: {data.get('totalModules')}")

    locked = 0
    open_count = 0
    print(f"\n  {'#':<3} {'Title':<50} {'Access':<10} {'Lock':<12}")
    print("  " + "-" * 85)
    for i, m in enumerate(data.get('modules', [])):
        access = m.get('hasAccess')
        min_lvl = m.get('minAccessLevel')
        is_locked = not access and min_lvl is not None
        if is_locked: locked += 1
        else: open_count += 1
        access_str = str(access) if access is not None else 'N/A'
        lock_str = f"Lv{min_lvl}" if is_locked else "Open"
        dom = data.get('domCards', [i]) if i < len(data.get('domCards', [])) else {}
        dom_lock = " DOM:🔒" if isinstance(dom, dict) and dom.get('isLocked') else ""
        print(f"  {i+1:<3} {m['title'][:50]:<50} {access_str:<10} {lock_str:<12}{dom_lock}")

    print(f"\n  Open: {open_count}, Locked: {locked}")

    # =========================================================
    # 2. Enter first open module
    # =========================================================
    print("\n" + "=" * 60)
    print("2. Enter first open module")
    print("=" * 60)

    target = None
    for m in data.get('modules', []):
        access = m.get('hasAccess')
        min_lvl = m.get('minAccessLevel')
        if access or min_lvl is None:
            target = m
            break

    if not target:
        print("  No open modules!")
        await s.stop()
        return

    mod_url = f"https://www.skool.com/{data['group']}/classroom/{target['slug']}"
    print(f"  Module: {target['title']}")
    print(f"  URL: {mod_url}")
    await s.navigate(mod_url)
    await asyncio.sleep(5)
    await save_ss(s, "learnai-2-module")

    lessons_result = await run_js(s, """
    (function() {
        var nd = document.getElementById('__NEXT_DATA__');
        if (!nd) return JSON.stringify({error: 'no __NEXT_DATA__'});
        var data = JSON.parse(nd.textContent);
        var pp = data.props.pageProps;
        if (!pp.course || !pp.course.children) return JSON.stringify({error: 'no course.children', keys: Object.keys(pp)});

        var lessons = [];
        var sections = pp.course.children;

        for (var i = 0; i < sections.length; i++) {
            var child = sections[i];
            if (!child.course) continue;
            var meta = child.course.metadata || {};

            if (child.children && child.children.length > 0) {
                var sectionTitle = meta.title || child.course.name;
                for (var j = 0; j < child.children.length; j++) {
                    var sub = child.children[j];
                    if (!sub.course) continue;
                    var sm = sub.course.metadata || {};
                    lessons.push({
                        id: sub.course.id, slug: sub.course.name,
                        title: sm.title || sub.course.name,
                        section: sectionTitle, type: 'nested',
                        hasAccess: sm.hasAccess,
                        videoId: sm.videoId || null,
                        videoLink: sm.videoLink || null,
                        videoStream: sm.videoStream || null,
                        videoLenMs: sm.videoLenMs || null,
                        hasResources: !!sm.resources,
                        metaKeys: Object.keys(sm)
                    });
                }
            } else {
                lessons.push({
                    id: child.course.id, slug: child.course.name,
                    title: meta.title || child.course.name,
                    section: '', type: 'flat',
                    hasAccess: meta.hasAccess,
                    videoId: meta.videoId || null,
                    videoLink: meta.videoLink || null,
                    videoStream: meta.videoStream || null,
                    videoLenMs: meta.videoLenMs || null,
                    hasResources: !!meta.resources,
                    metaKeys: Object.keys(meta)
                });
            }
        }

        var dom = {
            draggables: document.querySelectorAll('[data-rbd-draggable-id]').length,
            lessonLinks: document.querySelectorAll('a[href*="/classroom/"]').length,
            courseMenuTitle: (function() {
                var t = document.querySelector('[class*="CourseMenuTopTitleText"]');
                return t ? t.textContent.trim() : null;
            })()
        };

        return JSON.stringify({sections: sections.length, lessons: lessons, dom: dom});
    })()
    """)
    ld = json.loads(lessons_result) if isinstance(lessons_result, str) else lessons_result
    with open("output/discover/learnai-module-lessons.json", "w", encoding="utf-8") as f:
        json.dump(ld, f, indent=2, ensure_ascii=False)

    if ld.get('error'):
        print(f"  Error: {ld}")
    else:
        dom = ld.get('dom', {})
        print(f"  Sections: {ld.get('sections')}")
        print(f"  DOM: draggables={dom.get('draggables')}, links={dom.get('lessonLinks')}, title='{dom.get('courseMenuTitle')}'")
        print(f"  Total lessons: {len(ld.get('lessons', []))}")

        locked_l = 0
        print(f"\n  {'#':<3} {'Type':<7} {'Section':<20} {'Title':<35} {'Video':<12} {'Res':<5} {'Access'}")
        print("  " + "-" * 100)
        for i, l in enumerate(ld.get('lessons', [])):
            vtype = ""
            if l.get('videoLink'):
                vl = l['videoLink'].lower()
                if 'youtube' in vl: vtype = "YouTube"
                elif 'loom' in vl: vtype = "Loom"
                elif 'vimeo' in vl: vtype = "Vimeo"
                elif 'wistia' in vl: vtype = "Wistia"
                else: vtype = "ExtVid"
            elif l.get('videoId'): vtype = "Skool"
            elif l.get('videoStream'): vtype = "Stream"
            else: vtype = "-"

            access = l.get('hasAccess')
            access_str = "✅" if access else ("🔒" if access == False else "?")
            if access == False: locked_l += 1
            res = "✅" if l.get('hasResources') else "-"
            sec = l.get('section', '')[:20]
            print(f"  {i+1:<3} {l['type']:<7} {sec:<20} {l['title'][:35]:<35} {vtype:<12} {res:<5} {access_str}")

        open_l = len(ld.get('lessons', [])) - locked_l
        print(f"\n  Open: {open_l}, Locked: {locked_l}")

        # Show unique meta keys across all lessons
        all_keys = set()
        for l in ld.get('lessons', []):
            all_keys.update(l.get('metaKeys', []))
        print(f"  All meta keys: {sorted(all_keys)}")

    # =========================================================
    # 3. Enter first lesson with video
    # =========================================================
    lessons = ld.get('lessons', [])
    vid_lesson = None
    for l in lessons:
        if l.get('videoLink') or l.get('videoId'):
            vid_lesson = l
            break

    if vid_lesson:
        print("\n" + "=" * 60)
        print(f"3. Enter lesson with video: {vid_lesson['title']}")
        print("=" * 60)

        lesson_url = f"https://www.skool.com/{data['group']}/classroom/{target['slug']}?md={vid_lesson['id']}"
        print(f"  URL: {lesson_url}")
        await s.navigate(lesson_url)
        await asyncio.sleep(5)
        await save_ss(s, "learnai-3-lesson")

        lcheck = await run_js(s, """
        (function() {
            var iframes = [];
            document.querySelectorAll('iframe').forEach(function(f) {
                if (f.src && !f.src.includes('stripe.com')) iframes.push(f.src.substring(0, 150));
            });
            return JSON.stringify({
                url: window.location.href,
                title: document.title,
                selectedModule: (function() {
                    try { return JSON.parse(document.getElementById('__NEXT_DATA__').textContent).props.pageProps.selectedModule; } catch(e) { return null; }
                })(),
                muxPlayers: document.querySelectorAll('mux-player').length,
                videos: document.querySelectorAll('video').length,
                iframes: iframes,
                videoDuration: (function() {
                    var d = document.querySelector('[class*="VideoDuration"]');
                    return d ? d.textContent.trim() : null;
                })(),
                resources: document.querySelectorAll('[class*="ResourceWrapper"]').length
            });
        })()
        """)
        lc = json.loads(lcheck) if isinstance(lcheck, str) else lcheck
        print(f"  Title: {lc.get('title')}")
        print(f"  Selected module: {lc.get('selectedModule')}")
        print(f"  Video duration: {lc.get('videoDuration')}")
        print(f"  Mux players: {lc.get('muxPlayers')}")
        print(f"  Videos: {lc.get('videos')}")
        print(f"  Non-Stripe iframes: {lc.get('iframes')}")
        print(f"  Resource wrappers: {lc.get('resources')}")

        vtype = ""
        if vid_lesson.get('videoLink'):
            vl = vid_lesson['videoLink'].lower()
            if 'youtube' in vl: vtype = "YouTube"
            elif 'loom' in vl: vtype = "Loom"
            elif 'vimeo' in vl: vtype = "Vimeo"
            else: vtype = "External"
        elif vid_lesson.get('videoId'): vtype = "Skool native"
        print(f"  Video type: {vtype}")
        if vid_lesson.get('videoLink'): print(f"  Video link: {vid_lesson['videoLink'][:120]}")
        if vid_lesson.get('videoId'): print(f"  Video ID: {vid_lesson['videoId']}")

    print("\n=== LEARNING COMPLETE ===")
    await s.stop()

asyncio.run(main())
