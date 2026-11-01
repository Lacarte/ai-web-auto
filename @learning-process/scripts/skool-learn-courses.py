"""
Skool learner - Extract allCourses from __NEXT_DATA__ and figure out navigation.
"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ai_web_auto_backend.core.server import WebSocketServer
from ai_web_auto_backend.core.models import Command


async def run_js(s, script):
    resp = await s.execute_script(script)
    if resp and resp.data and resp.data.extracted_data:
        return resp.data.extracted_data.get("result", resp.data.extracted_data)
    return None


async def main():
    s = WebSocketServer()
    await s.start()
    print("Waiting for Chrome extension...")
    await s.wait_for_connection(timeout=60)
    print("Connected!\n")

    # Navigate to classroom
    print("=== Navigate to classroom ===")
    await s.navigate("https://www.skool.com/ai-video-creators/classroom")
    await asyncio.sleep(5)

    # Extract allCourses and figure out URL patterns
    print("\n=== Extract allCourses from __NEXT_DATA__ ===")
    result = await run_js(s, """
    (function() {
        var el = document.getElementById('__NEXT_DATA__');
        if (!el) return JSON.stringify({error: 'no __NEXT_DATA__'});
        var nd = JSON.parse(el.textContent);
        var pp = nd.props && nd.props.pageProps;
        if (!pp) return JSON.stringify({error: 'no pageProps'});

        var output = {
            courseRoute: pp.settings ? pp.settings.courseRoute : null,
            allCourses: [],
            groupName: pp.currentGroup ? pp.currentGroup.name : null,
            groupId: pp.currentGroup ? pp.currentGroup.id : null,
            renderData: pp.renderData || null
        };

        var courses = pp.allCourses || [];
        for (var i = 0; i < courses.length; i++) {
            var c = courses[i];
            var meta = c.metadata || {};
            output.allCourses.push({
                id: c.id,
                name: c.name,
                title: meta.title || null,
                description: (meta.description || '').substring(0, 100),
                unitType: c.unitType,
                public: c.public,
                state: c.state,
                rootId: c.rootId,
                hasChildren: false  // will check below
            });
        }

        // Also check if any course has a "course" property with children
        if (pp.course) {
            output.currentCourse = {
                id: pp.course.id,
                name: pp.course.name,
                childrenCount: pp.course.children ? pp.course.children.length : 0
            };
        }

        // Extract renderData.allCourses if different
        if (pp.renderData && pp.renderData.allCourses) {
            output.renderDataCoursesCount = pp.renderData.allCourses.length;
            // Check structure of first renderData course
            if (pp.renderData.allCourses.length > 0) {
                var first = pp.renderData.allCourses[0];
                output.renderDataFirstKeys = Object.keys(first);
                if (first.children) {
                    output.renderDataFirstChildrenCount = first.children.length;
                    if (first.children.length > 0) {
                        output.renderDataFirstChildKeys = Object.keys(first.children[0]);
                    }
                }
            }
        }

        return JSON.stringify(output);
    })()
    """)

    if result:
        data_str = result if isinstance(result, str) else json.dumps(result)
        data = json.loads(data_str) if isinstance(data_str, str) else data_str
        os.makedirs("output/discover", exist_ok=True)
        with open("output/discover/skool-allcourses.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        print(f"  Group: {data.get('groupName')} (id={data.get('groupId')})")
        print(f"  Course route: {data.get('courseRoute')}")
        print(f"  Total allCourses: {len(data.get('allCourses', []))}")
        print(f"  renderData courses: {data.get('renderDataCoursesCount')}")
        print(f"  renderData first keys: {data.get('renderDataFirstKeys')}")
        print(f"  renderData first children: {data.get('renderDataFirstChildrenCount')}")
        print(f"  renderData first child keys: {data.get('renderDataFirstChildKeys')}")

        print("\n  --- All Courses ---")
        for c in data.get('allCourses', []):
            print(f"    [{c.get('unitType')}] {c.get('title') or c.get('name')} (id={c.get('id')}, name={c.get('name')})")
    else:
        print("  No result")

    # Now extract the FULL renderData with children (the actual course tree)
    print("\n=== Extract full course tree from renderData ===")
    tree_result = await run_js(s, """
    (function() {
        var el = document.getElementById('__NEXT_DATA__');
        if (!el) return JSON.stringify({error: 'no __NEXT_DATA__'});
        var nd = JSON.parse(el.textContent);
        var pp = nd.props.pageProps;
        var rd = pp.renderData && pp.renderData.allCourses;
        if (!rd) return JSON.stringify({error: 'no renderData.allCourses'});

        var tree = [];
        for (var i = 0; i < rd.length; i++) {
            var mod = rd[i];
            var modMeta = mod.metadata || {};
            var modEntry = {
                id: mod.id,
                name: mod.name,
                title: modMeta.title || mod.name,
                unitType: mod.unitType,
                lessons: []
            };

            if (mod.children) {
                for (var j = 0; j < mod.children.length; j++) {
                    var lesson = mod.children[j];
                    var lMeta = lesson.metadata || {};
                    modEntry.lessons.push({
                        id: lesson.id,
                        name: lesson.name,
                        title: lMeta.title || lesson.name,
                        videoLink: lMeta.videoLink || null,
                        videoStream: lMeta.videoStream || null,
                        videoLenMs: lMeta.videoLenMs || null,
                        videoThumbnail: lMeta.videoThumbnail || null,
                        hasResources: !!(lMeta.resources),
                        completed: !!lMeta.completed,
                        description: (lMeta.description || '').substring(0, 100)
                    });
                }
            }
            tree.push(modEntry);
        }
        return JSON.stringify(tree);
    })()
    """)

    if tree_result:
        tree_str = tree_result if isinstance(tree_result, str) else json.dumps(tree_result)
        tree = json.loads(tree_str) if isinstance(tree_str, str) else tree_str
        with open("output/discover/skool-course-tree.json", "w", encoding="utf-8") as f:
            json.dump(tree, f, indent=2, ensure_ascii=False)

        total_lessons = 0
        for mod in tree:
            lessons = mod.get('lessons', [])
            total_lessons += len(lessons)
            print(f"\n  [{mod.get('title')}] ({len(lessons)} lessons)")
            for les in lessons[:3]:
                vtype = ""
                if les.get("videoLink"):
                    vl = les["videoLink"].lower()
                    if "youtube" in vl: vtype = " [YT]"
                    elif "loom" in vl: vtype = " [Loom]"
                    elif "vimeo" in vl: vtype = " [Vimeo]"
                    elif "skool" in vl or "b-cdn" in vl: vtype = " [Skool]"
                    else: vtype = " [Vid]"
                dur = ""
                if les.get("videoLenMs"):
                    secs = les["videoLenMs"] // 1000
                    dur = f" {secs//60}:{secs%60:02d}"
                res = " +res" if les.get("hasResources") else ""
                print(f"    - {les.get('title')}{vtype}{dur}{res}")
            if len(lessons) > 3:
                print(f"    ... and {len(lessons) - 3} more")
        print(f"\n  TOTAL: {len(tree)} modules, {total_lessons} lessons")
    else:
        print("  No tree result")

    # Figure out URL pattern for navigating to a specific lesson
    print("\n=== Determine URL pattern ===")
    url_result = await run_js(s, """
    (function() {
        var el = document.getElementById('__NEXT_DATA__');
        var nd = JSON.parse(el.textContent);
        var pp = nd.props.pageProps;
        var group = pp.currentGroup ? pp.currentGroup.name : 'unknown';
        var rd = pp.renderData && pp.renderData.allCourses;
        if (!rd || rd.length === 0) return JSON.stringify({error: 'no courses'});

        // Find first module with lessons
        for (var i = 0; i < rd.length; i++) {
            if (rd[i].children && rd[i].children.length > 0) {
                var lesson = rd[i].children[0];
                // Skool URL pattern: /group/classroom/courseName?md=lessonId
                var urlPattern = '/' + group + '/classroom/' + lesson.name + '?md=' + lesson.id;
                return JSON.stringify({
                    pattern: urlPattern,
                    fullUrl: 'https://www.skool.com' + urlPattern,
                    groupName: group,
                    lessonName: lesson.name,
                    lessonId: lesson.id,
                    moduleTitle: (rd[i].metadata || {}).title || rd[i].name,
                    lessonTitle: (lesson.metadata || {}).title || lesson.name,
                    courseRoute: pp.settings ? pp.settings.courseRoute : null
                });
            }
        }
        return JSON.stringify({error: 'no lessons found'});
    })()
    """)

    if url_result:
        url_str = url_result if isinstance(url_result, str) else json.dumps(url_result)
        url_data = json.loads(url_str) if isinstance(url_str, str) else url_str
        print(f"  URL pattern: {url_data.get('pattern')}")
        print(f"  Full URL: {url_data.get('fullUrl')}")
        print(f"  Course route setting: {url_data.get('courseRoute')}")
        print(f"  First lesson: {url_data.get('lessonTitle')} in {url_data.get('moduleTitle')}")

        # Try navigating to first lesson
        full_url = url_data.get('fullUrl')
        if full_url:
            print(f"\n=== Navigate to first lesson: {full_url} ===")
            await s.navigate(full_url)
            await asyncio.sleep(5)

            # Check what loaded
            inner = await run_js(s, """
            (function() {
                var result = {
                    url: window.location.href,
                    title: document.title,
                    draggables: document.querySelectorAll('[data-rbd-draggable-id]').length,
                    lessonLinks: document.querySelectorAll('a[href*="/classroom/"]').length,
                    courseMenuTitle: null,
                    muxPlayers: document.querySelectorAll('mux-player').length,
                    videos: document.querySelectorAll('video').length,
                    iframes: document.querySelectorAll('iframe').length,
                    expandChevrons: document.querySelectorAll('svg[viewBox="0 0 40 25"]').length
                };
                var topTitle = document.querySelector('[class*="CourseMenuTopTitleText"]');
                result.courseMenuTitle = topTitle ? topTitle.textContent.trim() : null;

                // Check __NEXT_DATA__ on lesson page
                var ndEl = document.getElementById('__NEXT_DATA__');
                if (ndEl) {
                    var nd = JSON.parse(ndEl.textContent);
                    var pp = nd.props.pageProps;
                    result.selectedModule = pp.selectedModule;
                    result.hasCourse = !!pp.course;
                    if (pp.course && pp.course.children) {
                        result.courseChildrenCount = pp.course.children.length;
                    }
                }

                // List first 5 classroom links
                var links = document.querySelectorAll('a[href*="/classroom/"]');
                result.firstLinks = [];
                for (var i = 0; i < Math.min(links.length, 5); i++) {
                    var titleEl = links[i].querySelector('[title]');
                    result.firstLinks.push({
                        href: links[i].getAttribute('href'),
                        title: titleEl ? titleEl.getAttribute('title') : links[i].textContent.trim().substring(0, 60)
                    });
                }

                return JSON.stringify(result);
            })()
            """)

            if inner:
                inner_str = inner if isinstance(inner, str) else json.dumps(inner)
                inner_data = json.loads(inner_str) if isinstance(inner_str, str) else inner_str
                with open("output/discover/skool-lesson-page.json", "w", encoding="utf-8") as f:
                    json.dump(inner_data, f, indent=2, ensure_ascii=False)

                print(f"  URL: {inner_data.get('url')}")
                print(f"  Title: {inner_data.get('title')}")
                print(f"  Draggables: {inner_data.get('draggables')}")
                print(f"  Lesson links: {inner_data.get('lessonLinks')}")
                print(f"  Course menu title: {inner_data.get('courseMenuTitle')}")
                print(f"  Expand chevrons: {inner_data.get('expandChevrons')}")
                print(f"  Selected module: {inner_data.get('selectedModule')}")
                print(f"  Course children: {inner_data.get('courseChildrenCount')}")
                print(f"  Mux players: {inner_data.get('muxPlayers')}")
                print(f"  Videos: {inner_data.get('videos')}")
                print(f"  Iframes: {inner_data.get('iframes')}")
                print(f"  First links:")
                for ll in inner_data.get('firstLinks', []):
                    print(f"    {ll.get('title')} -> {ll.get('href')}")

    print("\n=== LEARNING COMPLETE ===")
    await s.stop()


if __name__ == "__main__":
    asyncio.run(main())
