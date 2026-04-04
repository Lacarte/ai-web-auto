"""
Skool deep learner - clicks into a module card to discover the course list structure.
"""
import asyncio
import json
import sys
import os
import base64

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ai_web_auto_python.core.server import WebSocketServer
from ai_web_auto_python.core.models import Command


async def run_js(s, script):
    resp = await s.execute_script(script)
    if resp and resp.data and resp.data.extracted_data:
        r = resp.data.extracted_data.get("result", resp.data.extracted_data)
        return r
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
        print(f"  Screenshot: {path}")
        return True
    print("  No screenshot data")
    return False


async def main():
    s = WebSocketServer()
    await s.start()
    print("Waiting for Chrome extension to connect...")
    await s.wait_for_connection(timeout=60)
    print("Connected!\n")

    url = "https://www.skool.com/ai-video-creators/classroom"

    # Step 1: Navigate to classroom
    print(f"=== STEP 1: Navigate to {url} ===")
    await s.navigate(url)
    await asyncio.sleep(5)
    await save_screenshot(s, "skool-1-classroom")

    # Step 2: Analyze the classroom grid - find module cards
    print("\n=== STEP 2: Analyze classroom grid ===")
    grid_result = await run_js(s, """
    (function() {
        // Find the module cards - they are divs with module descriptions
        var result = { cards: [], links: [], pageUrl: window.location.href };

        // Method 1: Look for anchor tags that might link to courses
        var allLinks = document.querySelectorAll('a');
        for (var i = 0; i < allLinks.length; i++) {
            var href = allLinks[i].getAttribute('href') || '';
            if (href.includes('/classroom/') || href.includes('?md=')) {
                result.links.push({
                    href: href,
                    text: allLinks[i].textContent.trim().substring(0, 80),
                    tag: allLinks[i].tagName
                });
            }
        }

        // Method 2: Look for clickable card-like elements
        var divs = document.querySelectorAll('div');
        for (var j = 0; j < divs.length; j++) {
            var div = divs[j];
            var text = div.textContent.trim();
            // Look for module cards with step names and percentage
            if (text.match(/Step\\s*\\d|Start here|SUPPORT|Node Lab|Trends|Premium|Monetization/i) &&
                text.length < 300 && text.includes('%')) {
                var rect = div.getBoundingClientRect();
                if (rect.width > 100 && rect.height > 100) {
                    // Check for click handler or link inside
                    var innerLink = div.querySelector('a');
                    var hasClick = div.onclick || div.getAttribute('role') === 'button';
                    result.cards.push({
                        text: text.substring(0, 120),
                        x: Math.round(rect.x + rect.width/2),
                        y: Math.round(rect.y + rect.height/2),
                        w: Math.round(rect.width),
                        h: Math.round(rect.height),
                        tag: div.tagName,
                        hasInnerLink: !!innerLink,
                        innerLinkHref: innerLink ? innerLink.getAttribute('href') : null,
                        classes: div.className.substring(0, 100),
                        role: div.getAttribute('role') || ''
                    });
                }
            }
        }

        // Method 3: Check __NEXT_DATA__ for course/classroom structure
        var ndEl = document.getElementById('__NEXT_DATA__');
        if (ndEl) {
            try {
                var nd = JSON.parse(ndEl.textContent);
                var pp = nd.props && nd.props.pageProps;
                result.nextDataKeys = pp ? Object.keys(pp) : [];
                // Look for any course-related data
                if (pp) {
                    result.hasCourse = !!pp.course;
                    result.hasClassroom = !!pp.classroom;
                    result.hasModules = !!pp.modules;
                    result.hasSets = !!pp.sets;
                    result.allKeys = Object.keys(pp);
                    // Dump a few interesting values
                    for (var key of Object.keys(pp)) {
                        var val = pp[key];
                        if (val && typeof val === 'object' && !Array.isArray(val)) {
                            result['pp_' + key + '_keys'] = Object.keys(val).slice(0, 20);
                        } else if (Array.isArray(val)) {
                            result['pp_' + key + '_length'] = val.length;
                            if (val.length > 0 && typeof val[0] === 'object') {
                                result['pp_' + key + '_firstKeys'] = Object.keys(val[0]).slice(0, 15);
                            }
                        }
                    }
                }
            } catch(e) {
                result.nextDataError = e.message;
            }
        }

        return JSON.stringify(result);
    })()
    """)

    if grid_result:
        grid_str = grid_result if isinstance(grid_result, str) else json.dumps(grid_result)
        grid = json.loads(grid_str) if isinstance(grid_str, str) else grid_str
        os.makedirs("output/discover", exist_ok=True)
        with open("output/discover/skool-grid.json", "w", encoding="utf-8") as f:
            json.dump(grid, f, indent=2, ensure_ascii=False)

        print(f"  Links with /classroom/: {len(grid.get('links', []))}")
        for l in grid.get('links', [])[:5]:
            print(f"    {l}")
        print(f"  Module cards found: {len(grid.get('cards', []))}")
        for c in grid.get('cards', []):
            print(f"    [{c.get('w')}x{c.get('h')} @ ({c.get('x')},{c.get('y')})] {c.get('text', '')[:80]}")
            if c.get('innerLinkHref'):
                print(f"      -> {c['innerLinkHref']}")
        print(f"  __NEXT_DATA__ pageProps keys: {grid.get('allKeys', [])}")
        for k, v in grid.items():
            if k.startswith('pp_'):
                print(f"    {k}: {v}")
    else:
        print("  No grid result")

    # Step 3: Click the first module card to enter it
    print("\n=== STEP 3: Click into first module ('STEP 0 // Start here') ===")

    # First try to find clickable elements
    click_result = await run_js(s, """
    (function() {
        // Find first module card with a link
        var allLinks = document.querySelectorAll('a[href*="/classroom/"]');
        if (allLinks.length > 0) {
            return JSON.stringify({
                method: 'link',
                href: allLinks[0].getAttribute('href'),
                text: allLinks[0].textContent.trim().substring(0, 60)
            });
        }

        // Try finding module card divs and their actual link targets
        // Skool uses Next.js routing, so cards may use router.push
        var cards = document.querySelectorAll('[class*="CourseCard"], [class*="courseCard"], [class*="ModuleCard"]');
        if (cards.length > 0) {
            var rect = cards[0].getBoundingClientRect();
            return JSON.stringify({
                method: 'card_class',
                x: Math.round(rect.x + rect.width/2),
                y: Math.round(rect.y + rect.height/2),
                text: cards[0].textContent.trim().substring(0, 60)
            });
        }

        // Fallback: find the first card-like div and get its center
        var divs = document.querySelectorAll('div');
        for (var i = 0; i < divs.length; i++) {
            var text = divs[i].textContent.trim();
            if (text.indexOf('Start here') !== -1 && text.indexOf('%') !== -1 && text.length < 200) {
                var rect = divs[i].getBoundingClientRect();
                if (rect.width > 200 && rect.height > 200) {
                    // Look for an anchor inside this card
                    var link = divs[i].querySelector('a');
                    return JSON.stringify({
                        method: 'text_match',
                        x: Math.round(rect.x + rect.width/2),
                        y: Math.round(rect.y + rect.height/2),
                        href: link ? link.getAttribute('href') : null,
                        text: text.substring(0, 80)
                    });
                }
            }
        }

        return JSON.stringify({method: 'not_found'});
    })()
    """)

    if click_result:
        click_str = click_result if isinstance(click_result, str) else json.dumps(click_result)
        click = json.loads(click_str) if isinstance(click_str, str) else click_str
        print(f"  Click target: {click}")

        if click.get('href'):
            # Navigate directly
            full_url = click['href'] if click['href'].startswith('http') else f"https://www.skool.com{click['href']}"
            print(f"  Navigating to: {full_url}")
            await s.navigate(full_url)
            await asyncio.sleep(5)
        elif click.get('x') and click.get('y'):
            print(f"  CDP clicking at ({click['x']}, {click['y']})")
            await s.click_cdp(click['x'], click['y'])
            await asyncio.sleep(5)
        else:
            print("  Could not find clickable target!")
    else:
        print("  No click result")

    await save_screenshot(s, "skool-2-inside-module")

    # Step 4: Now analyze what's inside the module
    print("\n=== STEP 4: Analyze inside module ===")
    inner_result = await run_js(s, """
    (function() {
        var result = {
            url: window.location.href,
            title: document.title,
            modules: [],
            lessonLinks: [],
            draggables: [],
            hasCourseMenu: false,
            nextDataInfo: {}
        };

        // Check for course sidebar/menu
        var courseMenu = document.querySelector('[class*="CourseMenu"], [class*="courseMenu"], [class*="sidebar"]');
        result.hasCourseMenu = !!courseMenu;

        // Check for data-rbd-draggable-id (the Automa selectors)
        var drags = document.querySelectorAll('[data-rbd-draggable-id]');
        for (var i = 0; i < drags.length; i++) {
            var did = drags[i].getAttribute('data-rbd-draggable-id');
            result.draggables.push({
                id: did,
                tag: drags[i].tagName,
                text: drags[i].textContent.trim().substring(0, 80)
            });
        }

        // Check for lesson links
        var links = document.querySelectorAll('a[href*="/classroom/"]');
        for (var j = 0; j < links.length; j++) {
            var titleEl = links[j].querySelector('[title]');
            result.lessonLinks.push({
                href: links[j].getAttribute('href'),
                title: titleEl ? titleEl.getAttribute('title') : links[j].textContent.trim().substring(0, 60)
            });
        }

        // Check for module expander chevrons
        result.expandChevrons = document.querySelectorAll('svg[viewBox="0 0 40 25"]').length;

        // CourseMenuTopTitleText
        var topTitle = document.querySelector('[class*="CourseMenuTopTitleText"]');
        result.courseMenuTitle = topTitle ? topTitle.textContent.trim() : null;

        // __NEXT_DATA__
        var ndEl = document.getElementById('__NEXT_DATA__');
        if (ndEl) {
            try {
                var nd = JSON.parse(ndEl.textContent);
                var pp = nd.props && nd.props.pageProps;
                if (pp) {
                    result.nextDataInfo.keys = Object.keys(pp);
                    result.nextDataInfo.hasCourse = !!pp.course;
                    result.nextDataInfo.selectedModule = pp.selectedModule || null;
                    if (pp.course) {
                        result.nextDataInfo.courseId = pp.course.id;
                        result.nextDataInfo.courseName = pp.course.name;
                        result.nextDataInfo.childrenCount = pp.course.children ? pp.course.children.length : 0;
                        if (pp.course.children) {
                            var totalLessons = 0;
                            var moduleNames = [];
                            for (var k = 0; k < pp.course.children.length; k++) {
                                var set = pp.course.children[k];
                                var modName = set.course ? (set.course.metadata ? set.course.metadata.title || set.course.name : set.course.name) : '?';
                                var lessonCount = set.children ? set.children.length : 0;
                                totalLessons += lessonCount;
                                moduleNames.push(modName + ' (' + lessonCount + ' lessons)');
                            }
                            result.nextDataInfo.totalLessons = totalLessons;
                            result.nextDataInfo.moduleNames = moduleNames;
                        }
                    }
                }
            } catch(e) {
                result.nextDataInfo.error = e.message;
            }
        }

        // Video elements
        result.muxPlayers = document.querySelectorAll('mux-player').length;
        result.blobVideos = document.querySelectorAll('video[src^="blob:"]').length;
        result.allVideos = document.querySelectorAll('video').length;

        // Mark complete button
        var btns = document.querySelectorAll('button');
        result.totalButtons = btns.length;

        return JSON.stringify(result);
    })()
    """)

    if inner_result:
        inner_str = inner_result if isinstance(inner_result, str) else json.dumps(inner_result)
        inner = json.loads(inner_str) if isinstance(inner_str, str) else inner_str
        with open("output/discover/skool-inside-module.json", "w", encoding="utf-8") as f:
            json.dump(inner, f, indent=2, ensure_ascii=False)

        print(f"  URL: {inner.get('url')}")
        print(f"  Title: {inner.get('title')}")
        print(f"  Has course menu: {inner.get('hasCourseMenu')}")
        print(f"  Draggable elements: {len(inner.get('draggables', []))}")
        for d in inner.get('draggables', [])[:10]:
            print(f"    {d['id']}: {d['text'][:60]}")
        print(f"  Lesson links (a[href*='/classroom/']): {len(inner.get('lessonLinks', []))}")
        for ll in inner.get('lessonLinks', [])[:10]:
            print(f"    {ll.get('title', '?')[:60]} -> {ll.get('href')}")
        print(f"  Expand chevrons: {inner.get('expandChevrons')}")
        print(f"  Course menu title: {inner.get('courseMenuTitle')}")
        print(f"  Mux players: {inner.get('muxPlayers')}")
        print(f"  Videos: {inner.get('allVideos')}")
        ndi = inner.get('nextDataInfo', {})
        print(f"  __NEXT_DATA__ course: {ndi.get('courseName')} (children: {ndi.get('childrenCount')})")
        print(f"  Total lessons: {ndi.get('totalLessons')}")
        if ndi.get('moduleNames'):
            print("  Modules:")
            for mn in ndi['moduleNames']:
                print(f"    - {mn}")
    else:
        print("  No inner result")

    # Step 5: Final screenshot
    print("\n=== STEP 5: Final screenshot ===")
    await save_screenshot(s, "skool-3-final")

    print("\n=== DEEP LEARNING COMPLETE ===")
    await s.stop()


if __name__ == "__main__":
    asyncio.run(main())
