"""Full E2E test: Flow main page -> New Project -> Settings -> Type -> Create -> Download"""
import asyncio, json, base64, sys, os, time
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.stdout.reconfigure(encoding='utf-8', errors='replace') if hasattr(sys.stdout, 'reconfigure') else None
sys.path.insert(0, ".")
from ai_web_auto_backend.core.server import WebSocketServer

OUT = "output/learner"

async def ss(s, name):
    r = await s.screenshot()
    if r.data.screenshot:
        with open(f"{OUT}/{name}.png", "wb") as f:
            f.write(base64.b64decode(r.data.screenshot))

def p(r):
    return json.loads(r.data.extracted_data.get("result", "{}"))

async def find_and_cdp_click(s, js_find):
    """Run JS to find element pos, then CDP click it."""
    r = await s.execute_script(js_find)
    pos = p(r)
    if pos.get("x"):
        await s.click_cdp(pos["x"], pos["y"])
        return pos
    return None

async def main():
    os.makedirs(OUT, exist_ok=True)
    s = WebSocketServer()
    await s.start()
    await s.wait_for_connection(timeout=60)
    print("Connected!\n")

    # 1. Navigate to Flow
    print("1. Navigate to Flow main page...")
    await s.navigate("https://labs.google/fx/tools/flow")
    await asyncio.sleep(8)
    await ss(s, "e2e_01_flow")

    # 2. Click "New project" button
    print("2. Click New Project...")
    pos = await find_and_cdp_click(s, """
        var btns = document.querySelectorAll('button, a, [role=button], div[tabindex]');
        for (var b of btns) {
            var t = (b.textContent || '').trim().toLowerCase();
            if (t.includes('new project') || t.includes('new') || t.includes('create')) {
                var r = b.getBoundingClientRect();
                if (r.width > 30 && r.height > 30) {
                    return JSON.stringify({x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2), text:b.textContent.trim().slice(0,30)});
                }
            }
        }
        return JSON.stringify({error:'not found'});
    """)
    print(f"   Clicked: {pos}")
    await asyncio.sleep(5)
    await ss(s, "e2e_02_new")

    # 3. Open settings panel (Nano Banana button)
    print("\n3. Open settings panel...")
    pos = await find_and_cdp_click(s, """
        var btns = document.querySelectorAll('button[aria-haspopup]');
        for (var b of btns) {
            var r = b.getBoundingClientRect();
            if (r.y > 400 && r.width > 50) {
                return JSON.stringify({x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)});
            }
        }
        // Fallback: Nano Banana by text
        for (var b of document.querySelectorAll('button')) {
            if (b.textContent.includes('Nano')) {
                var r = b.getBoundingClientRect();
                return JSON.stringify({x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)});
            }
        }
        return JSON.stringify({error:'not found'});
    """)
    print(f"   Panel: {pos}")
    await asyncio.sleep(1.5)
    await ss(s, "e2e_03_panel")

    # 4. Select Image, 9:16, x2
    for name, sel in [("Image", '[role="tab"][id$="-trigger-IMAGE"]'),
                       ("9:16",  '[role="tab"][id$="-trigger-PORTRAIT"]'),
                       ("x2",    '[role="tab"][id$="-trigger-2"]')]:
        r = await s.execute_script(f"""
            var el = document.querySelector('{sel}');
            if (el) {{ var r = el.getBoundingClientRect(); return JSON.stringify({{x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2), state:el.getAttribute('data-state')}}); }}
            return JSON.stringify({{error:'not found', sel:'{sel}'}});
        """)
        pos = p(r)
        if pos.get("x"):
            await s.click_cdp(pos["x"], pos["y"])
            await asyncio.sleep(0.4)
            print(f"   {name}: clicked at ({pos['x']},{pos['y']}) was={pos.get('state')}")
        else:
            print(f"   {name}: NOT FOUND - {pos}")

    await ss(s, "e2e_04_settings")

    # Verify active tabs
    r = await s.execute_script("""
        var active = [];
        document.querySelectorAll('[role=tab][data-state=active]').forEach(function(t) {
            active.push(t.textContent.trim().slice(0,15));
        });
        return JSON.stringify(active);
    """)
    print(f"   Active tabs: {r.data.extracted_data.get('result')}")

    # 5. Close panel (Escape via CDP)
    print("\n5. Close panel...")
    # Use CDP Escape
    from ai_web_auto_backend.core.models import Command
    await s.send_command(Command(action="type_cdp", params={"text": "", "xpath": ""}))
    # Above just attaches debugger; now we need to send Escape directly
    # Actually let's just click outside the panel
    await s.click_cdp(100, 300)  # click on the canvas area
    await asyncio.sleep(0.5)
    await ss(s, "e2e_05_closed")

    # 6. Type prompt
    print("\n6. Type prompt...")
    await s.type_cdp(
        "A majestic dragon flying over a neon cyberpunk city at night",
        xpath="/html/body/div[1]/div[1]/div[5]/div/div/div[1]/div"
    )
    await asyncio.sleep(1)
    await ss(s, "e2e_06_typed")

    # 7. Click Create
    print("\n7. Click Create...")
    pos = await find_and_cdp_click(s, """
        for (var b of document.querySelectorAll('button')) {
            if (b.textContent.includes('arrow_forward') && b.textContent.includes('Create')) {
                var r = b.getBoundingClientRect();
                return JSON.stringify({x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)});
            }
        }
        return JSON.stringify({error:'not found'});
    """)
    print(f"   Create: {pos}")
    await asyncio.sleep(3)
    await ss(s, "e2e_07_sent")

    # 8. Poll for images
    print("\n8. Polling for images...")
    start = time.time()
    for i in range(40):
        await asyncio.sleep(3)
        elapsed = int(time.time() - start)
        r = await s.execute_script("""
            var big = [];
            document.querySelectorAll('img').forEach(function(i){
                if(i.getBoundingClientRect().width > 100 && i.src.includes('media.getMediaUrl'))
                    big.push(i.src.slice(0,150));
            });
            return JSON.stringify({count:big.length, urls:big});
        """)
        result = p(r)
        count = result.get("count", 0)
        if elapsed % 15 == 0 or count > 0:
            print(f"   [{elapsed}s] images={count}")
            await ss(s, f"e2e_poll_{elapsed:03d}s")
        if count > 0:
            print(f"\n   GENERATION COMPLETE! {count} images")
            await ss(s, "e2e_08_done")
            for j, url in enumerate(result.get("urls", [])):
                print(f"   Image {j+1}: {url}")
            break

    await s.stop()
    print("\nDone!")

asyncio.run(main())
