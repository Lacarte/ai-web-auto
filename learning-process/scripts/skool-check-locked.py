"""Check which modules are locked vs unlocked on the classroom page. Same tab."""
import asyncio, json, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_web_auto_python.core.server import WebSocketServer
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
        var nd = document.getElementById('__NEXT_DATA__');
        if (!nd) return JSON.stringify({error: 'no __NEXT_DATA__'});
        var data = JSON.parse(nd.textContent);
        var pp = data.props.pageProps;
        var allCourses = pp.allCourses || [];
        var rdCourses = (pp.renderData && pp.renderData.allCourses) || [];

        var modules = [];
        for (var i = 0; i < allCourses.length; i++) {
            var c = allCourses[i];
            var meta = c.metadata || {};

            // Find matching renderData entry for extra info
            var rd = null;
            for (var j = 0; j < rdCourses.length; j++) {
                if (rdCourses[j].id === c.id) { rd = rdCourses[j]; break; }
            }
            var rdMeta = rd && rd.metadata ? rd.metadata : {};

            modules.push({
                id: c.id,
                slug: c.name,
                title: meta.title || c.name,
                state: c.state,
                public: c.public,
                hasAccess: meta.hasAccess,
                locked: meta.locked,
                unlockLevel: meta.unlockLevel || meta.level || null,
                metaKeys: Object.keys(meta),
                rdHasAccess: rdMeta.hasAccess,
                rdLocked: rdMeta.locked,
                rdMetaKeys: Object.keys(rdMeta)
            });
        }

        // Also check the DOM for "Unlock at Level" text on cards
        var cards = document.querySelectorAll('[class*="CourseContent"]');
        var domLockInfo = [];
        for (var k = 0; k < cards.length; k++) {
            var text = cards[k].textContent.trim();
            var isLocked = text.includes('Unlock at') || text.includes('🔒') || text.includes('Locked');
            var levelMatch = text.match(/Unlock at Level\\s*(\\d+)/i);
            domLockInfo.push({
                index: k,
                title: text.substring(0, 80),
                isLocked: isLocked,
                unlockLevel: levelMatch ? parseInt(levelMatch[1]) : null
            });
        }

        return JSON.stringify({modules: modules, domCards: domLockInfo});
    })()
    """)

    data = json.loads(result) if isinstance(result, str) else result

    print("=== Module Access Status ===\n")
    print(f"{'#':<3} {'Title':<40} {'hasAccess':<12} {'State':<8} {'DOM Lock'}")
    print("-" * 95)

    dom_cards = data.get('domCards', [])
    for i, m in enumerate(data.get('modules', [])):
        access = m.get('hasAccess')
        access_str = str(access) if access is not None else 'N/A'
        dom = dom_cards[i] if i < len(dom_cards) else {}
        dom_lock = f"Unlock Lv{dom.get('unlockLevel')}" if dom.get('isLocked') else "Open"
        print(f"{i+1:<3} {m['title'][:40]:<40} {access_str:<12} {m.get('state',''):<8} {dom_lock}")

    locked = sum(1 for d in dom_cards if d.get('isLocked'))
    unlocked = len(dom_cards) - locked
    print(f"\n  Total: {len(dom_cards)} modules, {unlocked} open, {locked} locked")

    # Show metadata keys for a locked vs unlocked module
    mods = data.get('modules', [])
    if mods:
        print(f"\n  Example metadata keys (first module): {mods[0].get('metaKeys')}")
        if len(mods) > 1:
            print(f"  Example metadata keys (second module): {mods[1].get('metaKeys')}")

    await s.stop()

asyncio.run(main())
