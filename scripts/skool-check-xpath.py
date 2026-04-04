"""Check what the user's XPath points to on the classroom page. Same tab."""
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

    await s.navigate("https://www.skool.com/ai-video-creators/classroom")
    await asyncio.sleep(5)

    result = await run_js(s, """
    (function() {
        var xpath = '/html/body/div[1]/div/div[1]/div[3]/div/div/div[1]/div[1]/div';
        var el = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
        if (!el) return JSON.stringify({error: 'XPath not found'});

        var rect = el.getBoundingClientRect();
        var result = {
            tag: el.tagName,
            className: (el.className || '').substring(0, 200),
            id: el.id || '',
            role: el.getAttribute('role') || '',
            text: el.textContent.trim().substring(0, 300),
            innerHTML: el.innerHTML.substring(0, 500),
            x: Math.round(rect.x), y: Math.round(rect.y),
            w: Math.round(rect.width), h: Math.round(rect.height),
            childCount: el.children.length,
            children: []
        };

        // Inspect direct children
        for (var i = 0; i < Math.min(el.children.length, 20); i++) {
            var child = el.children[i];
            var cr = child.getBoundingClientRect();
            var childInfo = {
                index: i,
                tag: child.tagName,
                className: (child.className || '').substring(0, 120),
                text: child.textContent.trim().substring(0, 100),
                w: Math.round(cr.width), h: Math.round(cr.height),
                x: Math.round(cr.x), y: Math.round(cr.y),
                hasLink: !!child.querySelector('a'),
                linkHref: child.querySelector('a') ? child.querySelector('a').getAttribute('href') : null,
                dataAttrs: {}
            };
            // Check data attributes
            for (var a = 0; a < child.attributes.length; a++) {
                if (child.attributes[a].name.startsWith('data-')) {
                    childInfo.dataAttrs[child.attributes[a].name] = child.attributes[a].value;
                }
            }
            result.children.push(childInfo);
        }

        // Also check: is this the module card grid?
        // Look for locked indicators among children
        var lockedCount = 0;
        var unlockedCount = 0;
        for (var j = 0; j < el.children.length; j++) {
            var c = el.children[j];
            var txt = c.textContent || '';
            // Locked courses might have a lock icon or "Locked" text
            if (txt.includes('🔒') || txt.includes('Locked') || txt.includes('locked')) {
                lockedCount++;
            } else {
                unlockedCount++;
            }
            // Check for lock SVG
            var svgs = c.querySelectorAll('svg');
            for (var k = 0; k < svgs.length; k++) {
                var paths = svgs[k].querySelectorAll('path');
                // Lock icon typically has specific path data
                for (var p = 0; p < paths.length; p++) {
                    var d = paths[p].getAttribute('d') || '';
                    if (d.includes('M12 17') || d.includes('lock') || d.includes('M18 8h-1V6')) {
                        lockedCount++;
                        unlockedCount--;
                        break;
                    }
                }
            }
        }
        result.lockedCount = lockedCount;
        result.unlockedCount = unlockedCount;

        return JSON.stringify(result);
    })()
    """)

    data = json.loads(result) if isinstance(result, str) else result
    os.makedirs("output/discover", exist_ok=True)
    with open("output/discover/skool-xpath-element.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Element: <{data.get('tag')}> class='{data.get('className')[:80]}'")
    print(f"Size: {data.get('w')}x{data.get('h')} at ({data.get('x')},{data.get('y')})")
    print(f"Children: {data.get('childCount')}")
    print(f"Locked: {data.get('lockedCount')}, Unlocked: {data.get('unlockedCount')}")
    print(f"Text preview: {data.get('text', '')[:200]}")
    print()

    for c in data.get('children', []):
        locked = '🔒' if any(k for k in ['🔒', 'Locked'] if k in c.get('text', '')) else '✅'
        print(f"  [{c['index']}] {locked} <{c['tag']}> {c['w']}x{c['h']} @ ({c['x']},{c['y']})")
        print(f"       text: {c['text'][:80]}")
        if c.get('linkHref'):
            print(f"       link: {c['linkHref']}")
        if c.get('dataAttrs'):
            print(f"       data: {c['dataAttrs']}")

    # Screenshot
    resp = await s.screenshot()
    if resp.data.screenshot:
        img = resp.data.screenshot
        if img.startswith("data:"): img = img.split(",", 1)[1]
        with open("output/discover/skool-xpath-screenshot.png", "wb") as f2:
            f2.write(base64.b64decode(img))
        print(f"\n  Screenshot: output/discover/skool-xpath-screenshot.png")

    await s.stop()

asyncio.run(main())
