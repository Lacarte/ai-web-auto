"""Quick check of course.children structure inside a module."""
import asyncio, json, sys, os
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
    await s.wait_for_connection(timeout=60)
    print("Connected!\n")

    # Navigate directly into STEP 0 module
    await s.navigate("https://www.skool.com/ai-video-creators/classroom/e22d16af")
    await asyncio.sleep(5)

    # Dump course.children structure in detail
    result = await run_js(s, """
    (function() {
        var el = document.getElementById('__NEXT_DATA__');
        var nd = JSON.parse(el.textContent);
        var pp = nd.props.pageProps;
        var course = pp.course;
        if (!course || !course.children) return JSON.stringify({error: 'no course.children'});

        var output = {
            courseId: course.id,
            courseName: course.name,
            childrenCount: course.children.length,
            children: []
        };

        for (var i = 0; i < course.children.length; i++) {
            var child = course.children[i];
            var entry = {
                id: child.course ? child.course.id : null,
                name: child.course ? child.course.name : null,
                unitType: child.course ? child.course.unitType : null,
                hasChildren: !!(child.children && child.children.length > 0),
                childrenCount: child.children ? child.children.length : 0,
                metadataKeys: child.course && child.course.metadata ? Object.keys(child.course.metadata) : [],
                title: child.course && child.course.metadata ? child.course.metadata.title : null,
                videoLink: child.course && child.course.metadata ? child.course.metadata.videoLink : null,
                videoStream: child.course && child.course.metadata ? child.course.metadata.videoStream : null,
                videoLenMs: child.course && child.course.metadata ? child.course.metadata.videoLenMs : null,
                hasResources: child.course && child.course.metadata ? !!child.course.metadata.resources : false
            };
            // Also check first sub-child if exists
            if (child.children && child.children.length > 0) {
                var sub = child.children[0];
                entry.firstChild = {
                    id: sub.course ? sub.course.id : null,
                    name: sub.course ? sub.course.name : null,
                    title: sub.course && sub.course.metadata ? sub.course.metadata.title : null,
                    unitType: sub.course ? sub.course.unitType : null,
                    videoLink: sub.course && sub.course.metadata ? sub.course.metadata.videoLink : null
                };
            }
            output.children.push(entry);
        }

        // Also get lesson links from DOM
        var links = document.querySelectorAll('a[href*="/classroom/"]');
        output.lessonLinksDOM = [];
        for (var j = 0; j < links.length; j++) {
            var titleEl = links[j].querySelector('[title]');
            output.lessonLinksDOM.push({
                href: links[j].getAttribute('href'),
                title: titleEl ? titleEl.getAttribute('title') : links[j].textContent.trim().substring(0, 60)
            });
        }

        return JSON.stringify(output);
    })()
    """)

    data = json.loads(result) if isinstance(result, str) else result
    os.makedirs("output/discover", exist_ok=True)
    with open("output/discover/skool-children-structure.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Course: {data.get('courseName')} (children: {data.get('childrenCount')})")
    print(f"\nChildren:")
    for c in data.get('children', []):
        t = c.get('title') or c.get('name')
        ut = c.get('unitType')
        has_kids = c.get('hasChildren')
        kids_count = c.get('childrenCount')
        vl = c.get('videoLink', '')[:50] if c.get('videoLink') else 'none'
        vs = 'yes' if c.get('videoStream') else 'no'
        print(f"  [{ut}] {t}")
        print(f"    id={c.get('id')} slug={c.get('name')}")
        print(f"    videoLink={vl} videoStream={vs} hasChildren={has_kids}({kids_count})")
        if c.get('firstChild'):
            fc = c['firstChild']
            print(f"    -> First child: [{fc.get('unitType')}] {fc.get('title')} videoLink={fc.get('videoLink','none')[:50]}")

    print(f"\nDOM lesson links ({len(data.get('lessonLinksDOM', []))}):")
    for ll in data.get('lessonLinksDOM', []):
        print(f"  {ll.get('title')} -> {ll.get('href')}")

    await s.stop()

asyncio.run(main())
