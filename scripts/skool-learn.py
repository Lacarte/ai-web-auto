"""
Skool classroom learner script.
Navigates to the Skool classroom page, takes screenshots, discovers elements,
and extracts the course structure to verify/learn selectors.
"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from python.core.server import WebSocketServer
from python.core.models import Command


async def run_js(s, script):
    """Execute JS and return the result string."""
    resp = await s.execute_script(script)
    if resp and resp.data and resp.data.extracted_data:
        return resp.data.extracted_data.get("result", resp.data.extracted_data)
    return None


async def main():
    s = WebSocketServer()
    await s.start()
    print("Waiting for Chrome extension to connect...")
    await s.wait_for_connection(timeout=60)
    print("Connected!\n")

    url = "https://www.skool.com/ai-video-creators/classroom"

    # Step 1: Navigate
    print(f"=== STEP 1: Navigate to {url} ===")
    resp = await s.navigate(url)
    print(f"  Navigate status: {resp.status}")
    await asyncio.sleep(5)

    # Step 2: Screenshot
    print("\n=== STEP 2: Screenshot ===")
    resp = await s.screenshot()
    if resp.data.screenshot:
        os.makedirs("output", exist_ok=True)
        # Save the screenshot
        import base64
        img_data = resp.data.screenshot
        if img_data.startswith("data:"):
            img_data = img_data.split(",", 1)[1]
        with open("output/skool-classroom-1.png", "wb") as f:
            f.write(base64.b64decode(img_data))
        print("  Saved: output/skool-classroom-1.png")
    else:
        print("  No screenshot data returned")

    # Step 3: Discover interactive elements via discover command
    print("\n=== STEP 3: Discover elements ===")
    discover_resp = await s.send_command(Command(action="discover", timeout=30000))
    os.makedirs("output/discover", exist_ok=True)
    if discover_resp and discover_resp.data:
        elements = discover_resp.data.dom_snapshot
        if elements:
            with open("output/discover/skool-classroom.json", "w", encoding="utf-8") as f:
                json.dump(elements.model_dump() if hasattr(elements, 'model_dump') else elements, f, indent=2, ensure_ascii=False)
            ie = elements.interactive_elements if hasattr(elements, 'interactive_elements') else []
            print(f"  Found {len(ie)} interactive elements")
            print(f"  Page title: {elements.title if hasattr(elements, 'title') else '?'}")
            # Show classroom-related elements
            for el in ie:
                href = el.get("href", "")
                text = str(el.get("text", ""))[:60]
                if "/classroom/" in href:
                    print(f"    [{el.get('tag','')}] {text} -> {href}")
        else:
            # Try extracted_data
            ed = discover_resp.data.extracted_data
            with open("output/discover/skool-classroom.json", "w", encoding="utf-8") as f:
                json.dump(ed, f, indent=2, ensure_ascii=False)
            print(f"  Discover data saved (extracted_data keys: {list(ed.keys()) if isinstance(ed, dict) else type(ed)})")
    else:
        print("  Discover returned no data")

    # Step 4: Extract __NEXT_DATA__
    print("\n=== STEP 4: Extract __NEXT_DATA__ ===")
    next_data_result = await run_js(s, """
    (function() {
        var el = document.getElementById('__NEXT_DATA__');
        if (!el) return JSON.stringify({error: 'No __NEXT_DATA__ found'});
        try {
            var data = JSON.parse(el.textContent);
            var pp = data.props && data.props.pageProps;
            if (!pp) return JSON.stringify({error: 'No pageProps'});

            var result = {
                groupId: pp.currentGroup ? pp.currentGroup.id : null,
                groupName: pp.currentGroup ? pp.currentGroup.name : null,
                courseId: pp.course ? pp.course.id : null,
                courseName: pp.course ? pp.course.name : null,
                selectedModule: pp.selectedModule || null,
                modules: [],
                totalLessons: 0
            };

            if (pp.course && pp.course.children) {
                for (var i = 0; i < pp.course.children.length; i++) {
                    var set = pp.course.children[i];
                    var mod = {
                        id: set.course ? set.course.id : null,
                        name: set.course ? set.course.name : null,
                        title: set.course && set.course.metadata ? set.course.metadata.title : null,
                        lessons: []
                    };
                    if (set.children) {
                        for (var j = 0; j < set.children.length; j++) {
                            var lesson = set.children[j];
                            if (lesson.course) {
                                var meta = lesson.course.metadata || {};
                                mod.lessons.push({
                                    id: lesson.course.id,
                                    name: lesson.course.name,
                                    title: meta.title || null,
                                    videoLink: meta.videoLink || null,
                                    videoStream: meta.videoStream || null,
                                    videoLenMs: meta.videoLenMs || null,
                                    hasResources: !!(meta.resources),
                                    completed: !!meta.completed
                                });
                                result.totalLessons++;
                            }
                        }
                    }
                    result.modules.push(mod);
                }
            }
            return JSON.stringify(result);
        } catch(e) {
            return JSON.stringify({error: e.message});
        }
    })()
    """)

    if next_data_result:
        nd_str = next_data_result if isinstance(next_data_result, str) else json.dumps(next_data_result)
        try:
            nd = json.loads(nd_str) if isinstance(nd_str, str) else nd_str
        except (json.JSONDecodeError, TypeError):
            nd = {"raw": str(next_data_result)[:500]}

        with open("output/discover/skool-nextdata.json", "w", encoding="utf-8") as f:
            json.dump(nd, f, indent=2, ensure_ascii=False)

        if isinstance(nd, dict) and "error" not in nd:
            print(f"  Group: {nd.get('groupName')} (id={nd.get('groupId')})")
            print(f"  Course: {nd.get('courseName')} (id={nd.get('courseId')})")
            print(f"  Selected module: {nd.get('selectedModule')}")
            print(f"  Total modules: {len(nd.get('modules', []))}")
            print(f"  Total lessons: {nd.get('totalLessons', 0)}")
            print("\n  --- Modules ---")
            for mod in nd.get("modules", []):
                title = mod.get("title") or mod.get("name") or "?"
                lessons = mod.get("lessons", [])
                print(f"\n  [{title}] ({len(lessons)} lessons)")
                for les in lessons[:5]:
                    vtype = ""
                    if les.get("videoLink"):
                        vl = les["videoLink"].lower()
                        if "youtube" in vl: vtype = " [YouTube]"
                        elif "loom" in vl: vtype = " [Loom]"
                        elif "vimeo" in vl: vtype = " [Vimeo]"
                        elif "skool" in vl or "b-cdn" in vl: vtype = " [Skool]"
                        else: vtype = " [Video]"
                    dur = ""
                    if les.get("videoLenMs"):
                        secs = les["videoLenMs"] // 1000
                        dur = f" {secs//60}:{secs%60:02d}"
                    res = " +resources" if les.get("hasResources") else ""
                    done = " done" if les.get("completed") else ""
                    print(f"    - {les.get('title') or les.get('name')}{vtype}{dur}{res}{done}")
                if len(lessons) > 5:
                    print(f"    ... and {len(lessons) - 5} more")
        else:
            print(f"  Result: {nd}")
    else:
        print("  No __NEXT_DATA__ result")

    # Step 5: Check DOM selectors used by our extension
    print("\n=== STEP 5: Verify extension selectors ===")
    sel_result = await run_js(s, """
    (function() {
        var results = {};
        var drags = document.querySelectorAll('[data-rbd-draggable-id]');
        var modules = [];
        var lessons_links = [];
        for (var i = 0; i < drags.length; i++) {
            var did = drags[i].getAttribute('data-rbd-draggable-id');
            if (did && did.indexOf('set-') === 0 && did.indexOf('setModule-') !== 0) {
                var links = drags[i].querySelectorAll('a[href*="/classroom/"]');
                modules.push({
                    dragId: did,
                    linksCount: links.length,
                    firstLinkHref: links.length > 0 ? links[0].getAttribute('href') : null
                });
                for (var j = 0; j < links.length; j++) {
                    var titleEl = links[j].querySelector('[title]');
                    lessons_links.push({
                        href: links[j].getAttribute('href'),
                        title: titleEl ? titleEl.getAttribute('title') : links[j].textContent.trim().substring(0, 60)
                    });
                }
            }
        }
        results.moduleContainers = modules.length;
        results.modules = modules;
        results.totalLessonLinks = lessons_links.length;
        results.lessonLinks = lessons_links.slice(0, 10);
        results.expandChevrons = document.querySelectorAll('svg[viewBox="0 0 40 25"]').length;
        var topTitle = document.querySelector('[class*="CourseMenuTopTitleText"]');
        results.courseMenuTitle = topTitle ? topTitle.textContent.trim() : null;
        var btns = document.querySelectorAll('button');
        results.totalButtons = btns.length;
        var checkBtns = 0;
        for (var b = 0; b < btns.length; b++) {
            var svg = btns[b].querySelector('svg');
            if (svg) {
                var vb = svg.getAttribute('viewBox');
                if (vb === '0 0 40 40' || (vb === '0 0 24 24' && svg.querySelectorAll('path').length >= 2)) {
                    checkBtns++;
                }
            }
        }
        results.markCompleteButtons = checkBtns;
        results.muxPlayers = document.querySelectorAll('mux-player').length;
        results.blobVideos = document.querySelectorAll('video[src^="blob:"]').length;
        results.iframes = document.querySelectorAll('iframe').length;
        return JSON.stringify(results);
    })()
    """)

    if sel_result:
        sel_str = sel_result if isinstance(sel_result, str) else json.dumps(sel_result)
        try:
            sel = json.loads(sel_str) if isinstance(sel_str, str) else sel_str
        except (json.JSONDecodeError, TypeError):
            sel = {"raw": str(sel_result)[:500]}

        with open("output/discover/skool-selectors.json", "w", encoding="utf-8") as f:
            json.dump(sel, f, indent=2, ensure_ascii=False)

        if isinstance(sel, dict):
            print(f"  Module containers (data-rbd-draggable-id='set-*'): {sel.get('moduleContainers')}")
            print(f"  Expand chevrons (svg viewBox='0 0 40 25'): {sel.get('expandChevrons')}")
            print(f"  Total lesson links (a[href*='/classroom/']): {sel.get('totalLessonLinks')}")
            print(f"  Course menu title: {sel.get('courseMenuTitle')}")
            print(f"  Mark complete buttons: {sel.get('markCompleteButtons')}")
            print(f"  Mux players: {sel.get('muxPlayers')}")
            print(f"  Blob videos: {sel.get('blobVideos')}")
            print(f"  Iframes: {sel.get('iframes')}")

            if sel.get('totalLessonLinks') == 0 and sel.get('moduleContainers', 0) > 0:
                print("\n  WARNING: Modules found but no lesson links - modules may be collapsed!")

            print(f"\n  First lesson links:")
            for ll in sel.get("lessonLinks", []):
                print(f"    {ll.get('title', '?')[:60]} -> {ll.get('href', '?')}")
        else:
            print(f"  Result: {sel}")
    else:
        print("  No selector check result")

    # Step 6: Final screenshot
    print("\n=== STEP 6: Final screenshot ===")
    resp = await s.screenshot()
    if resp.data.screenshot:
        import base64
        img_data = resp.data.screenshot
        if img_data.startswith("data:"):
            img_data = img_data.split(",", 1)[1]
        with open("output/skool-classroom-2.png", "wb") as f:
            f.write(base64.b64decode(img_data))
        print("  Saved: output/skool-classroom-2.png")

    print("\n=== LEARNING COMPLETE ===")
    print("Check output/discover/ for JSON data and output/ for screenshots.")

    await s.stop()


if __name__ == "__main__":
    asyncio.run(main())
