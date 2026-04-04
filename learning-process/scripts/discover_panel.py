"""Discover all Flow UI controls: modes, ratios, multipliers, loading, download."""
import asyncio, base64, json, sys, os
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.stdout.reconfigure(encoding='utf-8', errors='replace') if hasattr(sys.stdout, 'reconfigure') else None
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ai_web_auto_python.core.server import WebSocketServer

OUT = Path("output/learner")
URL = "https://labs.google/fx/tools/flow/project/fdd97a0e-a74c-483e-8869-261e6db306c2"

JS_DISCOVER_CONTROLS = r"""
function getXPath(el) {
    if (el.id) return '//*[@id="' + el.id + '"]';
    var parts = [];
    while (el && el.nodeType === 1) {
        var idx = 1, sib = el.previousSibling;
        while (sib) { if (sib.nodeType === 1 && sib.tagName === el.tagName) idx++; sib = sib.previousSibling; }
        parts.unshift(el.tagName.toLowerCase() + '[' + idx + ']');
        el = el.parentNode;
    }
    return '/' + parts.join('/');
}
var controls = { modes: [], ratios: [], multipliers: [], models: [], other: [] };
document.querySelectorAll('[role=tab],[role=radio],[role=option],button,[role=button],[data-state]').forEach(function(el) {
    var r = el.getBoundingClientRect();
    if (r.width < 5 || r.height < 5) return;
    var text = (el.textContent || '').trim();
    var info = {
        text: text.slice(0, 40), role: el.getAttribute('role') || '',
        ariaSelected: el.getAttribute('aria-selected') || '',
        ariaChecked: el.getAttribute('aria-checked') || '',
        dataState: el.getAttribute('data-state') || '',
        xpath: getXPath(el),
        rect: {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)}
    };
    if (text === 'Image' || text === 'Video') controls.modes.push(info);
    else if (['16:9','4:3','1:1','3:4','9:16'].some(function(r){return text.includes(r);})) controls.ratios.push(info);
    else if (['x1','x2','x3','x4'].indexOf(text) >= 0) controls.multipliers.push(info);
    else if (text.includes('Nano') || text.includes('Banana')) controls.models.push(info);
    else if (r.y > 300 && r.y < 700 && r.width > 20) controls.other.push(info);
});
return JSON.stringify(controls);
"""

JS_FIND_LOADERS = r"""
var loaders = [];
document.querySelectorAll('*').forEach(function(el) {
    var cls = (el.className || '').toString().toLowerCase();
    var style = window.getComputedStyle(el);
    if (cls.includes('loading') || cls.includes('spinner') || cls.includes('progress') ||
        cls.includes('skeleton') || cls.includes('pulse') || cls.includes('generat') ||
        style.animationName !== 'none' || el.getAttribute('role') === 'progressbar') {
        var r = el.getBoundingClientRect();
        loaders.push({
            tag: el.tagName.toLowerCase(),
            className: cls.slice(0, 100),
            animation: style.animationName || '',
            visible: r.width > 0 && r.height > 0,
            rect: {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)}
        });
    }
});
return JSON.stringify(loaders);
"""

JS_FIND_DOWNLOADS = r"""
var dl = [];
document.querySelectorAll('img').forEach(function(el) {
    var r = el.getBoundingClientRect();
    if (r.width > 50) dl.push({type:'img', src:(el.src||'').slice(0,200), srcset:(el.srcset||'').slice(0,100), alt:el.alt||'', w:Math.round(r.width), h:Math.round(r.height)});
});
document.querySelectorAll('[download],a[href],button').forEach(function(el) {
    var t = (el.textContent + (el.getAttribute('aria-label')||'')).toLowerCase();
    if (t.includes('download') || t.includes('save') || t.includes('export') || el.hasAttribute('download'))
        dl.push({type:'dl_btn', text:el.textContent.trim().slice(0,40), href:(el.href||'').slice(0,150), tag:el.tagName});
});
document.querySelectorAll('canvas').forEach(function(el) {
    var r = el.getBoundingClientRect();
    if (r.width > 50) dl.push({type:'canvas', w:Math.round(r.width), h:Math.round(r.height)});
});
document.querySelectorAll('[src*="blob"],[href*="blob"]').forEach(function(el) {
    dl.push({type:'blob', src:(el.src||el.href||'').slice(0,150), tag:el.tagName});
});
return JSON.stringify(dl);
"""

def p(data): return json.loads(data.get("result", "{}") if isinstance(data, dict) else "{}")

async def screenshot(s, name):
    ss = await s.screenshot()
    if ss.data.screenshot:
        path = OUT / f"{name}.png"
        path.write_bytes(base64.b64decode(ss.data.screenshot))
        print(f"  [SCREENSHOT] {path}")

async def click_by_text(s, text_match, log=""):
    r = await s.execute_script(f"""
        var all = document.querySelectorAll('[role=tab],[role=radio],[role=option],button,[role=button]');
        for (var el of all) {{
            var t = el.textContent.trim();
            if (t === '{text_match}' || t.includes('{text_match}')) {{
                el.click();
                return JSON.stringify({{clicked:true, text:t.slice(0,30), selected:el.getAttribute('aria-selected'), checked:el.getAttribute('aria-checked')}});
            }}
        }}
        return JSON.stringify({{clicked:false}});
    """)
    result = p(r.data.extracted_data)
    print(f"  {log}: {result}")
    return result

async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    s = WebSocketServer()
    await s.start()
    await s.wait_for_connection(timeout=60)
    print("Connected!\n")

    await s.navigate(URL)
    await asyncio.sleep(8)
    await screenshot(s, "01_landing")

    # ── Open settings panel ──────────────────────────────────────────────
    print("=== Opening settings panel (click Nano Banana) ===")
    await click_by_text(s, "Nano Banana", "Open panel")
    await asyncio.sleep(2)
    await screenshot(s, "02_panel_open")

    # ── Discover controls ────────────────────────────────────────────────
    print("\n=== Discovering panel controls ===")
    r = await s.execute_script("return " + JS_DISCOVER_CONTROLS.replace("\n", " "))
    controls = p(r.data.extracted_data)
    (OUT / "panel_controls.json").write_text(json.dumps(controls, indent=2))

    for cat in ["modes", "ratios", "multipliers", "models"]:
        items = controls.get(cat, [])
        print(f"\n  {cat.upper()} ({len(items)}):")
        for el in items:
            sel = "SELECTED" if el.get("ariaSelected") == "true" or el.get("dataState") == "active" else ""
            print(f'    "{el["text"]}" {sel} xpath={el["xpath"][:70]}')

    # ── Test: Select Image mode ──────────────────────────────────────────
    print("\n=== Selecting Image mode ===")
    await click_by_text(s, "Image", "Image mode")
    await asyncio.sleep(0.5)
    await screenshot(s, "03_image_mode")

    # ── Test: Select 9:16 ratio ──────────────────────────────────────────
    print("\n=== Selecting 9:16 ratio ===")
    await click_by_text(s, "9:16", "9:16 ratio")
    await asyncio.sleep(0.5)
    await screenshot(s, "04_ratio_916")

    # ── Test: Select x1 multiplier ───────────────────────────────────────
    print("\n=== Selecting x1 multiplier ===")
    await click_by_text(s, "x1", "x1 multiplier")
    await asyncio.sleep(0.5)
    await screenshot(s, "05_x1_selected")

    # ── Close panel by clicking outside ──────────────────────────────────
    print("\n=== Closing panel ===")
    await s.execute_script("document.body.click(); return 'ok';")
    await asyncio.sleep(1)
    await screenshot(s, "06_panel_closed")

    # ── Type and send ────────────────────────────────────────────────────
    print("\n=== Typing prompt ===")
    XPATH_INPUT = "/html/body/div[1]/div[1]/div[5]/div/div/div[1]/div"
    await s.type_cdp("A majestic dragon in a crystal cave, 9:16 portrait", xpath=XPATH_INPUT)
    await asyncio.sleep(1)
    await screenshot(s, "07_typed")

    print("\n=== Clicking Create ===")
    await click_by_text(s, "arrow_forwardCreate", "Send")
    await asyncio.sleep(3)
    await screenshot(s, "08_after_send")

    # ── Track loading animation ──────────────────────────────────────────
    print("\n=== Tracking generation (loading CSS animations) ===")
    import time
    start = time.time()
    for i in range(40):
        await asyncio.sleep(3)
        elapsed = int(time.time() - start)

        r = await s.execute_script("return " + JS_FIND_LOADERS.replace("\n", " "))
        loaders = p(r.data.extracted_data)
        visible_loaders = [l for l in loaders if l.get("visible") and l.get("animation") != "none"] if isinstance(loaders, list) else []

        r2 = await s.execute_script("""return JSON.stringify({
            imgs: document.querySelectorAll('img').length,
            bigImgs: Array.from(document.querySelectorAll('img')).filter(function(i){return i.getBoundingClientRect().width > 100;}).length
        });""")
        state = p(r2.data.extracted_data)

        print(f"  [{elapsed:3d}s] loaders={len(visible_loaders)} imgs={state.get('imgs',0)} bigImgs={state.get('bigImgs',0)}")

        if i % 5 == 0:
            await screenshot(s, f"09_poll_{elapsed:03d}s")

        if state.get("bigImgs", 0) > 0 and len(visible_loaders) == 0:
            print(f"  GENERATION COMPLETE at {elapsed}s!")
            await screenshot(s, "10_generation_done")
            break

    # ── Find download references ─────────────────────────────────────────
    print("\n=== Finding download references ===")
    r3 = await s.execute_script("return " + JS_FIND_DOWNLOADS.replace("\n", " "))
    downloads = p(r3.data.extracted_data)
    (OUT / "download_refs.json").write_text(json.dumps(downloads, indent=2))

    if isinstance(downloads, list):
        for d in downloads:
            print(f"  {d.get('type')}: {d}")

    # Try right-clicking an image to see context menu options
    r4 = await s.execute_script(r"""
        var imgs = document.querySelectorAll('img');
        var bigImgs = [];
        for (var img of imgs) {
            var r = img.getBoundingClientRect();
            if (r.width > 100 && r.height > 100) {
                bigImgs.push({
                    src: (img.src||'').slice(0, 300),
                    srcset: (img.srcset||'').slice(0, 200),
                    naturalWidth: img.naturalWidth,
                    naturalHeight: img.naturalHeight,
                    alt: img.alt || '',
                    crossOrigin: img.crossOrigin || '',
                    loading: img.loading || '',
                    w: Math.round(r.width), h: Math.round(r.height)
                });
            }
        }
        return JSON.stringify(bigImgs);
    """)
    big_imgs = p(r4.data.extracted_data)
    print(f"\n  Large images ({len(big_imgs) if isinstance(big_imgs, list) else 0}):")
    if isinstance(big_imgs, list):
        for img in big_imgs:
            print(f"    {img.get('naturalWidth')}x{img.get('naturalHeight')} src={img.get('src','')[:100]}")
        (OUT / "big_images.json").write_text(json.dumps(big_imgs, indent=2))

    await screenshot(s, "11_final")

    # ── Summary ──────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("DISCOVERY COMPLETE")
    print("=" * 60)
    print(f"Files saved to {OUT}/")

    await s.stop()

if __name__ == "__main__":
    asyncio.run(main())
