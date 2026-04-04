"""
Test HTML capture of a Skool lesson page as a single self-contained file.
Keep resource links active (don't base64 images, keep hrefs absolute).
Same tab throughout.
"""
import asyncio
import json
import sys
import os
import base64

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

    # Navigate to lesson (same tab)
    lesson_url = "https://www.skool.com/ai-video-creators/classroom/e22d16af?md=5ffde8748ff6467e95168c2b01de3385"
    print(f"=== Navigate to lesson (same tab) ===")
    await s.navigate(lesson_url)
    await asyncio.sleep(6)

    # Capture HTML as single file with active resource links
    print("\n=== Capture HTML ===")
    html_result = await run_js(s, """
    (async function() {
        // Clone the entire document
        var clone = document.documentElement.cloneNode(true);

        // Remove overlays / extension junk
        var removeSelectors = [
            '#automa-progress', '#automa-options-dialog', '#sdc-overlay',
            '[data-skool-dl-btn]', 'script[src*="automa"]'
        ];
        for (var i = 0; i < removeSelectors.length; i++) {
            var els = clone.querySelectorAll(removeSelectors[i]);
            for (var j = 0; j < els.length; j++) els[j].remove();
        }

        // === INLINE ALL CSS ===
        var allCSS = '';
        var sheets = Array.from(document.styleSheets);
        for (var si = 0; si < sheets.length; si++) {
            try {
                if (sheets[si].cssRules) {
                    for (var ri = 0; ri < sheets[si].cssRules.length; ri++) {
                        allCSS += sheets[si].cssRules[ri].cssText + '\\n';
                    }
                }
            } catch(e) {
                // Cross-origin stylesheet - fetch it
                if (sheets[si].href) {
                    try {
                        var r = await fetch(sheets[si].href);
                        allCSS += (await r.text()) + '\\n';
                    } catch(fe) {}
                }
            }
        }
        // Also grab inline <style> tags
        document.querySelectorAll('style').forEach(function(t) {
            allCSS += t.textContent + '\\n';
        });

        // Remove <link rel="stylesheet"> from clone (we inlined them)
        clone.querySelectorAll('link[rel="stylesheet"]').forEach(function(l) { l.remove(); });

        // Remove existing <style> from clone head (avoid duplication)
        clone.querySelectorAll('head > style').forEach(function(s) { s.remove(); });

        // Add single inlined <style>
        var styleEl = document.createElement('style');
        styleEl.textContent = allCSS;
        var head = clone.querySelector('head');
        if (head) head.appendChild(styleEl);

        // === FIX ALL LINKS TO ABSOLUTE ===
        clone.querySelectorAll('a[href]').forEach(function(link) {
            try {
                var href = link.getAttribute('href');
                if (href && !href.startsWith('http') && !href.startsWith('data:') && !href.startsWith('#') && !href.startsWith('javascript:')) {
                    link.setAttribute('href', new URL(href, window.location.href).href);
                }
            } catch(e) {}
        });

        // === FIX ALL IMAGES TO ABSOLUTE (keep links active, don't base64) ===
        clone.querySelectorAll('img').forEach(function(img) {
            var src = img.getAttribute('src');
            if (src && !src.startsWith('http') && !src.startsWith('data:')) {
                try { img.setAttribute('src', new URL(src, window.location.href).href); } catch(e) {}
            }
            // Fix srcset too
            var srcset = img.getAttribute('srcset');
            if (srcset) {
                var parts = srcset.split(',').map(function(part) {
                    var pieces = part.trim().split(/\\s+/);
                    if (pieces[0] && !pieces[0].startsWith('http') && !pieces[0].startsWith('data:')) {
                        try { pieces[0] = new URL(pieces[0], window.location.href).href; } catch(e) {}
                    }
                    return pieces.join(' ');
                });
                img.setAttribute('srcset', parts.join(', '));
            }
        });

        // === FIX BACKGROUND IMAGES IN INLINE STYLES ===
        clone.querySelectorAll('[style]').forEach(function(el) {
            var style = el.getAttribute('style');
            if (style && style.includes('url(')) {
                style = style.replace(/url\\(['"]?(?!data:|http)([^'")]+)['"]?\\)/g, function(match, p1) {
                    try { return "url('" + new URL(p1, window.location.href).href + "')"; } catch(e) { return match; }
                });
                el.setAttribute('style', style);
            }
        });

        // === EMBED VIDEO PLAYER ===
        // Check for video info in __NEXT_DATA__
        var ndEl = document.getElementById('__NEXT_DATA__');
        if (ndEl) {
            try {
                var nd = JSON.parse(ndEl.textContent);
                var pp = nd.props && nd.props.pageProps;
                var selMod = pp ? pp.selectedModule : null;
                if (pp && pp.course && pp.course.children && selMod) {
                    for (var ci = 0; ci < pp.course.children.length; ci++) {
                        var child = pp.course.children[ci];
                        if (child.course && child.course.id === selMod) {
                            var meta = child.course.metadata || {};
                            var videoLink = meta.videoLink || '';

                            // If there's a YouTube video, inject an embed iframe
                            if (videoLink.includes('youtube.com') || videoLink.includes('youtu.be')) {
                                var vidMatch = videoLink.match(/(?:youtu\\.be\\/|youtube\\.com\\/(?:watch\\?v=|embed\\/|v\\/))([^?&\\/]+)/);
                                if (vidMatch) {
                                    var coverEl = clone.querySelector('[class*="CoverImage"]');
                                    var playerWrapper = clone.querySelector('[class*="VideoPlayerWrapper"]') || clone.querySelector('[class*="VideoWrapper"]');
                                    var target = playerWrapper || (coverEl ? coverEl.parentElement : null);
                                    if (target) {
                                        target.innerHTML = '<div style="position:relative;width:100%;padding-bottom:56.25%;background:#000;border-radius:10px;overflow:hidden;">' +
                                            '<iframe style="position:absolute;top:0;left:0;width:100%;height:100%;border:none;" ' +
                                            'src="https://www.youtube.com/embed/' + vidMatch[1] + '?rel=0&modestbranding=1" ' +
                                            'frameborder="0" allowfullscreen allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"></iframe></div>';
                                    }
                                }
                            }

                            // Loom embed
                            if (videoLink.includes('loom.com')) {
                                var loomUrl = videoLink.replace('/share/', '/embed/');
                                var target2 = clone.querySelector('[class*="VideoPlayerWrapper"]') || clone.querySelector('[class*="VideoWrapper"]');
                                if (target2) {
                                    target2.innerHTML = '<div style="position:relative;width:100%;padding-bottom:56.25%;background:#000;border-radius:10px;overflow:hidden;">' +
                                        '<iframe style="position:absolute;top:0;left:0;width:100%;height:100%;border:none;" ' +
                                        'src="' + loomUrl + '" frameborder="0" allowfullscreen></iframe></div>';
                                }
                            }

                            break;
                        }
                    }
                }
            } catch(e) {}
        }

        // === REMOVE SCRIPTS (they won't work offline anyway) ===
        clone.querySelectorAll('script').forEach(function(s) { s.remove(); });

        // === ADD META CHARSET + BASE TAG ===
        var metaCharset = clone.querySelector('meta[charset]');
        if (!metaCharset) {
            var mc = document.createElement('meta');
            mc.setAttribute('charset', 'utf-8');
            head = clone.querySelector('head');
            if (head) head.insertBefore(mc, head.firstChild);
        }

        // Add base tag for relative resource resolution
        var baseTag = document.createElement('base');
        baseTag.href = window.location.origin;
        head = clone.querySelector('head');
        if (head) head.insertBefore(baseTag, head.firstChild);

        // === BUILD FINAL HTML ===
        var html = '<!DOCTYPE html>\\n<html' +
            (clone.getAttribute('lang') ? ' lang="' + clone.getAttribute('lang') + '"' : '') +
            (clone.getAttribute('dir') ? ' dir="' + clone.getAttribute('dir') + '"' : '') +
            '>\\n' + clone.innerHTML + '\\n</html>';

        return html;
    })()
    """)

    if html_result and isinstance(html_result, str) and html_result.startswith('<!DOCTYPE'):
        os.makedirs("output/test-html", exist_ok=True)
        out_path = "output/test-html/welcome-check-this-first.html"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html_result)
        size_kb = os.path.getsize(out_path) / 1024
        print(f"  SAVED: {out_path} ({size_kb:.0f} KB)")

        # Quick stats
        link_count = html_result.count('href="http')
        img_count = html_result.count('<img')
        style_count = html_result.count('<style')
        has_base = '<base' in html_result
        print(f"  Links (absolute): {link_count}")
        print(f"  Images: {img_count}")
        print(f"  Inline styles: {style_count}")
        print(f"  Has <base> tag: {has_base}")
        print(f"  Has inline CSS: {'<style>' in html_result}")
        print(f"  Scripts removed: {'<script' not in html_result}")
    else:
        print(f"  FAILED - result type: {type(html_result)}")
        if html_result:
            preview = str(html_result)[:300]
            print(f"  Preview: {preview}")

    # Take screenshot for comparison
    resp = await s.screenshot()
    if resp.data.screenshot:
        img = resp.data.screenshot
        if img.startswith("data:"): img = img.split(",", 1)[1]
        with open("output/test-html/lesson-screenshot.png", "wb") as f:
            f.write(base64.b64decode(img))
        print(f"  Screenshot: output/test-html/lesson-screenshot.png")

    print("\n=== DONE ===")
    await s.stop()


if __name__ == "__main__":
    asyncio.run(main())
