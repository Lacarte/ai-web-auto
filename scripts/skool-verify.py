"""
Quick verification: navigate into one module and confirm lessons are visible.
This validates the updated SKOOL-DOWN-COURSE extension logic.
"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from python.core.server import WebSocketServer


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

    # Step 1: Navigate to classroom and get first module slug
    print("=== Navigate to classroom ===")
    await s.navigate("https://www.skool.com/ai-video-creators/classroom")
    await asyncio.sleep(5)

    result = await run_js(s, """
    (function() {
        var el = document.getElementById('__NEXT_DATA__');
        if (!el) return JSON.stringify({error: 'no __NEXT_DATA__'});
        var nd = JSON.parse(el.textContent);
        var pp = nd.props.pageProps;
        var group = pp.currentGroup ? pp.currentGroup.name : '';
        var courses = pp.allCourses || [];
        // Pick "STEP 0 // Start here" or first course
        var target = null;
        for (var i = 0; i < courses.length; i++) {
            var title = (courses[i].metadata || {}).title || '';
            if (title.indexOf('Start here') !== -1 || title.indexOf('STEP 0') !== -1) {
                target = courses[i];
                break;
            }
        }
        if (!target && courses.length > 1) target = courses[1]; // skip first if it's a challenge
        if (!target) return JSON.stringify({error: 'no suitable module'});
        return JSON.stringify({
            groupName: group,
            moduleSlug: target.name,
            moduleTitle: (target.metadata || {}).title || target.name,
            moduleUrl: '/' + group + '/classroom/' + target.name
        });
    })()
    """)

    data = json.loads(result) if isinstance(result, str) else result
    print(f"  Target module: {data.get('moduleTitle')}")
    print(f"  URL: {data.get('moduleUrl')}")

    # Step 2: Navigate INTO the module
    module_url = f"https://www.skool.com{data['moduleUrl']}"
    print(f"\n=== Navigate into module: {module_url} ===")
    await s.navigate(module_url)
    await asyncio.sleep(5)

    # Step 3: Extract lessons from inside the module
    inner = await run_js(s, """
    (function() {
        var el = document.getElementById('__NEXT_DATA__');
        if (!el) return JSON.stringify({error: 'no __NEXT_DATA__'});
        var nd = JSON.parse(el.textContent);
        var pp = nd.props.pageProps;
        var result = {
            url: window.location.href,
            selectedModule: pp.selectedModule,
            hasCourse: !!pp.course,
            courseChildrenCount: 0,
            lessons: [],
            domSelectors: {}
        };

        if (pp.course && pp.course.children) {
            result.courseChildrenCount = pp.course.children.length;
            for (var i = 0; i < pp.course.children.length; i++) {
                var section = pp.course.children[i];
                var sectionTitle = section.course && section.course.metadata ? section.course.metadata.title : 'Section';
                if (section.children) {
                    for (var j = 0; j < section.children.length; j++) {
                        var lesson = section.children[j];
                        if (!lesson.course) continue;
                        var meta = lesson.course.metadata || {};
                        result.lessons.push({
                            id: lesson.course.id,
                            slug: lesson.course.name,
                            title: meta.title || lesson.course.name,
                            section: sectionTitle,
                            videoLink: meta.videoLink || null,
                            videoStream: meta.videoStream || null,
                            videoLenMs: meta.videoLenMs || null,
                            hasResources: !!(meta.resources)
                        });
                    }
                }
            }
        }

        // Verify DOM selectors
        result.domSelectors.draggables = document.querySelectorAll('[data-rbd-draggable-id]').length;
        result.domSelectors.lessonLinks = document.querySelectorAll('a[href*="/classroom/"]').length;
        result.domSelectors.chevrons = document.querySelectorAll('svg[viewBox="0 0 40 25"]').length;
        var topTitle = document.querySelector('[class*="CourseMenuTopTitleText"]');
        result.domSelectors.courseMenuTitle = topTitle ? topTitle.textContent.trim() : null;
        result.domSelectors.muxPlayers = document.querySelectorAll('mux-player').length;
        result.domSelectors.videos = document.querySelectorAll('video').length;

        return JSON.stringify(result);
    })()
    """)

    inner_data = json.loads(inner) if isinstance(inner, str) else inner
    os.makedirs("output/discover", exist_ok=True)
    with open("output/discover/skool-verified-module.json", "w", encoding="utf-8") as f:
        json.dump(inner_data, f, indent=2, ensure_ascii=False)

    print(f"  URL: {inner_data.get('url')}")
    print(f"  Selected module: {inner_data.get('selectedModule')}")
    print(f"  Has course: {inner_data.get('hasCourse')}")
    print(f"  Course sections: {inner_data.get('courseChildrenCount')}")
    print(f"  Total lessons: {len(inner_data.get('lessons', []))}")

    dom = inner_data.get('domSelectors', {})
    print(f"\n  DOM Selectors:")
    print(f"    Draggables: {dom.get('draggables')}")
    print(f"    Lesson links: {dom.get('lessonLinks')}")
    print(f"    Chevrons: {dom.get('chevrons')}")
    print(f"    Course menu title: {dom.get('courseMenuTitle')}")
    print(f"    Mux players: {dom.get('muxPlayers')}")
    print(f"    Videos: {dom.get('videos')}")

    lessons = inner_data.get('lessons', [])
    if lessons:
        print(f"\n  Lessons:")
        for les in lessons:
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
            print(f"    [{les.get('section')}] {les.get('title')}{vtype}{dur}{res}")
            print(f"      URL: /{data['groupName']}/classroom/{les['slug']}?md={les['id']}")

    print("\n=== VERIFICATION COMPLETE ===")
    if inner_data.get('hasCourse') and len(lessons) > 0:
        print("SUCCESS: Module navigation works! Lessons are extracted from __NEXT_DATA__.")
    else:
        print("ISSUE: Course data not found after navigating into module.")

    await s.stop()


if __name__ == "__main__":
    asyncio.run(main())
