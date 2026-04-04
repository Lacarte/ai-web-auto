"""Test settings panel navigation using CDP clicks."""
import asyncio, json, base64, sys, os
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.stdout.reconfigure(encoding='utf-8', errors='replace') if hasattr(sys.stdout, 'reconfigure') else None
sys.path.insert(0, ".")
from ai_web_auto_python.core.server import WebSocketServer

URL = "https://labs.google/fx/tools/flow/project/fdd97a0e-a74c-483e-8869-261e6db306c2"

async def main():
    s = WebSocketServer()
    await s.start()
    await s.wait_for_connection(timeout=30)
    print("Connected!")

    await s.navigate(URL)
    await asyncio.sleep(8)

    # Find the Nano Banana button position
    r = await s.execute_script("""
        var xpath = '/html/body/div[1]/div[1]/div[5]/div/div/div[2]/div[2]/button[1]';
        var el = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
        if (!el) {
            var btns = document.querySelectorAll('button[aria-haspopup]');
            for (var b of btns) { if (b.getBoundingClientRect().y > 500) { el = b; break; } }
        }
        if (el) {
            var r = el.getBoundingClientRect();
            return JSON.stringify({found:true, x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2), w:Math.round(r.width)});
        }
        return JSON.stringify({found:false});
    """)
    btn = json.loads(r.data.extracted_data.get("result", "{}"))
    print(f"Settings button: {btn}")

    if btn.get("found"):
        # Click via CDP for trusted event
        print(f"Clicking via CDP at ({btn['x']}, {btn['y']})...")
        cr = await s.click_cdp(btn["x"], btn["y"])
        print(f"CDP click: {cr.status}")
        await asyncio.sleep(1.5)

        # Screenshot
        ss = await s.screenshot()
        if ss.data.screenshot:
            with open("output/learner/panel_cdp.png", "wb") as f:
                f.write(base64.b64decode(ss.data.screenshot))
            print("Screenshot saved")

        # Check DOM for panel
        r2 = await s.execute_script("""
            var tabs = document.querySelectorAll('[role=tab]').length;
            var menus = document.querySelectorAll('[role=menu]').length;
            var radix = document.querySelectorAll('[data-radix-menu-content]').length;
            var flowTabs = document.querySelectorAll('.flow_tab_slider_trigger').length;
            var tabDetails = [];
            document.querySelectorAll('[role=tab]').forEach(function(t) {
                tabDetails.push({text:t.textContent.trim().slice(0,20), controls:(t.getAttribute('aria-controls')||'').slice(-30), state:t.getAttribute('data-state')});
            });
            return JSON.stringify({tabs:tabs, menus:menus, radix:radix, flowTabs:flowTabs, tabDetails:tabDetails});
        """)
        data = json.loads(r2.data.extracted_data.get("result", "{}"))
        print(f"DOM: tabs={data.get('tabs')}, menus={data.get('menus')}, radix={data.get('radix')}, flowTabs={data.get('flowTabs')}")
        for t in data.get("tabDetails", []):
            print(f"  tab: '{t['text']}' ...{t['controls']} state={t['state']}")

        # If panel opened, click 9:16 and x2
        if data.get("tabs", 0) > 0:
            # Click 9:16
            r3 = await s.execute_script("""
                var tabs = document.querySelectorAll('[role=tab]');
                for (var t of tabs) {
                    if (t.textContent.trim().includes('9:16')) { t.click(); return JSON.stringify({clicked:'9:16'}); }
                }
                return JSON.stringify({clicked:false});
            """)
            print(f"9:16: {r3.data.extracted_data.get('result')}")
            await asyncio.sleep(0.5)

            # Click x2
            r4 = await s.execute_script("""
                var tabs = document.querySelectorAll('[role=tab]');
                for (var t of tabs) {
                    if (t.textContent.trim() === 'x2') { t.click(); return JSON.stringify({clicked:'x2'}); }
                }
                return JSON.stringify({clicked:false});
            """)
            print(f"x2: {r4.data.extracted_data.get('result')}")
            await asyncio.sleep(0.5)

            # Screenshot after selections
            ss2 = await s.screenshot()
            if ss2.data.screenshot:
                with open("output/learner/panel_selected.png", "wb") as f:
                    f.write(base64.b64decode(ss2.data.screenshot))
                print("Selection screenshot saved")

            # Verify states
            r5 = await s.execute_script("""
                var results = [];
                document.querySelectorAll('[role=tab]').forEach(function(t) {
                    if (t.getAttribute('data-state') === 'active')
                        results.push(t.textContent.trim().slice(0,15));
                });
                return JSON.stringify({active: results});
            """)
            print(f"Active tabs: {r5.data.extracted_data.get('result')}")
        else:
            print("Panel did NOT open")

    await s.stop()

asyncio.run(main())
