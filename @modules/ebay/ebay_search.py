"""eBay product search through the ai-web-auto platform (read-only: never bids, buys or adds to cart).

    python ebay_search.py search "rtx 3060 12gb" --min 150 --max 450 [--sold] [--pages 3]
    python ebay_search.py read 128147436297 [more ids]
    python ebay_search.py job jobs/llm-workstation.json [--only gpu,cpu] [--read-top 4] [--no-read]

Every order runs module_kit.preflight first (eBay tab found, bound, ready). Page scripts use placeholders
for the selectors registered in knowledge/locators.json. Results go to @downloads/<run>/.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import re
import random
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MODULE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(MODULE_DIR.parent))
sys.path.insert(0, str(MODULE_DIR.parent.parent))
import module_kit  # noqa: E402
import ebay_rules as R  # noqa: E402

MODULE = module_kit.load(MODULE_DIR)
S = MODULE.settings


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Blocked(RuntimeError):
    """CAPTCHA, bot wall or sign-in: the human must act; never bypassed."""


# ── page scripts ─────────────────────────────────────────────────────────────

STATE_JS = r"""JSON.stringify((() => {
  const t = ((document.body && document.body.innerText) || '').slice(0, 4000) + ' ' + document.title;
  return {url: location.href, title: document.title,
    captcha: /captcha|verify (that )?you('| a)re (a )?human|Pardon Our Interruption|unusual (activity|traffic)|security measure/i.test(t)
      || !!document.querySelector('iframe[src*="captcha" i], #captcha_form, form[action*="captcha" i]'),
    signin: /(^|\.)signin\.ebay\.com$/.test(location.host)};
})())"""

FEEDBACK_JS = r"""(() => { let b = document.getElementById('awa-ebay-badge');
  if (!b) { b = document.createElement('div'); b.id = 'awa-ebay-badge';
    b.style.cssText = 'position:fixed;z-index:2147483647;bottom:12px;right:12px;max-width:440px;padding:8px 12px;'
      + 'background:#0b1f33e6;color:#fff;font:13px/1.4 system-ui;border-radius:8px;pointer-events:none;box-shadow:0 2px 10px #0007';
    document.documentElement.appendChild(b); }
  b.textContent = 'AI Web-Auto · eBay · ' + __MSG__; return 'ok'; })()"""

HIGHLIGHT_JS = r"""((sel) => { document.querySelectorAll('.awa-ebay-hl').forEach(e => { e.classList.remove('awa-ebay-hl'); e.style.outline = ''; });
  const el = document.querySelector(sel); if (el) { el.classList.add('awa-ebay-hl'); el.style.outline = '3px solid #ffb300'; el.style.outlineOffset = '2px'; }
  return !!el; })(__SEL__)"""

SCROLL_JS = r"""(() => { const h = document.documentElement.scrollHeight, step = Math.max(600, innerHeight);
  for (let y = 0; y <= h; y += step) scrollTo(0, y);  // synchronous: Chrome throttles timers in background windows
  document.querySelectorAll('img[loading="lazy"]').forEach(i => { i.loading = 'eager'; });
  scrollTo(0, 0); return document.querySelectorAll(@@result_card@@).length; })()"""

CARDS_JS = r"""JSON.stringify((() => {
  const dividerText = @@fewer_words_divider.text@@;
  const divider = [...document.querySelectorAll('li, h2, h3, div, span')].find(e =>
    (e.innerText || '').length < 120 && (e.innerText || '').includes(dividerText));
  const out = [];
  for (const card of document.querySelectorAll(@@result_card@@)) {
    const a = [...card.querySelectorAll(@@card_item_link@@)].find(x => /\/itm\/\d{9,}/.test(x.href));
    if (!a) continue;
    out.push({id: a.href.match(/\/itm\/(\d{9,})/)[1],
      fewer: !!divider && !!(divider.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING),
      lines: (card.innerText || '').split('\n').map(s => s.trim()).filter(Boolean)});
  }
  const count = (document.querySelector(@@results_count@@) || {}).innerText || '';
  const next = document.querySelector(@@next_page@@);
  return {url: location.href, count, next: next && next.href && next.getAttribute('aria-disabled') !== 'true' ? next.href : null, cards: out};
})())"""

ITEM_JS = r"""JSON.stringify((() => {
  const q = s => document.querySelector(s);
  const txt = s => { const e = q(s); return e ? e.innerText.replace(/[ \t]+/g, ' ').trim() : null; };
  const specs = {};
  for (const dt of document.querySelectorAll(@@item_specifics@@ + ' ' + @@spec_pair@@)) {
    let dd = dt.nextElementSibling; while (dd && dd.tagName !== 'DD') dd = dd.nextElementSibling;
    const k = (dt.innerText || '').trim().replace(/:$/, ''), v = dd ? dd.innerText.replace(/\s+/g, ' ').trim() : '';
    if (k && v && !(k in specs)) specs[k] = v.replace(/\s*Read more\s*about the condition$/i, '');
  }
  const photos = [...new Set([...document.querySelectorAll(@@photos@@ + ' img')].map(i => i.currentSrc || i.src || i.getAttribute('data-src') || '')
    .filter(u => /ebayimg/.test(u)).map(u => u.replace(/s-l\d+\./, 's-l1600.')))];
  const body = document.body.innerText;
  const itemNo = (body.match(/eBay item number:\s*(\d{9,})/) || [])[1] || null;
  const desc = q(@@description_frame@@);
  return {url: location.href, item_number: itemNo, title: txt(@@item_title@@),
    price_text: txt(@@price_primary@@), bin_text: txt(@@bin_price@@), bid_text: txt(@@bid_price@@),
    bin_button: !!q(@@buy_it_now_button@@), bid_button: !!q(@@place_bid_button@@),
    condition: txt(@@condition@@), quantity: txt(@@quantity@@), seller: txt(@@seller_card@@),
    shipping: txt(@@shipping_section@@), returns: txt(@@returns_section@@), confidence: txt(@@shop_confidence@@),
    store: txt(@@store_info@@), ratings: txt(@@seller_ratings@@), specs, photos,
    msku: (() => {
      const grab = (txt, key) => {  // the JSON object that follows "key":
        const i = txt.indexOf('"' + key + '":{'); if (i < 0) return null;
        let k = txt.indexOf('{', i), depth = 0, inStr = false, esc = false;
        for (let p = k; p < txt.length; p++) {
          const ch = txt[p];
          if (inStr) { if (esc) esc = false; else if (ch === '\\') esc = true; else if (ch === '"') inStr = false; continue; }
          if (ch === '"') inStr = true; else if (ch === '{') depth++; else if (ch === '}') { depth--; if (!depth) {
            try { return JSON.parse(txt.slice(k, p + 1)); } catch (e) { return null; } } }
        }
        return null;
      };
      for (const sc of document.querySelectorAll('script')) {
        const txt = sc.textContent || '';
        if (txt.indexOf('"MSKU":{') < 0) continue;
        const m = grab(txt, 'MSKU'); if (!m || !m.variationsMap) continue;
        const names = {};
        for (const [vid, item] of Object.entries(m.menuItemMap || {})) names[vid] = item;
        const menus = (m.selectMenus || []).map(s => ({label: s.displayLabel, ids: s.menuItemValueIds}));
        const labelOf = {};
        for (const mn of menus) for (const id of (mn.ids || [])) labelOf[String(id)] = mn.label;
        const out = [];
        for (const [varId, v] of Object.entries(m.variationsMap)) {
          const opts = Object.values(names).filter(it => (it.matchingVariationIds || []).map(String).includes(String(varId)));
          const price = v && v.binModel && v.binModel.price && v.binModel.price.value ? v.binModel.price.value.value : null;
          const q = v && v.quantity ? v.quantity : {};
          out.push({variation_id: varId, options: opts.map(o => o.valueName), price,
                    labeled: opts.map(o => ({menu: labelOf[String(o.valueId)] || '', value: o.valueName})),
                    out_of_stock: !!(q.outOfStock || opts.some(o => o.outOfStock))});
        }
        return {menus, variations: out.slice(0, 80)};
      }
      return null;
    })(),
    variations: [...document.querySelectorAll(@@buy_box@@ + ' button[aria-haspopup="listbox"], ' + @@buy_box@@ + ' select')]
      .map(b => (b.getAttribute('aria-label') || b.innerText || '').replace(/\s+/g, ' ').trim()).filter(Boolean).slice(0, 6),
    ended: /This listing (has ended|was ended)|no longer available|Bidding has ended/i.test(body.slice(0, 6000)),
    description_url: desc ? desc.src : null};
})())"""

DESC_JS = r"""JSON.stringify({url: location.href, text: (document.body.innerText || '').replace(/\n{3,}/g, '\n\n').slice(0, 12000),
  images: [...document.querySelectorAll('img')].map(i => i.src).filter(u => /^https?:/.test(u)).slice(0, 20)})"""


# ── platform helpers ─────────────────────────────────────────────────────────

class EbaySession:
    def __init__(self, server, out_dir: Path, nav_params: dict | None = None):
        self.s = server
        self.out = out_dir
        (out_dir / "evidence").mkdir(parents=True, exist_ok=True)
        self.page_loads = 0
        # extra navigate params, e.g. {"activate": False, "dismiss_banners": False} for a background watcher
        self.nav_params = nav_params or {}

    async def js(self, script: str, retries: int = 2):
        last = None
        for _ in range(retries + 1):
            try:
                return await module_kit._run_script(self.s, MODULE.js(script))
            except RuntimeError as e:
                last = e
                await asyncio.sleep(1.5)
        raise RuntimeError(f"page script failed: {last}")

    async def feedback(self, msg: str) -> None:
        print(f"  · {msg}", flush=True)
        try:
            await self.js(FEEDBACK_JS.replace("__MSG__", json.dumps(msg)), retries=0)
        except Exception:
            pass

    async def highlight(self, key: str) -> None:
        try:
            await self.js(HIGHLIGHT_JS.replace("__SEL__", json.dumps(MODULE.css(key))), retries=0)
        except Exception:
            pass

    async def guard(self) -> dict:
        st = await self.js(STATE_JS)
        if st.get("captcha") or st.get("signin"):
            await self.feedback("Blocked - please solve the check in this tab, then run again")
            raise Blocked(f"{'CAPTCHA / bot check' if st.get('captcha') else 'sign-in'} at {st['url']} - "
                          "solve it in Chrome, then run the order again (never bypassed)")
        return st

    async def goto(self, url: str, ready_keys: list[str], timeout: float = 25.0) -> dict:
        """Navigate, then poll for any ready signal (bounded); detect blocks. One retry on a slow load."""
        if self.page_loads:
            await module_kit.human_pause(S)  # random, person-like pacing between page loads
        self.page_loads += 1
        for attempt in range(2):
            # the page's own ready signal decides; eBay's trackers can delay the load event past 30 s
            await module_kit._command(self.s, "navigate", {"url": url, **self.nav_params}, timeout_ms=20000)
            ok = await module_kit._poll(lambda: self._present(ready_keys), timeout, 0.6)  # a read mid-load fails: keep polling
            st = await self.guard()
            if ok:
                return st
            if attempt == 0:
                await self.feedback("page slow - reloading once")
        raise RuntimeError(f"none of {ready_keys} appeared at {url}")

    # ── searching like a person (settings.search_via = "form") ──────────────

    async def _pause(self) -> None:
        if self.page_loads:
            await module_kit.human_pause(S)
        self.page_loads += 1

    async def _present(self, keys: list[str]) -> list[str]:
        """Registered signals showing now; none while the page is between documents."""
        try:
            return await module_kit._any_present(MODULE, self.s, keys)
        except RuntimeError:
            return []

    async def _state(self) -> dict | None:
        """Page state, or None while the page is between documents (a read during navigation fails)."""
        try:
            return await self.js(STATE_JS, retries=0)
        except RuntimeError:
            return None

    async def _results_for(self, query: str, not_url: str | None) -> bool:
        st = await self._state()
        if st is None:
            return False
        if st.get("captcha") or st.get("signin"):
            return True  # stop polling; guard() reports it
        parts = urlsplit(st["url"])
        q = dict(parse_qsl(parts.query)).get("_nkw") if parts.path.startswith("/sch/") else None
        return (st["url"] != not_url and q is not None and q.strip().lower() == query.strip().lower()
                and bool(await self._present(["result_card", "no_results"])))

    async def search_form(self, query: str, timeout: float = 25.0) -> dict:
        """Type the query in the header search box and click Search (trusted input, tab in front). Evidence: a
        results page whose _nkw is the query."""
        await self._pause()
        if not await module_kit._poll(lambda: self._present(["search_box"]), 10.0, 0.5):
            # no search box here (e.g. a page without the header): go home once, like clicking the logo
            await self.guard()
            await self.goto(MODULE.manifest["app_url"], ["search_box"])
        if not await module_kit._poll(lambda: self._present(["search_box"]), 10.0, 0.5):
            await self.guard()
            raise RuntimeError("the eBay search box is not on the page")
        await self.feedback(f"Typing '{query}' in the search box, then clicking Search")
        self.last_via = "search box + Search button, then filters"
        try:
            before = await module_kit.type_and_submit(MODULE, self.s, query, "search_box", "search_submit", S)
            if not await module_kit._poll(lambda: self._results_for(query, before), timeout, 0.6):
                raise RuntimeError(f"clicking Search did not open the results for {query!r}")
        except RuntimeError as e:
            # Typing can't reach a hidden tab (the user is using Chrome) and the box value check refuses to submit.
            # Rather than lose the search, open the same search by its URL - and say so.
            if not S.get("form_fallback_url", True):
                raise
            await self.feedback(f"typing didn't take ({e}) - opening the search URL instead")
            await self.goto("https://www.ebay.com/sch/i.html?" + urlencode({"_nkw": query, "_sacat": 0}), ["result_card", "no_results"])
            self.last_via = f"search URL (typing failed: {str(e)[:90]}), then filters"
        return await self.guard()

    async def read_down(self, bursts=None) -> dict:
        """Read down the results with mouse-wheel flicks until Next is in view or a random number of flicks.
        Every card is already in the page before scrolling (checked live), so this is pacing, not data loading."""
        r = await module_kit.read_down(MODULE, self.s, "next_page", tuple(bursts or S.get("read_bursts", [4, 14])))
        await self.feedback(f"Read down the results ({r['bursts']} wheel flicks, {r['via']}, {r['end']})")
        return r

    async def _on_page(self, page: int) -> bool:
        st = await self._state()
        if st is None:
            return False
        return dict(parse_qsl(urlsplit(st["url"]).query)).get("_pgn") == str(page) and \
            bool(await self._present(["result_card", "no_results"]))

    async def click_next(self, page: int, timeout: float = 12.0) -> str | None:
        """Read on to the Next link, pause, click it (trusted); if the click doesn't move the page, open its URL.
        Evidence: _pgn=page. Returns 'click' / 'link url', or None when there is no next page."""
        await module_kit.bring_to_front(self.s)
        await self.read_down((8, 30))
        await self._pause()
        nx = await module_kit.click_link(MODULE, self.s, "next_page")
        if not nx:
            return None
        if nx["clicked"]:
            if await module_kit._poll(lambda: self._on_page(page), timeout, 0.6):
                await self.guard()
                return "click"
            await self.feedback(f"Next click didn't move to page {page} - opening the link's URL")
        else:
            await self.feedback(f"Next link covered by {nx.get('hit')} - opening the link's URL")
        await self.goto(nx["href"], ["result_card", "no_results"])
        return "link url"

    async def screenshot(self, name: str) -> str | None:
        try:
            resp = await self.s.screenshot()
            img = resp.data.screenshot or ""
            if img.startswith("data:"):
                img = img.split(",", 1)[1]
            if img:
                p = self.out / "evidence" / f"{name}.png"
                p.write_bytes(base64.b64decode(img))
                return str(p.relative_to(self.out))
        except Exception:
            pass
        return None


# ── search ───────────────────────────────────────────────────────────────────

PARTS_QUERY = re.compile(r"\bfor\s*parts?\b|\bparts\s*only\b|\bparts\s*/\s*repair\b|\bnot\s*working\b", re.I)


def wants_parts(query: str) -> bool:
    """The query itself asks for parts / not-working items (the user's rule: only then is 'For parts' searched)."""
    return bool(PARTS_QUERY.search(query or ""))


def condition_ids(query: str) -> list[int]:
    """The user's condition list; 7000 (For parts or not working) only when the query asks for parts."""
    ids = list(S["condition_ids"])
    if wants_parts(query):
        ids.append(S.get("parts_condition_id", 7000))
    return ids


def search_url(query: str, lo=None, hi=None, sold=False, page=1, exclude_parts=True, sort=15, auction=False) -> str:
    """sort: 15 = price + shipping lowest first, 10 = newly listed (eBay _sop). auction=True: auctions only, ending
    soonest first. Item location = settings.item_location (3 = North America)."""
    p = {"_nkw": query, "_sacat": 0, "LH_PrefLoc": S.get("item_location", 1), "_stpos": S["ship_to_zip"], "_fcid": 1, "_ipg": 120}
    if sold:
        p.update({"LH_Sold": 1, "LH_Complete": 1})
    elif auction:
        p.update({"LH_Auction": 1, "_sop": S.get("auction_sort", 1)})
    else:
        p.update({"LH_BIN": 1, "_sop": sort})
    ids = condition_ids(query)
    if not exclude_parts and S.get("parts_condition_id", 7000) not in ids:
        ids.append(S.get("parts_condition_id", 7000))
    p["LH_ItemCondition"] = "|".join(str(c) for c in ids)
    if lo:
        p["_udlo"] = lo
    if hi:
        p["_udhi"] = hi
    if page > 1:
        p["_pgn"] = page
    return "https://www.ebay.com/sch/i.html?" + urlencode(p)


TYPED_SEARCH_KEEP = ("_from", "_trksid")  # eBay's own tracking params of a typed search


def merge_url(results_url: str, wanted_url: str) -> str:
    """The results page the search box produced, with our filters applied (Buy It Now or auctions, location, ZIP,
    conditions, price range, sort, 120 per page). From the typed search only eBay's tracking params (_from, _trksid)
    are kept: eBay's form carries the PREVIOUS search's filters into the new URL (live 2026-10-28: a Buy It Now
    search after an auction search kept LH_Auction=1 and found almost nothing)."""
    wanted = parse_qsl(urlsplit(wanted_url).query, keep_blank_values=True)
    keys = {k for k, _ in wanted}
    extra = [(k, v) for k, v in parse_qsl(urlsplit(results_url).query, keep_blank_values=True)
             if k in TYPED_SEARCH_KEEP and k not in keys]
    w = urlsplit(wanted_url)
    return urlunsplit((w.scheme, w.netloc, w.path, urlencode(wanted + extra), ""))


def seconds_left(text: str | None) -> int | None:
    """'1d 19h left' / '19h 28m left' / '45m left' / '30s left' -> seconds (None if not shown)."""
    if not text or not re.search(r"\d+\s*[dhms]\b", text):
        return None
    total, found = 0, False
    for n, unit in re.findall(r"(\d+)\s*([dhms])\b", text):
        total += int(n) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
        found = True
    return total if found else None


def auction_fields(L: list[str]) -> dict:
    """What an auction card shows (live 2026-10-28): '$1,375.00' | '28 bids ·' | 'Time left' | '1d 19h left (Fri, 05:55 PM)'.
    The price on an auction card is the CURRENT BID - never a purchase price."""
    text = " | ".join(L)
    bids = re.search(r"\b(\d+)\s+bids?\b", text)
    tl = next((l for l in L if re.search(r"\d+\s*[dhms]\b.*\bleft\b", l)), None)
    ends = re.search(r"\(([^)]+)\)", tl or "")
    return {"bids": int(bids.group(1)) if bids else None,
            "time_left": re.sub(r"\s*\(.*$", "", tl).strip() if tl else None,
            "seconds_left": seconds_left(re.sub(r"\(.*$", "", tl or "")),
            "ends": ends.group(1) if ends else None}


def parse_card(card: dict, parts_ok: bool = False) -> dict:
    """parts_ok: the query asked for parts, so a parts / not-working condition or title is not excluded."""
    L = card["lines"]
    text = " | ".join(L)
    title = next((l for l in L if len(l) > 15 and not l.startswith(("Sponsored", "Opens in"))
                  and not re.match(r"^(Sold|Ended)\s+\w{3}\s+\d", l)), L[0] if L else "")
    title = re.sub(r"^NEW LISTING", "", title, flags=re.I).strip()
    price_line = next((l for l in L if re.match(r"^\$[\d,]+\.\d{2}", l)), None)
    ship = next((l for l in L if re.search(r"delivery|shipping", l, re.I) and ("$" in l or "Free" in l)), None)
    cond = next((l for l in L if re.match(r"^(Brand New|New|Pre-Owned|Used|Open Box|Refurbished|Certified|Excellent|"
                                            r"Very Good|Good|Seller refurbished|Parts)", l)), None)
    seller = next((l for l in L if re.search(r"% positive", l)), "")
    item = {
        "id": card["id"], "url": f"https://www.ebay.com/itm/{card['id']}", "title": title,
        "price": R.money(price_line), "price_is_range": bool(price_line and " to " in price_line),
        "shipping": 0.0 if ship and re.search(r"free", ship, re.I) else R.money(ship), "shipping_text": ship,
        "auction": bool(re.search(r"\b\d+ bids?\b|Time left|\bbid\b", text, re.I)) and "Buy It Now" not in text,
        "best_offer": "or Best Offer" in text, "condition": cond,
        "seller": seller.split(" ")[0] if seller else None, **R.seller_stats(seller),
        "sold_line": next((l for l in L if l.startswith("Sold ")), None),
        "fewer_words": card.get("fewer", False),
        "located_in": next((re.sub(r"^Located in\s+", "", l) for l in L if l.startswith("Located in ")), None),
    }
    if item["auction"]:
        item.update(auction_fields(L), current_bid=item["price"])
    item["excluded"] = R.exclusion_reason(title, cond)
    if parts_ok and item["excluded"] and re.search(r"parts|not\s*working|repair", item["excluded"], re.I):
        item["excluded"] = None  # the query asked for parts: a parts listing is what was searched for
    item["landed_estimate"] = R.landed_cost(item["price"], item["shipping"], S["sales_tax_rate"])
    return item


async def search_products(sess: EbaySession, query: str, lo=None, hi=None, sold=False, max_pages=None, sort=15,
                          auction=False) -> dict:
    """Search, read each page, follow pages until the exact matches end; drop excluded listings.
    auction=True: auctions only, ending soonest first (each item carries current_bid, bids, time_left, ends).

    settings.search_via = "form" (default): type the query in the search box and click Search, apply the filters to
    that results page, read down it with the mouse wheel, click Next for more pages (the tab comes to the front).
    "url": open the search URL directly and scroll by script (works in a background tab)."""
    max_pages = max_pages or S["max_pages"]
    via_form = S.get("search_via", "form") == "form"
    seen, items, excluded, pages = set(), [], [], []
    parts_ok = wants_parts(query)
    kind = "Sold comps" if sold else "Auctions" if auction else "Searching"
    for page in range(1, max_pages + 1):
        url = search_url(query, lo, hi, sold, page, sort=sort, auction=auction)
        await sess.feedback(f"{kind}: {query} (${lo}-{hi}) page {page}")
        if not via_form:
            await sess.goto(url, ["result_card", "no_results"])
            n, how = await sess.js(SCROLL_JS), "url"
        else:
            if page == 1:
                st = await sess.search_form(query)
                await sess.goto(merge_url(st["url"], url), ["result_card", "no_results"])
                how = getattr(sess, "last_via", "search box + Search button, then filters")
            else:
                how = await sess.click_next(page)
                if not how:
                    break
            await module_kit.bring_to_front(sess.s)
            n = (await sess.read_down())["bursts"]
        data = await sess.js(CARDS_JS)
        new = [c for c in data["cards"] if c["id"] not in seen and c["id"] != "123456"]
        exact = [c for c in new if not c["fewer"]]
        for c in exact:
            seen.add(c["id"])
            it = parse_card(c, parts_ok)
            (excluded if it["excluded"] else items).append(it)
        pages.append({"page": page, "via": how, "url": data["url"], "cards": len(data["cards"]), "exact_new": len(exact),
                      "count_text": data["count"], **({"wheel_flicks": n} if via_form else {})})
        await sess.feedback(f"{query}: page {page} - {len(exact)} exact matches, {len(items)} kept so far")
        # Stop when this page reached eBay's "fewer words" filler, added nothing, or has no next page.
        if not exact or len(exact) < len(new) or not data.get("next"):
            break
    return {"query": query, "sold": sold, "auction": auction, "min": lo, "max": hi, "fetched_at": now(), "pages": pages,
            "items": items, "excluded": excluded}


# ── auction details (listing page) ───────────────────────────────────────────

AUCTION_JS = r"""JSON.stringify((() => {
  const t = s => { const e = document.querySelector(s); return e ? (e.innerText || '').replace(/\s+/g, ' ').trim() : null; };
  const body = document.body.innerText || '';
  return {url: location.href, ready: document.readyState,
    item_no: (body.match(/eBay item number:\s*(\d{9,})/) || [])[1] || null,
    bid_price: t(@@bid_price@@), bid_count: t(@@bid_count@@), end_time: t(@@end_time@@), watchers: t(@@watchers_signal@@),
    ended: /This listing (has )?ended|Bidding has ended/i.test(body.slice(0, 6000))};
})())"""


def parse_auction_page(raw: dict) -> dict:
    """Listing-page auction facts. Watchers come from eBay's demand signal ('108 people are watching this'); when
    eBay shows no signal the count is unknown (None), never 0."""
    w = re.search(r"(\d[\d,]*)\s+(?:people are watching|watchers?|watching)", raw.get("watchers") or "", re.I)
    bids = re.search(r"(\d+)\s+bids?", raw.get("bid_count") or raw.get("bid_price") or "")
    end = raw.get("end_time") or ""
    left = re.search(r"Ends in\s+((?:\d+\s*[dhms]\s*)+)", end)
    return {"current_bid": R.money(raw.get("bid_price")), "bids": int(bids.group(1)) if bids else None,
            "watchers": int(w.group(1).replace(",", "")) if w else None, "watchers_text": raw.get("watchers"),
            "time_left": left.group(1).strip() if left else None,
            "seconds_left": seconds_left(left.group(1)) if left else None,
            "ends": end[left.end():].strip() if left else (end or None), "ended": bool(raw.get("ended")),
            "read_at": now()}


async def read_auction(sess: EbaySession, item_id: str) -> dict:
    """Open an auction's listing page (read-only: Place bid and Watch are never touched) and read the current bid,
    bids, time left and watchers. Evidence: the page's item number is the requested one."""
    await sess.feedback(f"Reading auction {item_id}: bids, time left, watchers")
    await sess.goto(f"https://www.ebay.com/itm/{item_id}", ["item_title"])
    if S.get("search_via", "form") == "form":  # glance down the listing like a person (a few wheel flicks)
        await module_kit.bring_to_front(sess.s)
        await module_kit.human_scroll(sess.s, max_bursts=random.randint(1, 3))
    raw = await sess.js(AUCTION_JS)
    if raw.get("item_no") and raw["item_no"] != str(item_id):
        raise RuntimeError(f"listing page shows item {raw['item_no']}, not {item_id}")
    return {"id": str(item_id), **parse_auction_page(raw)}


# ── listing ──────────────────────────────────────────────────────────────────

async def read_listing(sess: EbaySession, item_id: str, with_description: bool = True) -> dict:
    """Open the item page, extract the visible evidence, read the seller's description frame."""
    url = f"https://www.ebay.com/itm/{item_id}"
    await sess.feedback(f"Validating listing {item_id}")
    await sess.goto(url, ["item_title"])
    await sess.highlight("price_primary")
    # The description iframe and the lower seller sections render only when scrolled into view.
    await sess.js("(() => { scrollTo(0, document.documentElement.scrollHeight); return 1; })()")
    await module_kit._poll(lambda: module_kit._any_present(MODULE, sess.s, ["description_frame"]), 6.0, 0.5)
    await sess.js("(() => { scrollTo(0, 0); return 1; })()")
    raw = await sess.js(ITEM_JS)
    if not raw.get("description_url"):  # known pattern of the frame's src
        raw["description_url"] = f"https://itm.ebaydesc.com/itmdesc/{item_id}"
    shot = await sess.screenshot(f"item_{item_id}")
    raw["fetched_at"] = now()
    desc = {"text": "", "images": [], "url": raw.get("description_url")}
    if with_description and raw.get("description_url"):
        await sess.feedback(f"Reading seller description of {item_id}")
        try:
            await sess.goto(raw["description_url"], ["description_body"])
            desc = await sess.js(DESC_JS)
        except Exception as e:
            desc["error"] = str(e)
    rec = assess_listing(item_id, raw, desc)
    rec["evidence"]["screenshot"] = shot
    (sess.out / "evidence" / f"item_{item_id}.json").write_text(json.dumps({"raw": raw, "description": desc}, indent=1,
                                                                        ensure_ascii=False), encoding="utf-8")
    await sess.feedback(f"{item_id}: {'REJECT' if rec['trust']['reject'] or rec['excluded'] else 'ok'} "
                        f"trust {rec['trust']['score']}")
    return rec


def set_variation_require(pattern: str | None) -> None:
    """Which option of a multi-option listing qualifies (e.g. RAM >= 64GB); set by the job before reading."""
    S["variation_require"] = pattern


def assess_listing(item_id: str, raw: dict, desc: dict) -> dict:
    """Turn the raw page extraction into a checked listing record (pure: testable without a browser)."""
    specs = raw.get("specs") or {}
    title = raw.get("title") or ""
    cond = (raw.get("condition") or "").replace("Condition:", "").strip()
    cond = re.sub(r"^(\w[\w -]*?)\s+\1$", r"\1", cond)  # "Used Used" -> "Used"

    ship = R.shipping_cost(raw.get("shipping"))
    seller = R.seller_stats(raw.get("seller") or "")
    returns = raw.get("returns") or ""
    qty = raw.get("quantity") or ""
    mismatches = []
    option = None
    msku = raw.get("msku") or {}
    if msku.get("variations"):
        opts = [v for v in msku["variations"] if v.get("price") and not v.get("out_of_stock")]
        need = S.get("variation_require")  # e.g. RAM >= 64GB
        def meets(v):
            if not need:
                return True
            ram = [o["value"] for o in v.get("labeled") or [] if re.search(r"\bRAM\b|memory|DDR", o.get("menu") or "", re.I)]
            if ram:  # a RAM menu exists: only its value counts (an SSD menu's "128 GB" is not RAM)
                return bool(re.search(need, " ".join(ram), re.I))
            if len(v["options"]) > 1:  # several menus but none labelled RAM: cannot tell - don't guess
                return False
            return bool(re.search(need, " ".join(v["options"]), re.I))

        ok = [v for v in opts if meets(v)]
        if ok:
            option = min(ok, key=lambda v: v["price"])
        title_opt = [v for v in msku["variations"] if need and meets(v)]
        if title_opt and all(v.get("out_of_stock") for v in title_opt):
            mismatches.append("every option matching the title's configuration is out of stock")
    elif raw.get("variations"):
        mismatches.append("multi-option listing (" + "; ".join(raw["variations"][:3]) + ") - option prices could not be read")
    if raw.get("item_number") and raw["item_number"] != str(item_id):
        mismatches.append(f"item number on page {raw['item_number']} != {item_id}")
    auction_only = bool(raw.get("bid_button")) and not raw.get("bin_button")
    price = None if auction_only else R.money(raw.get("bin_text") or raw.get("price_text"))
    if option:
        price = float(option["price"])
    elif msku.get("variations"):
        price = None  # no in-stock option meets the requirement
    desc_text = desc.get("text") or ""
    loc = re.search(r"Located in:\s*([^\n]+)", raw.get("shipping") or "")
    rec = {
        "item_id": str(item_id), "url": f"https://www.ebay.com/itm/{item_id}", "title": title,
        "price": price, "price_type": "auction (current bid - not a purchase price)" if auction_only else "Buy It Now",
        "current_bid": R.money(raw.get("bid_text")) if raw.get("bid_text") else None,
        "shipping_to_zip": None if ship == float("inf") else ship, "ships_to_zip": ship != float("inf"),
        "shipping_text": raw.get("shipping"), "landed_cost": R.landed_cost(price, ship, S["sales_tax_rate"]),
        "condition": cond, "returns_text": returns, "return_days": R.return_days(returns),
        "free_returns": bool(re.search(r"free returns|seller pays for return", returns, re.I)),
        "seller_text": raw.get("seller"), **seller,
        "top_rated": "Top Rated Plus" in (raw.get("confidence") or ""),
        "store": (raw.get("store") or "")[:300], "seller_ratings": (raw.get("ratings") or "")[:300],
        "location": loc.group(1).strip() if loc else None,
        # no Buy It Now and no bid button = eBay's "This item is out of stock" page (item 800020842561, 2026-10-30:
        # price still shown, no quantity line); None = an older read without the button fields: unknown, not unavailable
        "quantity": qty, "available": not raw.get("ended") and not re.search(r"\b0 available|Out of stock|sold out", qty, re.I)
                                      and not (raw.get("bin_button") is False and raw.get("bid_button") is False),
        "ended": raw.get("ended"), "specs": specs, "photos": len(raw.get("photos") or []),
        "photo_urls": (raw.get("photos") or [])[:12], "description_excerpt": desc_text[:6000],
        "desc_flags": R.description_flags(desc_text), "tested": R.tested_evidence(title, desc_text),
        "variations": raw.get("variations") or [],
        "chosen_option": {"options": option["options"], "labeled": option.get("labeled"), "price": option["price"],
                          "variation_id": option["variation_id"]} if option else None,
        "options_seen": [{"options": v["options"], "price": v["price"], "out_of_stock": v["out_of_stock"]}
                         for v in (msku.get("variations") or [])][:30],
        "options_seen_full": (msku.get("variations") or [])[:80],
        "auction_only": auction_only, "mismatches": mismatches,
        "excluded": R.exclusion_reason(title, cond),
        "evidence": {"source_url": raw.get("url"), "description_url": desc.get("url"), "fetched_at": raw.get("fetched_at"),
                     "sha256": hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()},
    }
    rec["trust"] = R.trust(rec)
    return rec


# ── confirm a multi-option price by selecting the options in the page ───────

OPTION_JS = r"""((menu, value) => {
  const panel = document.querySelector(@@msku_panel@@);
  if (!panel) return JSON.stringify({err: 'no option panel'});
  const norm = s => (s || '').replace(/:$/, '').trim().toLowerCase();
  const btn = [...panel.querySelectorAll(@@msku_menu_button@@)].find(b =>
    norm((b.querySelector('.btn__label') || {}).innerText || b.innerText.split('\n')[0]) === norm(menu));
  if (!btn) return JSON.stringify({err: 'no menu ' + menu});
  const shown = ((btn.querySelector('.btn__text') || btn).innerText || '').trim();
  if (value === null) {
    btn.scrollIntoView({block: 'center'});
    const r = btn.getBoundingClientRect();
    return JSON.stringify({x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), shown,
                           expanded: btn.getAttribute('aria-expanded') === 'true'});
  }
  const box = document.getElementById(btn.getAttribute('aria-controls'));
  const opt = box && [...box.querySelectorAll(@@msku_option@@)].find(o => o.getAttribute('data-sku-value-name') === value);
  if (!opt) return JSON.stringify({err: 'no option ' + value, shown});
  opt.scrollIntoView({block: 'nearest'});  // long menus scroll: bring the option into the visible part of the list
  const r = opt.getBoundingClientRect();
  const hit = document.elementFromPoint(Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2));
  if (!hit || !opt.contains(hit)) return JSON.stringify({err: 'option covered or off screen', shown});
  if (!r.width || !r.height) return JSON.stringify({err: 'option not visible', shown});
  return JSON.stringify({x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), shown});
})(__ARGS__)"""


MENUS_JS = r"""JSON.stringify((() => {
  const panel = document.querySelector(@@msku_panel@@);
  if (!panel) return null;
  return [...panel.querySelectorAll(@@msku_menu_button@@)].map(btn => {
    const label = ((btn.querySelector('.btn__label') || {}).innerText || btn.innerText.split('\n')[0]).replace(/:$/, '').trim();
    const shown = ((btn.querySelector('.btn__text') || btn).innerText || '').trim();
    const box = document.getElementById(btn.getAttribute('aria-controls'));
    const options = box ? [...box.querySelectorAll(@@msku_option@@)].map(o => ({
      value: o.getAttribute('data-sku-value-name'),
      text: (o.innerText || '').replace(/\s+/g, ' ').trim(),
      disabled: o.getAttribute('aria-disabled') === 'true' || /disabled|unavailable/i.test(o.className) || /out of stock|unavailable/i.test(o.innerText || '')
    })) : [];
    return {label, shown, options};
  });
})())"""


async def walk_options(sess: "EbaySession", rec: dict, targets: dict | None = None) -> dict:
    """Select menu by menu. targets = {menu label: value}; a menu without a target gets its first enabled option.
    Records, for every step, the menu's options as they are after the previous choices, the choice and the price."""
    await sess.feedback(f"Walking the option menus of {rec['item_id']}")
    await sess.goto(rec["url"], ["item_title"])
    # Chrome does not reliably process input in a hidden tab: bring OUR bound tab to the front before clicking.
    try:
        await module_kit._command(sess.s, "activate_tab", {"query": "ebay.com"}, timeout_ms=8000)
        await asyncio.sleep(0.5)
    except Exception:
        pass
    if not await module_kit._poll(lambda: module_kit._any_present(MODULE, sess.s, ["msku_panel"]), 8.0, 0.5):
        return {"ok": False, "detail": "option panel not found", "steps": []}

    async def at(menu, value):
        return await sess.js(OPTION_JS.replace("__ARGS__", json.dumps(menu) + ", " + json.dumps(value)))

    async def price_now():
        return R.money(await sess.js("JSON.stringify((document.querySelector(@@price_primary@@) || {}).innerText || '')"))

    steps = []
    menus = await sess.js(MENUS_JS) or []
    for idx in range(len(menus)):
        menus = await sess.js(MENUS_JS) or []  # re-read: options of later menus can change after each choice
        m = menus[idx]
        enabled = [o for o in m["options"] if not o["disabled"]]
        want = (targets or {}).get(m["label"])
        pick = next((o for o in enabled if o["value"] == want), None) if want else (enabled[0] if enabled else None)
        step = {"menu": m["label"], "options_before": m["options"], "wanted": want,
                "chosen": pick["value"] if pick else None}
        if not pick:
            step["error"] = f"'{want}' is not available after the previous choices" if want else "no enabled option"
            steps.append(step)
            return {"ok": False, "steps": steps, "detail": step["error"]}
        done = m["shown"] == pick["value"]
        for _ in range(2):
            if done:
                break
            btn = await at(m["label"], None)
            await sess.s.click_cdp(btn["x"], btn["y"])
            opt = await module_kit._poll(lambda: _opt_point(at, {"menu": m["label"], "value": pick["value"]}), 5.0, 0.3)
            if opt:
                await sess.s.click_cdp(opt["x"], opt["y"])
                done = bool(await module_kit._poll(lambda: _shown(at, {"menu": m["label"], "value": pick["value"]}), 5.0, 0.3))
        await asyncio.sleep(0.6)
        step.update(selected=done, price_after=await price_now())
        steps.append(step)
        await sess.feedback(f"{m['label']} = {pick['value']} -> {step['price_after']}")
        if not done:
            return {"ok": False, "steps": steps, "detail": f"could not select {m['label']} = {pick['value']}"}
    state = await sess.js("JSON.stringify({url: location.href, price: (document.querySelector(@@price_primary@@) || {}).innerText || ''})")
    return {"ok": True, "steps": steps, "final_price": R.money(state["price"]), "url": state["url"],
            "variation_id": (re.search(r"[?&]var=(\d+)", state["url"]) or [None, None])[1], "checked_at": now()}


def embedded_price(rec: dict, chosen: dict[str, str]) -> tuple[float | None, str | None, bool | None]:
    """Price, variation id and stock of an exact combination in the page's embedded data."""
    for v in (rec.get("options_seen_full") or []):
        lab = {o["menu"]: o["value"] for o in v.get("labeled") or []}
        if lab and all(lab.get(k) == val for k, val in chosen.items()):
            return v["price"], v["variation_id"], v["out_of_stock"]
    return None, None, None


async def confirm_option(sess: "EbaySession", rec: dict) -> dict:
    """Walk to the chosen (cheapest qualifying) option and check page price + variation id against the embedded data."""
    co = rec.get("chosen_option") or {}
    labeled = [o for o in (co.get("labeled") or []) if o.get("menu")]
    if not labeled:
        return {"confirmed": None, "detail": "single unlabelled option menu - embedded price only"}
    w = await walk_options(sess, rec, {o["menu"]: o["value"] for o in labeled})
    ok = bool(w.get("ok") and w.get("final_price") is not None and abs(w["final_price"] - float(co["price"])) < 0.01
              and (w.get("variation_id") in (None, str(co.get("variation_id")))))
    return {"confirmed": ok, "displayed_price": w.get("final_price"), "embedded_price": co["price"],
            "variation_id_page": w.get("variation_id"), "variation_id_embedded": co.get("variation_id"), "walk": w,
            "detail": "page price and variation id match the embedded option" if ok else (w.get("detail") or
                      f"page shows {w.get('final_price')} (var {w.get('variation_id')}) vs embedded {co['price']} (var {co.get('variation_id')})")}


async def _opt_point(at, o):
    p = await at(o["menu"], o["value"])
    return None if p.get("err") else p


async def _shown(at, o):
    p = await at(o["menu"], None)
    return p.get("shown") == o["value"]


# ── runner ───────────────────────────────────────────────────────────────────

async def with_session(action: str, fn, out_dir: Path):
    import warnings
    warnings.filterwarnings("ignore")
    from ai_web_auto_backend.automation_controller import BrowserAgent
    agent = BrowserAgent()
    print("Waiting for the AI Web-Auto extension on ws://localhost:8765 ...", flush=True)
    await agent.connect(timeout=60)
    try:
        # Work in a tab of our own: the user may be browsing eBay in the other tabs (preflight prefers the
        # active tab already on the target page, which is this new one).
        await module_kit._command(agent.server, "navigate", {"url": MODULE.manifest["app_url"], "new_tab": True}, timeout_ms=20000)
        pf = await module_kit.preflight(MODULE, agent.server, action)
        print(pf.summary(), flush=True)
        if not pf.ok:
            raise SystemExit(1)
        sess = EbaySession(agent.server, out_dir)
        try:
            return await fn(sess)
        finally:
            await sess.feedback("Done - results saved locally")
            try:  # close the tab this order opened (bound); older extensions without close_tab leave it open
                await module_kit._command(agent.server, "close_tab", timeout_ms=5000)
            except Exception:
                pass
    finally:
        await agent.stop()


def run_dir(name: str) -> Path:
    d = MODULE_DIR / S["downloads_dir"] / f"{datetime.now():%Y-%m-%d}-{name}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write(path: Path, data) -> Path:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("search"); p.add_argument("query"); p.add_argument("--min", type=float); p.add_argument("--max", type=float)
    p.add_argument("--sold", action="store_true"); p.add_argument("--pages", type=int)
    p = sub.add_parser("read"); p.add_argument("ids", nargs="+")
    p = sub.add_parser("confirm"); p.add_argument("ids", nargs="+"); p.add_argument("--require")
    p = sub.add_parser("explore"); p.add_argument("ids", nargs="+")
    p = sub.add_parser("job"); p.add_argument("job"); p.add_argument("--only"); p.add_argument("--read-top", type=int)
    p.add_argument("--no-read", action="store_true"); p.add_argument("--no-search", action="store_true")
    p.add_argument("--refresh-selected", action="store_true", help="re-read the selected builds' listings now and confirm option prices")
    p.add_argument("--scan-variations", action="store_true", help="visit every listing read; walk option menus where present")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        if a.cmd == "search":
            out = run_dir("search")
            async def go(sess):
                return await search_products(sess, a.query, a.min, a.max, a.sold, a.pages)
            res = asyncio.run(with_session("sold_comps" if a.sold else "search_products", go, out))
            path = write(out / f"search-{re.sub(r'[^a-z0-9]+', '-', a.query.lower())}{'-sold' if a.sold else ''}.json", res)
            for it in res["items"][:25]:
                print(f"  {it['price']!s:>8} +{it['shipping']!s:<6} {it['id']}  {it['title'][:70]}  [{it['seller']} {it['feedback_pct']}%/{it['feedback_count']}]")
            print(f"{len(res['items'])} kept, {len(res['excluded'])} excluded -> {path}")
        elif a.cmd == "read":
            out = run_dir("listings")
            async def go(sess):
                return [await read_listing(sess, i) for i in a.ids]
            recs = asyncio.run(with_session("read_listing", go, out))
            for r in recs:
                print(f"  {r['item_id']}  ${r['price']} + ship {r['shipping_to_zip']} = landed {r['landed_cost']}  "
                      f"trust {r['trust']['score']} {r['trust']['reject'] or ''} {r['excluded'] or ''}")
            write(out / "listings.json", recs)
        elif a.cmd == "confirm":
            set_variation_require(a.require or r"\b(64|96|128|192|256)\s?GB(?!\s*(SSD|NVMe|HDD|M\.2|storage))")
            out = run_dir("confirm")
            async def go(sess):
                res = []
                for i in a.ids:
                    rec = await read_listing(sess, i)
                    res.append({"item_id": i, "title": rec["title"], "price": rec["price"], "landed": rec["landed_cost"],
                                "chosen_option": rec.get("chosen_option"), "options_seen": rec.get("options_seen"),
                                "confirm": await confirm_option(sess, rec)})
                return res
            res = asyncio.run(with_session("read_listing", go, out))
            write(out / "confirm.json", res)
            for r in res:
                print(json.dumps(r, indent=1, ensure_ascii=False)[:3000])
        elif a.cmd == "explore":
            out = run_dir("explore")
            async def go(sess):
                res = []
                for i in a.ids:
                    rec = await read_listing(sess, i)
                    w = await walk_options(sess, rec)
                    chosen = {s["menu"]: s["chosen"] for s in w.get("steps", []) if s.get("chosen")}
                    ep, evid, oos = embedded_price(rec, chosen)
                    res.append({"item_id": i, "title": rec["title"], "walk": w, "embedded_price": ep, "embedded_variation_id": evid,
                                "embedded_out_of_stock": oos,
                                "match": (w.get("final_price") is not None and ep is not None and abs(w["final_price"] - ep) < 0.01)})
                return res
            res = asyncio.run(with_session("read_listing", go, out))
            write(out / "explore.json", res)
            for r in res:
                print(f"\n{r['item_id']} {r['title'][:70]}")
                for s in r["walk"].get("steps", []):
                    avail = [o["value"] + (" (disabled)" if o["disabled"] else "") for o in s["options_before"]]
                    print(f"  {s['menu']:<14} options {avail} -> chose {s['chosen']!r} -> page price {s.get('price_after')}")
                print(f"  final {r['walk'].get('final_price')} (var {r['walk'].get('variation_id')}) | embedded {r['embedded_price']} "
                      f"(var {r['embedded_variation_id']}) | match {r['match']}")
        elif a.cmd == "job":
            sys.path.insert(0, str(MODULE_DIR / "jobs"))
            import job_runner
            return job_runner.run(a, MODULE, with_session, search_products, read_listing, run_dir, write)
    except module_kit.KnowledgeError as e:
        print(f"ERROR: {e}")
        return 2
    except Blocked as e:
        print(f"BLOCKED: {e}")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
