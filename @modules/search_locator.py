"""Find and use any site's search box like a person: "look, click, see where the cursor went, type, press the
button next to it" (the user's visual idea, made measurable, 2026-10-29).

1. LOOK   - scan what is drawn on screen for search bars: wide short rectangles near the top, a magnifier icon (an SVG
            circle + a line, or an icon named search/magnifier/loupe) inside or beside them, hint text (placeholder /
            label / visible text: "Search", "Find a good deal", "What can we help you find?"), inside a search form,
            not covered by anything. Every candidate is scored; the best wins - never the first match.
            The site's own declarations help: <link rel="search"> (OpenSearch) and JSON-LD SearchAction name the query
            parameter (q, k, _nkw, st...).
2. FOCUS  - click the best bar (trusted click) and ask the browser which element now has the keyboard cursor
            (document.activeElement, through shadow roots). That element is where the text goes. If the click only
            opened a panel (a button that reveals the box), look again inside the opened page and click the box.
3. TYPE   - clear it, type the query (trusted CDP insertText, varied speed), read the value back: it must be exactly the
            query or nothing is submitted.
4. SUBMIT - click the button next to the box (closest, magnifier or "search"/"submit", not Clear/voice/camera, nothing
            covering it); if there is none, a trusted Enter.
5. PROOF  - the URL changes and carries the query, or the page title/results do. Otherwise it was not a search.
A screenshot of the page is saved as evidence. Robot checks / sign-in walls are reported, never bypassed.
"""
from __future__ import annotations

import asyncio
import base64
import json
import random
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus

import module_kit

SCAN_JS = r"""JSON.stringify((() => {
  const HINT = /search|find|looking for|what can we help|keyword|model number|item number|good deal|shop for/i;
  const BAD = /\b(e-?mail|password|zip|postal|phone|newsletter|subscribe|coupon|promo|quantity|qty|gift card|first name|last name)\b/i;
  const FACET = /facet|filter|refine|sidebar|brand/i;
  const deep = (root, sel, out = []) => { for (const e of root.querySelectorAll(sel)) out.push(e);
    for (const h of root.querySelectorAll('*')) if (h.shadowRoot) deep(h.shadowRoot, sel, out); return out; };
  const vis = e => { const r = e.getBoundingClientRect(), cs = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && +cs.opacity !== 0; };
  const pointOf = e => { const r = e.getBoundingClientRect(); return {x: Math.round(r.left + Math.min(r.width * 0.35, 120)), y: Math.round(r.top + r.height / 2)}; };
  const onTop = e => { const p = pointOf(e); if (p.y < 0 || p.y > innerHeight || p.x < 0 || p.x > innerWidth) return false;
    let h = document.elementFromPoint(p.x, p.y); while (h && h.shadowRoot && h.shadowRoot.elementFromPoint) { const d = h.shadowRoot.elementFromPoint(p.x, p.y); if (!d || d === h) break; h = d; }
    return !!h && (h === e || e.contains(h) || h.contains(e) || (e.labels && [...e.labels].some(l => l.contains(h)))); };
  const magnifierIn = root => {
    for (const s of root.querySelectorAll('svg')) {
      if (s.querySelector('circle') && s.querySelector('line, path, polyline, rect')) return true;
      const t = (s.getAttribute('aria-label') || '') + ' ' + ((s.querySelector('title') || {}).textContent || '') + ' ' + (s.getAttribute('class') || '') + ' ' + (s.getAttribute('data-testid') || '');
      if (/search|magnif|loupe/i.test(t)) return true;
      const u = s.querySelector('use'); if (u && /search|magnif|loupe/i.test(u.getAttribute('href') || u.getAttribute('xlink:href') || '')) return true;
    }
    return !![...root.querySelectorAll('i, span, img')].find(i => /search|magnif|loupe/i.test(((i.className || '').toString()) + ' ' + (i.getAttribute('alt') || '') + ' ' + (i.getAttribute('src') || '')));
  };
  // the site's own declarations of its search parameter
  let param = null, template = null;
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    try { const j = JSON.parse(s.textContent); const all = Array.isArray(j) ? j : [j, ...(j['@graph'] || [])];
      for (const n of all) { const a = n && n.potentialAction; const acts = Array.isArray(a) ? a : [a];
        for (const x of acts) if (x && /SearchAction/.test(x['@type'] || '')) { template = typeof x.target === 'string' ? x.target : (x.target && x.target.urlTemplate) || null; } } } catch (e) {}
  }
  if (template) { const m = template.match(/[?&]([^=&]+)=\{search_term/); param = m ? m[1] : null; }
  const osd = document.querySelector('link[rel="search"][type*="opensearch"]');
  const PARAMS = ['q', 'query', 'k', '_nkw', 'st', 'search_query', 'keyword', 'keywords', 's', 'term', 'searchTerm', 'text', 'd'];
  const cands = [];
  for (const e of deep(document, 'input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=submit]):not([type=button]), textarea, [contenteditable="true"], [role=searchbox], [role=combobox], button, [role=button], a[href="#"], div[tabindex]')) {
    if (!vis(e)) continue;
    const r = e.getBoundingClientRect();
    const isText = /^(INPUT|TEXTAREA)$/.test(e.tagName) || e.isContentEditable || /searchbox|combobox/.test(e.getAttribute('role') || '');
    const label = [e.getAttribute('placeholder'), e.getAttribute('aria-label'), e.getAttribute('title'), e.getAttribute('name'), e.id,
                   isText ? '' : (e.innerText || '').slice(0, 60), (e.labels && e.labels[0] ? e.labels[0].innerText : '')].filter(Boolean).join(' | ');
    if (!isText && !HINT.test(label)) continue;          // a non-input only counts if it says "search"/"find..."
    if (BAD.test(label) || /^(email|password|tel|number|url)$/i.test(e.type || '')) continue;
    if (r.width < 100 || r.height < 18 || r.height > 90) continue;
    const why = [];
    let score = 0;
    const top = onTop(e); if (!top && !(isText && r.top >= 0 && r.top < innerHeight)) continue;
    if (top) { score += 3; why.push('on top'); }
    if (isText) { score += 3; why.push('text box'); }
    if ((e.type || '') === 'search' || /searchbox/.test(e.getAttribute('role') || '')) { score += 2; why.push('type=search'); }
    if (e.name && param && e.name === param) { score += 4; why.push('name = declared param ' + param); }
    else if (e.name && PARAMS.includes(e.name)) { score += 2; why.push('name=' + e.name); }
    if (HINT.test(label)) { score += 3; why.push('hint "' + label.slice(0, 50) + '"'); }
    const ratio = r.width / r.height; if (ratio >= 5) { score += 2; why.push('bar shape'); }
    score += Math.min(2, r.width / 400);
    if (r.top < 220) { score += 2; why.push('near the top'); }
    if (e.closest('header, nav, [role=banner], [class*=header i], [id*=header i]')) { score += 1; why.push('in header'); }
    if (e.closest('search, form[role=search], [role=search], form[action*=search i]')) { score += 2; why.push('in a search form'); }
    let box = e, mag = magnifierIn(e);
    for (let i = 0; i < 4 && !mag && box.parentElement; i++) { box = box.parentElement; const br = box.getBoundingClientRect();
      if (br.width > r.width + 220 || br.height > r.height * 3 + 20) break; mag = magnifierIn(box); }
    if (mag) { score += 3; why.push('magnifier icon'); }
    if (e.closest('aside, [class*=facet i], [class*=filter i], [class*=refine i], [id*=facet i]') || FACET.test(e.id || '')) { score -= 5; why.push('facet/filter box (-)'); }
    const p = pointOf(e);
    cands.push({score: Math.round(score * 10) / 10, why, tag: e.tagName, id: e.id || null, name: e.getAttribute('name'), type: e.getAttribute('type'),
      label: label.slice(0, 80), x: p.x, y: p.y, rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)], text_box: isText});
  }
  cands.sort((a, b) => b.score - a.score);
  return {url: location.href, title: document.title, declared_param: param, declared_template: template, opensearch: osd ? osd.href : null,
          candidates: cands.slice(0, 6)};
})())"""

FOCUS_JS = r"""JSON.stringify((() => {
  let a = document.activeElement; while (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement;
  if (!a || a === document.body) return null;
  const r = a.getBoundingClientRect();
  const text = (/^(INPUT|TEXTAREA)$/.test(a.tagName) && !/^(checkbox|radio|submit|button|hidden|email|password)$/i.test(a.type || '')) || a.isContentEditable;
  return {tag: a.tagName, id: a.id || null, name: a.getAttribute('name'), type: a.getAttribute('type'), placeholder: a.getAttribute('placeholder'),
          aria: a.getAttribute('aria-label'), text_box: !!text, value: a.isContentEditable ? a.innerText : (a.value ?? null),
          rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]};
})())"""

FOCUS_BEST_JS = r"""JSON.stringify(((x, y) => {
  let e = document.elementFromPoint(x, y);
  while (e && e.shadowRoot && e.shadowRoot.elementFromPoint) { const d = e.shadowRoot.elementFromPoint(x, y); if (!d || d === e) break; e = d; }
  if (e && !/^(INPUT|TEXTAREA)$/.test(e.tagName) && !e.isContentEditable) e = e.querySelector('input, textarea') || (e.closest('label') || {}).control || e;
  if (!e || !e.focus) return null;
  e.focus();
  let a = document.activeElement; while (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement;
  if (!a || a === document.body) return null;
  const r = a.getBoundingClientRect();
  return {tag: a.tagName, id: a.id || null, name: a.getAttribute('name'), type: a.getAttribute('type'), placeholder: a.getAttribute('placeholder'),
          aria: a.getAttribute('aria-label'), text_box: /^(INPUT|TEXTAREA)$/.test(a.tagName) || a.isContentEditable, value: a.value ?? null,
          rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]};
})(__X__, __Y__))"""

SUBMIT_JS = r"""JSON.stringify((() => {
  let a = document.activeElement; while (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement;
  if (!a) return null;
  const ir = a.getBoundingClientRect();
  const BADB = /clear|reset|voice|\bmic|microphone|camera|image|photo|lens|scan|cancel|close|back|barcode|upload/i;
  const GOOD = /search|submit|\bgo\b|find/i;
  const vis = e => { const r = e.getBoundingClientRect(), cs = getComputedStyle(e); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const mag = e => [...e.querySelectorAll('svg')].some(s => (s.querySelector('circle') && s.querySelector('line, path, polyline')) ||
    /search|magnif|loupe/i.test((s.getAttribute('aria-label') || '') + (s.getAttribute('class') || '') + ((s.querySelector('title') || {}).textContent || '')))
    || [...e.querySelectorAll('i, span, img')].some(i => /search|magnif|loupe/i.test(((i.className || '').toString()) + (i.getAttribute('alt') || '')));
  const scope = new Set();
  if (a.form) a.form.querySelectorAll('button, input[type=submit], [role=button]').forEach(b => scope.add(b));
  let up = a; for (let i = 0; i < 5 && up.parentElement; i++) { up = up.parentElement; const r = up.getBoundingClientRect();
    if (r.height > ir.height * 3 + 30) break; up.querySelectorAll('button, input[type=submit], [role=button], a').forEach(b => scope.add(b)); }
  document.querySelectorAll('button, input[type=submit], [role=button]').forEach(b => { const r = b.getBoundingClientRect();
    if (Math.abs((r.top + r.height / 2) - (ir.top + ir.height / 2)) < 40 && r.left > ir.left - 10 && r.left < ir.right + 160) scope.add(b); });
  const out = [];
  for (const b of scope) {
    if (b === a || !vis(b)) continue;
    const r = b.getBoundingClientRect();
    const label = [b.getAttribute('aria-label'), b.getAttribute('title'), b.id, (b.className || '').toString(), (b.innerText || b.value || '').slice(0, 30),
                   b.getAttribute('data-testid'), b.getAttribute('type')].filter(Boolean).join(' | ');
    if (BADB.test(label.replace(/search-?(ai|button|btn|icon|submit)/ig, ''))) continue;
    const x = Math.round(r.left + r.width / 2), y = Math.round(r.top + r.height / 2);
    const h = document.elementFromPoint(x, y);
    if (!h || !(h === b || b.contains(h) || h.contains(b))) continue;
    let score = 0; const why = [];
    if (GOOD.test(label)) { score += 3; why.push('says search/submit'); }
    if (mag(b)) { score += 3; why.push('magnifier'); }
    if ((b.getAttribute('type') || '') === 'submit') { score += 2; why.push('type=submit'); }
    if (a.form && b.form === a.form) { score += 2; why.push('same form'); }
    const dx = Math.max(0, r.left - ir.right), dy = Math.abs((r.top + r.height / 2) - (ir.top + ir.height / 2));
    score -= (dx + dy) / 100;
    if (score < 2) continue;
    out.push({score: Math.round(score * 10) / 10, why, label: label.slice(0, 80), x, y, rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]});
  }
  out.sort((p, q) => q.score - p.score);
  return {box: [Math.round(ir.left), Math.round(ir.top), Math.round(ir.width), Math.round(ir.height)], buttons: out.slice(0, 4)};
})())"""

STATE_JS = r"""JSON.stringify((() => {
  const body = (document.body && document.body.innerText) || '';
  const head = body.slice(0, 2500) + ' ' + document.title;
  let blocked = null;
  if (/identity\.walmart\.com|\/account\/login|\/signin|\/ap\/signin/i.test(location.href)) blocked = 'sign-in wall';
  else if (/just a moment|performing security verification|checking your browser/i.test(head)) blocked = 'Cloudflare security check';
  else if (/press\s*&\s*hold|robot or human|are you a human|verify you are (a )?human|access denied|enter the characters you see/i.test(head)) blocked = 'robot check';
  return {url: location.href, title: document.title, ready: document.readyState, blocked};
})())"""


async def _js(server, script: str):
    return await module_kit._run_script(server, script)


async def screenshot(server, path: Path) -> str | None:
    try:
        resp = await server.screenshot()
        img = getattr(resp.data, "screenshot", None) or ""
        if img.startswith("data:"):
            img = img.split(",", 1)[1]
        if img:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(img))
            return str(path)
    except Exception:
        pass
    return None


async def look(server) -> dict:
    """Step 1: the scored search-bar candidates on screen (plus the site's declared search parameter)."""
    return await _js(server, SCAN_JS)


async def focus_test(server, cand: dict) -> dict | None:
    """Step 2: click a candidate like a person, return the element that got the keyboard cursor."""
    await module_kit.think(0.3, 0.9)
    await server.click_cdp(cand["x"], cand["y"])
    await asyncio.sleep(0.7)
    return await _js(server, FOCUS_JS)


async def to_top(server, max_bursts: int = 30) -> None:
    """Go back up to the top of the page like a person (wheel flicks up) - search bars live in the header, and a page
    left scrolled down hides them (live 2026-10-29: Best Buy picked another box)."""
    for _ in range(max_bursts):
        y = await _js(server, "JSON.stringify(scrollY)")
        if not y or y <= 2:
            return
        r = await module_kit._command(server, "wheel", {"x": 640, "y": 360, "notches": random.randint(8, 14),
                                                        "delta_y": -random.randint(100, 125), "notch_delay_ms": random.randint(20, 60)})
        if r.status != "success":
            break
        await asyncio.sleep(random.uniform(0.15, 0.4))
    await _js(server, "JSON.stringify(scrollTo({top: 0, behavior: 'instant'}) || 1)")
    await asyncio.sleep(0.4)


async def plan(server, evidence: Path | None = None) -> dict:
    """Look + focus test, no typing: where the cursor lands and which button would be clicked. For dry runs."""
    await module_kit.bring_to_front(server)
    await to_top(server)
    seen = await look(server)
    out = {"url": seen["url"], "declared_param": seen.get("declared_param"), "declared_template": seen.get("declared_template"),
           "opensearch": seen.get("opensearch"), "candidates": seen["candidates"], "opened_panel": False}
    if evidence:
        out["screenshot"] = await screenshot(server, evidence)
    if not seen["candidates"]:
        out["result"] = "no search bar found on screen"
        return out
    best = seen["candidates"][0]
    f = await focus_test(server, best)
    if not (f and f.get("text_box")):  # the click opened something (a panel / overlay): look again
        again = await look(server)
        boxes = [c for c in again["candidates"] if c["text_box"]]
        out["opened_panel"] = True
        if boxes:
            f = await focus_test(server, boxes[0])
            out["after_open"] = boxes[0]
    if not (f and f.get("text_box")) and best.get("text_box"):
        # the site ignored the click (live 2026-10-29: Walmart kept the cursor on <body> although the box was on top and
        # the click was delivered): the page puts the cursor in the scored box itself; typing stays real keyboard input
        f = await _js(server, FOCUS_BEST_JS.replace("__X__", str(best["x"])).replace("__Y__", str(best["y"])))
        out["focus_via"] = "page focus() - the site ignored the click"
    out["focused"] = f
    if f and f.get("text_box"):
        out["submit"] = await _js(server, SUBMIT_JS)
        out["result"] = "ready"
    else:
        out["result"] = "clicked the bar but no text box got the cursor"
    return out


SUGGEST_JS = r"""JSON.stringify(((words) => {
  let a = document.activeElement; while (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement;
  if (!a) return null;
  const ir = a.getBoundingClientRect();
  const out = [];
  for (const e of document.querySelectorAll('a[href], [role=option], li, button')) {
    const r = e.getBoundingClientRect();
    if (r.width < 80 || r.height < 14 || r.top < ir.bottom - 4 || r.top > ir.bottom + 650) continue;
    if (r.right < ir.left - 40 || r.left > ir.right + 40) continue;
    const t = (e.innerText || '').replace(/\s+/g, ' ').trim().toLowerCase();
    if (!t || t.length > 200) continue;
    const x = Math.round(r.left + Math.min(r.width / 2, 160)), y = Math.round(r.top + r.height / 2);
    const h = document.elementFromPoint(x, y); if (!h || !(h === e || e.contains(h) || h.contains(e))) continue;
    const all = /see all|all results|search for|view all/.test(t);
    const has = words.every(w => t.includes(w));
    if (!all && !has) continue;
    out.push({text: t.slice(0, 120), x, y, score: (all ? 5 : 0) + (has ? 3 : 0) - (r.top - ir.bottom) / 400});
  }
  out.sort((p, q) => q.score - p.score);
  return out.slice(0, 5);
})(__WORDS__))"""

HITS_JS = r"""JSON.stringify(((words) => {
  const lines = ((document.body && document.body.innerText) || '').toLowerCase().split('\n');
  const hits = lines.filter(l => l.length > 12 && words.every(w => l.includes(w))).length;
  return {hits, len: lines.length, sig: lines.slice(0, 400).join('|').length};
})(__WORDS__))"""


async def _hits(server, query: str) -> dict:
    words = [w for w in re.split(r"\W+", query.lower()) if len(w) >= 3 or w.isdigit()]
    words = [w for w in words if any(ch.isdigit() for ch in w)] or words  # model numbers decide ('3060')
    try:
        return await _js(server, HITS_JS.replace("__WORDS__", json.dumps(words)))
    except RuntimeError:
        return {"hits": 0, "len": 0, "sig": 0}


def _carries(url: str, title: str, query: str) -> bool:
    words = [w for w in re.split(r"\W+", query.lower()) if w]
    hay = unquote_plus(url).lower() + " " + (title or "").lower()
    return all(w in hay for w in words)


async def search(server, query: str, settings: dict | None = None, evidence: Path | None = None, timeout: float = 25.0) -> dict:
    """Steps 1-5. Returns {ok, how, url, why, plan}. Never submits unless the box holds exactly the query."""
    settings = settings or {}
    st = await _js(server, STATE_JS)
    if st.get("blocked"):
        return {"ok": False, "why": st["blocked"], "url": st["url"], "blocked": True}
    p = await plan(server, evidence)
    if p.get("result") != "ready":
        return {"ok": False, "why": p.get("result"), "plan": p}
    f = p["focused"]
    if (f.get("value") or "").strip():
        await module_kit._command(server, "key", {"key": "a", "ctrl": True})
        await module_kit._command(server, "key", {"key": "Backspace"})
    lo, hi = settings.get("type_ms_per_char", [45, 140])
    r = await module_kit._command(server, "type_cdp", {"text": query, "delay_per_char": random.randint(lo, hi)}, timeout_ms=30000)
    if r.status != "success":
        return {"ok": False, "why": f"typing failed: {r.error}", "plan": p}
    f2 = await _js(server, FOCUS_JS)
    if not f2 or (f2.get("value") or "").strip().lower() != query.strip().lower():
        return {"ok": False, "why": f"the box holds {f2 and f2.get('value')!r}, not {query!r} - not submitting", "plan": p}
    before = (await _js(server, STATE_JS))["url"]
    hits_before = await _hits(server, query)
    sub = await _js(server, SUBMIT_JS)
    btn = (sub or {}).get("buttons", [None])[0] if sub and sub.get("buttons") else None
    await module_kit.think(0.3, 1.0)
    sugg = None
    if not btn:  # no button: an autocomplete list? Enter would pick its FIRST entry (live 2026-10-29: Swappa -> an RTX 5090)
        await asyncio.sleep(1.5)
        # the model number decides ('3060'); sizes like '12gb' are often missing from suggestion names
        words = [w for w in re.split(r"\W+", query.lower()) if w.isdigit() and len(w) >= 3] or \
            [w for w in re.split(r"\W+", query.lower()) if len(w) >= 3]
        sl = await _js(server, SUGGEST_JS.replace("__WORDS__", json.dumps(words)))
        sugg = (sl or [None])[0]
        if not sugg:
            shown = await _js(server, SUGGEST_JS.replace("__WORDS__", "[]")) or []
            if shown:  # a suggestion list without our model: Enter would open its first entry - a wrong product
                await module_kit._command(server, "key", {"key": "Escape"})
                if evidence:
                    await screenshot(server, evidence.with_name(evidence.stem + "-after.png"))
                return {"ok": False, "plan": p, "suggestions": [x["text"] for x in shown],
                        "why": f"the site's suggestions have no {' '.join(words)}: " + " / ".join(x["text"][:40] for x in shown[:5])}
    if btn:
        how = "clicked the button next to the box"
        await server.click_cdp(btn["x"], btn["y"])
    elif sugg:
        how = f"clicked the matching suggestion '{sugg['text'][:60]}'"
        await server.click_cdp(sugg["x"], sugg["y"])
    else:
        how = "pressed Enter (no search button or matching suggestion next to the box)"
        await module_kit._command(server, "key", {"key": "Enter"})
    for attempt in range(2):
        deadline = asyncio.get_running_loop().time() + timeout / 2
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.8)
            try:
                s2 = await _js(server, STATE_JS)
            except RuntimeError:
                continue
            if s2.get("blocked"):
                return {"ok": False, "why": s2["blocked"], "url": s2["url"], "blocked": True, "plan": p, "how": how}
            if s2["url"] != before and s2["ready"] == "complete" and _carries(s2["url"], s2["title"], query):
                if evidence:
                    await screenshot(server, evidence.with_name(evidence.stem + "-results.png"))
                return {"ok": True, "how": how, "url": s2["url"], "button": btn, "plan": p, "proof": "results URL carries the query"}
            # in-page search (live 2026-10-29: PayMore, Swappa filter the list without a new URL): the page changed and
            # now shows more listings with the query's model number
            h = await _hits(server, query)
            if s2["ready"] == "complete" and h["hits"] >= 3 and h["hits"] > hits_before["hits"] and h["sig"] != hits_before["sig"]:
                await asyncio.sleep(1.5)
                if evidence:
                    await screenshot(server, evidence.with_name(evidence.stem + "-results.png"))
                return {"ok": True, "how": how, "url": s2["url"], "button": btn, "plan": p,
                        "proof": f"page now lists {h['hits']} lines with the query (was {hits_before['hits']})"}
        if attempt == 0 and btn:  # the click didn't navigate: the person presses Enter in the box
            f3 = await _js(server, FOCUS_JS)
            if not (f3 and f3.get("text_box")):
                g = await focus_test(server, {"x": f2["rect"][0] + min(f2["rect"][2] // 3, 120), "y": f2["rect"][1] + f2["rect"][3] // 2})
            await module_kit._command(server, "key", {"key": "Enter"})
            how += ", then Enter"
    if evidence:
        await screenshot(server, evidence.with_name(evidence.stem + "-after.png"))
    return {"ok": False, "why": "no results page carrying the query appeared", "how": how, "plan": p}
