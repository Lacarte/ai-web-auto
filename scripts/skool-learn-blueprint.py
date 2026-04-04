"""
Learn the structure of https://www.skool.com/the-ai-blueprint/classroom
Same tab throughout.
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
    # 1. Classroom main page
    # =========================================================
    print("=" * 60)
    print("1. Scan classroom main page")
    print("=" * 60)
    await s.navigate("https://www.skool.com/the-ai-blueprint/classroom")
    await asyncio.sleep(6)
    await save_ss(s, "blueprint-1-classroom")

    scan = await run_js(s, """
    (function() {
        var nd = document.getElementById('__NEXT_DATA__');
        if (!nd) return JSON.stringify({error: 'no __NEXT_DATA__'});
        var data = JSON.parse(nd.textContent);
        var pp = data.props.pageProps;
        var group = pp.currentGroup ? pp.currentGroup.name : '';
        var groupId = pp.currentGroup ? pp.currentGroup.id : '';
        var allCourses = pp.allCourses || [];
        var rdCourses = (pp.renderData && pp.renderData.allCourses) || [];

        var modules = [];
        for (var i = 0; i < allCourses.length; i++) {
            var c = allCourses[i];
            var meta = c.metadata || {};
            modules.push({
                id: c.id,
                slug: c.name,
                title: meta.title || c.name,
                hasAccess: meta.hasAccess,
                minAccessLevel: meta.minAccessLevel,
                numModules: meta.numModules,
                state: c.state,
                public: c.public,
                metaKeys: Object.keys(meta)
            });
        }

        // DOM cards check
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
            modules: modules,
            domCards: domCards,
            pagePropsKeys: Object.keys(pp)
        });
    })()
    """)

    data = json.loads(scan) if isinstance(scan, str) else scan
    os.makedirs("output/discover", exist_ok=True)
    with open("output/discover/blueprint-classroom.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"  Group: {data.get('group')} (id={data.get('groupId')})")
    print(f"  Course route: {data.get('courseRoute')}")
    print(f"  Total modules: {data.get('totalModules')}")
    print(f"  PageProps keys: {data.get('pagePropsKeys')}")

    locked_count = 0
    open_count = 0
    print(f"\n  {'#':<3} {'Title':<45} {'Access':<10} {'Locked?':<12} {'MetaKeys'}")
    print("  " + "-" * 100)
    for i, m in enumerate(data.get('modules', [])):
        access = m.get('hasAccess')
        access_str = str(access) if access is not None else 'N/A'
        min_lvl = m.get('minAccessLevel')
        is_locked = not access and min_lvl is not None
        lock_str = f"Lv{min_lvl}" if is_locked else "Open"
        if is_locked: locked_count += 1
        else: open_count += 1

        dom = data.get('domCards', [])[i] if i < len(data.get('domCards', [])) else {}
        dom_locked = "🔒 DOM" if dom.get('isLocked') else ""
        print(f"  {i+1:<3} {m['title'][:45]:<45} {access_str:<10} {lock_str:<12} {dom_locked}")

    print(f"\n  Open: {open_count}, Locked: {locked_count}")

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
        print("  No open modules found!")
        await s.stop()
        return

    mod_url = f"https://www.skool.com/{data['group']}/classroom/{target['slug']}"
    print(f"  Module: {target['title']}")
    print(f"  URL: {mod_url}")
    await s.navigate(mod_url)
    await asyncio.sleep(5)
    await save_ss(s, "blueprint-2-module")

    lessons_data = await run_js(s, """
    (function() {
        var nd = document.getElementById('__NEXT_DATA__');
        if (!nd) return JSON.stringify({error: 'no __NEXT_DATA__'});
        var data = JSON.parse(nd.textContent);
        var pp = data.props.pageProps;
        if (!pp.course || !pp.course.children) return JSON.stringify({error: 'no course.children', ppKeys: Object.keys(pp)});

        var groupId = pp.currentGroup ? pp.currentGroup.id : '';
        var group = pp.currentGroup ? pp.currentGroup.name : '';
        var lessons = [];
        var sections = pp.course.children;

        for (var i = 0; i < sections.length; i++) {
            var child = sections[i];
            if (!child.course) continue;
            var meta = child.course.metadata || {};

            if (child.children && child.children.length > 0) {
                // NESTED structure
                var sectionTitle = meta.title || child.course.name;
                for (var j = 0; j < child.children.length; j++) {
                    var sub = child.children[j];
                    if (!sub.course) continue;
                    var sm = sub.course.metadata || {};
                    lessons.push({
                        id: sub.course.id,
                        slug: sub.course.name,
                        title: sm.title || sub.course.name,
                        section: sectionTitle,
                        type: 'nested',
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
                // FLAT structure
                lessons.push({
                    id: child.course.id,
                    slug: child.course.name,
                    title: meta.title || child.course.name,
                    section: '',
                    type: 'flat',
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
            courseMenuTitle: null,
            muxPlayers: document.querySelectorAll('mux-player').length,
            videos: document.querySelectorAll('video').length
        };
        var t = document.querySelector('[class*="CourseMenuTopTitleText"]');
        dom.courseMenuTitle = t ? t.textContent.trim() : null;

        return JSON.stringify({
            sections: sections.length,
            lessons: lessons,
            dom: dom,
            groupId: groupId,
            group: group
        });
    })()
    """)

    ld = json.loads(lessons_data) if isinstance(lessons_data, str) else lessons_data
    with open("output/discover/blueprint-module-lessons.json", "w", encoding="utf-8") as f:
        json.dump(ld, f, indent=2, ensure_ascii=False)

    if ld.get('error'):
        print(f"  Error: {ld}")
    else:
        print(f"  Sections: {ld.get('sections')}")
        dom = ld.get('dom', {})
        print(f"  DOM: draggables={dom.get('draggables')}, links={dom.get('lessonLinks')}, title='{dom.get('courseMenuTitle')}'")
        print(f"  Total lessons: {len(ld.get('lessons', []))}")

        locked_l = 0
        print(f"\n  {'#':<3} {'Section':<25} {'Title':<40} {'Video':<15} {'Access'}")
        print("  " + "-" * 95)
        for i, l in enumerate(ld.get('lessons', [])):
            vtype = ""
            if l.get('videoLink'):
                vl = l['videoLink'].lower()
                if 'youtube' in vl: vtype = "YouTube"
                elif 'loom' in vl: vtype = "Loom"
                elif 'vimeo' in vl: vtype = "Vimeo"
                elif 'wistia' in vl: vtype = "Wistia"
                else: vtype = "ExtVideo"
            elif l.get('videoId'): vtype = "Skool"
            elif l.get('videoStream'): vtype = "Stream"
            else: vtype = "-"

            access = l.get('hasAccess')
            access_str = "✅" if access else ("🔒" if access == False else "?")
            if access == False: locked_l += 1

            sec = l.get('section', '')[:25]
            print(f"  {i+1:<3} {sec:<25} {l['title'][:40]:<40} {vtype:<15} {access_str}")

        open_l = len(ld.get('lessons', [])) - locked_l
        print(f"\n  Open: {open_l}, Locked: {locked_l}")

    # =========================================================
    # 3. Enter first lesson to check video
    # =========================================================
    lessons = ld.get('lessons', [])
    if lessons:
        first = lessons[0]
        print("\n" + "=" * 60)
        print(f"3. Enter first lesson: {first['title']}")
        print("=" * 60)

        lesson_url = f"https://www.skool.com/{ld['group']}/classroom/{target['slug']}?md={first['id']}"
        print(f"  URL: {lesson_url}")
        await s.navigate(lesson_url)
        await asyncio.sleep(5)
        await save_ss(s, "blueprint-3-lesson")

        lcheck = await run_js(s, """
        (function() {
            return JSON.stringify({
                url: window.location.href,
                title: document.title,
                selectedModule: (function() {
                    var nd = document.getElementById('__NEXT_DATA__');
                    if (!nd) return null;
                    return JSON.parse(nd.textContent).props.pageProps.selectedModule;
                })(),
                muxPlayers: document.querySelectorAll('mux-player').length,
                videos: document.querySelectorAll('video').length,
                iframes: Array.from(document.querySelectorAll('iframe')).filter(function(f) {
                    return !f.src.includes('stripe.com');
                }).map(function(f) { return f.src.substring(0, 150); }),
                videoDuration: (function() {
                    var d = document.querySelector('[class*="VideoDuration"]');
                    return d ? d.textContent.trim() : null;
                })(),
                markCompleteBtn: document.querySelectorAll('button svg[viewBox="0 0 24 24"]').length +
                                 document.querySelectorAll('button svg[viewBox="0 0 40 40"]').length
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
        print(f"  Mark complete btns: {lc.get('markCompleteBtn')}")

    print("\n=== LEARNING COMPLETE ===")
    await s.stop()

asyncio.run(main())
