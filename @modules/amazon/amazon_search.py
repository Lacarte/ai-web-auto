"""Amazon US product search through the ai-web-auto platform (read-only: never adds to cart, buys or changes
the delivery address).

    python amazon_search.py search "rtx 3060 12gb" --min 200 --max 400 [--pages 2]

Every order runs module_kit.preflight first (Amazon tab found, bound, ready). Page scripts use placeholders for
the selectors registered in knowledge/locators.json. Results go to @downloads/<date>-search-<query>/.
    python amazon_search.py product B0BK36KLDD [B0CTK1SFVD ...]   # read product pages (real price, availability)

Product pages hold another extension's frame, so Chrome refuses the debugger there; the extension reads them through
chrome.scripting instead (capability script_fallback). Add to cart / Buy Now are read for presence, never clicked.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MODULE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(MODULE_DIR.parent))
sys.path.insert(0, str(MODULE_DIR.parent.parent))
import module_kit  # noqa: E402

MODULE = module_kit.load(MODULE_DIR)
S = MODULE.settings
BASE = "https://www.amazon.com"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Blocked(RuntimeError):
    """Robot check or sign-in wall: the human must act; never bypassed."""


def needs_person(st: dict | None) -> str | None:
    """What on this page only the person may do (None = nothing): a robot check, the 'I am human' checkbox, a sign-in."""
    if not st:
        return None
    if st.get("human"):
        return "an 'I am human' check"
    if st.get("captcha"):
        return "a robot check"
    if st.get("signin"):
        return "a sign-in page"
    return None


# ── page scripts ─────────────────────────────────────────────────────────────

STATE_JS = r"""JSON.stringify((() => {
  const main = (document.querySelector('#search') || document.body || {}).innerText || '';
  return {url: location.href, title: document.title, ready: document.readyState,
    captcha: !!document.querySelector(@@captcha_form@@)
      || /Enter the characters you see below|Sorry, we just need to make sure you're not a robot/i.test(main.slice(0, 3000) + document.title),
    human: !!document.querySelector(@@human_check@@) || /Check the box to continue/i.test((document.body || {}).innerText ? document.body.innerText.slice(0, 2000) : ''),
    signin: /\/ap\/signin/.test(location.pathname)};
})())"""

FEEDBACK_JS = r"""(() => { let b = document.getElementById('awa-amazon-badge');
  if (!b) { b = document.createElement('div'); b.id = 'awa-amazon-badge';
    b.style.cssText = 'position:fixed;z-index:2147483647;bottom:12px;right:12px;max-width:440px;padding:8px 12px;'
      + 'background:#232f3ee6;color:#fff;font:13px/1.4 system-ui;border-radius:8px;pointer-events:none;box-shadow:0 2px 10px #0007';
    document.documentElement.appendChild(b); }
  b.textContent = 'AI Web-Auto · Amazon · ' + __MSG__; return 'ok'; })()"""

CARDS_JS = r"""JSON.stringify((() => {
  const txt = (root, sel) => { const e = root.querySelector(sel); return e ? (e.innerText || e.textContent || '').replace(/[ \t]+/g, ' ').trim() : null; };
  const out = [];
  for (const card of document.querySelectorAll(@@result_card@@)) {
    const asin = card.getAttribute('data-asin');
    if (!asin) continue;
    const reviews = card.querySelector(@@card_reviews@@);
    const link = card.querySelector(@@card_link@@);
    out.push({asin,
      title: (txt(card, @@card_title@@) || '').replace(/\s*\n\s*/g, ' '),
      price: (card.querySelector(@@card_price@@) || {}).textContent || null,
      list_price: (card.querySelector(@@card_list_price@@) || {}).textContent || null,
      delivery: txt(card, @@card_delivery@@),
      secondary: txt(card, @@card_secondary_offer@@),
      rating: (card.querySelector(@@card_rating@@) || {}).textContent || null,
      reviews: reviews ? reviews.getAttribute('aria-label') : null,
      prime: !!card.querySelector(@@card_prime@@),
      href: link ? link.href : null,
      lines: (card.innerText || '').split('\n').map(s => s.trim()).filter(Boolean).slice(0, 30)});
  }
  const next = document.querySelector(@@next_page@@);
  return {url: location.href, ready: document.readyState,
    count: ((document.querySelector(@@results_info@@) || {}).innerText || '').split('\n')[0],
    deliver_to: ((document.querySelector(@@deliver_to@@) || {}).innerText || '').replace(/[​-‍]/g, '').trim() || null,
    no_results: ((document.querySelector('#search') || document.body).innerText || '').includes(@@no_results.text@@),
    next: next && next.href ? next.href : null, cards: out};
})())"""

PRODUCT_JS = r"""JSON.stringify((() => {
  const clean = s => (s || '').replace(/[ \t]+/g, ' ').replace(/\s*\n\s*/g, ' | ').trim();
  const txt = (sel, root = document) => { const e = root.querySelector(sel); return e ? clean(e.innerText || e.textContent) : null; };
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  // prices: priceToPay renders '$198' '.' '22' on separate lines - join without spaces
  const price = (sel, root = document) => { const e = [...root.querySelectorAll(sel)].find(x => /\d/.test(x.textContent || ''));
    return e ? (e.textContent || '').replace(/\s+/g, '') : null; };
  const variants = [...document.querySelectorAll(@@selected_variant@@)].filter((li, i, a) => a.indexOf(li) === i).map(li => ({
    asin: li.getAttribute('data-asin'), label: txt(@@variant_label@@, li),
    price: price(@@variant_price@@, li), availability: txt(@@variant_availability@@, li),
    prime: !!li.querySelector(@@variant_prime@@)}));
  const asinEl = document.querySelector(@@product_asin@@);
  return {url: location.href, ready: document.readyState, title: txt(@@product_title@@),
    asin: asinEl ? asinEl.value : null,
    availability: txt(@@product_availability@@),
    add_to_cart: !!document.querySelector(@@add_to_cart@@), add_to_cart_used: !!document.querySelector(@@add_to_cart_used@@),
    buy_now: !!document.querySelector(@@buy_now@@), see_all: !!document.querySelector(@@see_all_offers@@),
    out_of_stock: !!document.querySelector(@@out_of_stock@@),
    core_price: price(@@core_price@@), variants,
    prime: [...document.querySelectorAll(@@buybox_prime@@)].some(vis),
    delivery: txt(@@delivery_block@@), merchant: txt(@@merchant_info@@),
    buybox: (txt(@@buybox@@) || '').slice(0, 600)};
})())"""


# ── card parsing (pure) ──────────────────────────────────────────────────────

def money(text: str | None) -> float | None:
    text = re.sub(r"(\d)\s*\.\s*(\d{2})\b", r"\1.\2", text or "")  # priceToPay reads '$399 . 97'
    m = re.search(r"\$\s?([\d,]+(?:\.\d{2})?)", text)
    return float(m.group(1).replace(",", "")) if m else None


EXCLUDE_TITLE = re.compile(r"\bfor parts\b|\bparts only\b|not working|\bbroken\b|\bdefective\b|\bas[- ]is\b", re.I)


def parse_card(card: dict, tax: float | None = None) -> dict:
    """One search card -> a normalized item. Unknowns stay None; nothing is guessed."""
    lines = card.get("lines") or []
    text = " | ".join(lines)
    # sponsored cards prefix the title with Amazon's ad disclaimer, run together (seen live 2026-10-28):
    # "SponsoredSponsored You're seeing this ad based on the product's relevance to your search query.Leave ad feedback  <title>"
    raw_title = card.get("title") or ""
    title = re.sub(r"^(Sponsored\s*)+(You.re seeing this ad.*?search query\.\s*)?(Leave ad feedback\s*)?", "", raw_title).strip()
    delivery = card.get("delivery") or ""
    price = money(card.get("price"))
    if re.search(r"Temporarily out of stock|Currently unavailable|out of stock", delivery + " " + text, re.I):
        availability = "out_of_stock"
    elif price is None:
        availability = "no_offer"  # 'See options' / no featured offer: no single price to track
    else:
        availability = "in_stock"
    if re.search(r"FREE delivery", delivery, re.I):
        shipping = 0.0
    else:
        m = re.search(r"\$\s?([\d,]+\.\d{2})\s+(?:delivery|shipping)", delivery, re.I)
        shipping = float(m.group(1).replace(",", "")) if m else None
    left = re.search(r"Only (\d+) left in stock", delivery + " " + text)
    offers = re.search(r"\$\s?([\d,]+\.\d{2})\s*\((\d+\+?) used & new offers?\)", card.get("secondary") or "")
    rating = re.match(r"([\d.]+) out of 5", card.get("rating") or "")
    reviews = re.match(r"([\d,]+) ratings?", card.get("reviews") or "")
    bought = re.search(r"([\d.]+K?\+?) bought in past month", text)
    item = {
        "id": card["asin"], "asin": card["asin"], "url": f"{BASE}/dp/{card['asin']}", "title": title,
        "price": price, "list_price": money(card.get("list_price")), "shipping": shipping,
        "shipping_text": delivery or None, "prime_only_free": bool(re.search(r"FREE delivery for Prime members", delivery, re.I)),
        "availability": availability, "available": availability == "in_stock",
        "stock_left": int(left.group(1)) if left else None,
        "condition": "Renewed" if re.search(r"\bRenewed\b", title, re.I) else "New",
        "offers_from": {"price": float(offers.group(1).replace(",", "")), "count": offers.group(2),
                        "note": "lowest other offer, condition not shown on the card"} if offers else None,
        "rating": float(rating.group(1)) if rating else None,
        "reviews": int(reviews.group(1).replace(",", "")) if reviews else None,
        "bought_past_month": bought.group(1) if bought else None,
        "sponsored": "Sponsored" in lines[:3] or raw_title.startswith("Sponsored"),
        "prime": bool(card.get("prime")),  # the card's Prime badge (search page)
        "auction": False,
    }
    item["excluded"] = "title: parts / not working" if EXCLUDE_TITLE.search(title) else None
    tax = S.get("sales_tax_rate", 0.07) if tax is None else tax
    item["landed_estimate"] = round((price + shipping) * (1 + tax), 2) if price is not None and shipping is not None else None
    return item


UNAVAILABLE = re.compile(r"currently unavailable|temporarily out of stock|out of stock|no longer available", re.I)


def _field(text: str, label: str) -> str | None:
    """'Sold by Atomic Cellular | In stock' / 'Sold by | Amazon.com' -> the value after the label."""
    m = re.search(label + r"\s*:?\s*\|?\s*([^|]+)", text or "", re.I)
    return m.group(1).strip() if m else None


def parse_product(raw: dict, asin: str) -> dict:
    """A product page read -> what can actually be bought (pure; unknowns stay None).

    Real price = the price of the SELECTED option in the variation menu (the user's rule: the card price can belong to
    another option), else the buy box price. Available = a buy button is on the page (Add to cart / used-offer Add to
    cart / Buy Now) AND the availability line doesn't say unavailable / out of stock - a page can say 'In stock' with
    no buy button, and a card can show a price for something that can't be bought."""
    variants = raw.get("variants") or []
    variant = next((v for v in variants if v.get("asin") == asin), None) or (variants[0] if len(variants) == 1 else None)
    page_price, var_price = money(raw.get("core_price")), money((variant or {}).get("price"))
    price = var_price if var_price is not None else page_price
    buyable = bool(raw.get("add_to_cart") or raw.get("add_to_cart_used") or raw.get("buy_now"))
    avail_text = raw.get("availability") or ""
    if raw.get("out_of_stock") or UNAVAILABLE.search(avail_text) or UNAVAILABLE.search((variant or {}).get("availability") or ""):
        availability = "out_of_stock"
    elif not buyable:
        availability = "no_featured_offer" if raw.get("see_all") else "not_buyable"
    elif price is None:
        availability = "no_price"
    else:
        availability = "in_stock"
    buybox, title = raw.get("buybox") or "", raw.get("title") or ""
    # used = the buy box sells a used copy ('Buy Used', its own Add to Cart); a hidden #usedOfferListingID is no signal
    used = buybox.startswith("Buy Used") or (bool(raw.get("add_to_cart_used")) and not raw.get("add_to_cart"))
    used_cond = re.search(r"Used\s*:\s*(Like New|Very Good|Good|Acceptable)", buybox, re.I)
    condition = (f"Used - {used_cond.group(1)}" if used_cond else "Used") if used else \
        "Renewed" if re.search(r"\bRenewed\b", title, re.I) else "New"
    delivery = raw.get("delivery") or ""
    if re.search(r"FREE delivery", delivery, re.I) and not re.search(r"Prime members", delivery, re.I):
        shipping = 0.0
    else:
        m = re.search(r"\$\s?([\d,]+\.\d{2})\s+(?:delivery|shipping)", delivery, re.I)
        shipping = float(m.group(1).replace(",", "")) if m else None
    seller_text = " | ".join(x for x in (raw.get("merchant"), buybox) if x)
    left = re.search(r"Only (\d+) left in stock", avail_text)
    notes = []
    if var_price is not None and page_price is not None and abs(var_price - page_price) > 0.009:
        notes.append(f"selected option ${var_price:.2f} vs buy box ${page_price:.2f}")
    if raw.get("asin") and raw["asin"] != asin:
        notes.append(f"page shows ASIN {raw['asin']}, not {asin}")
    return {"asin": asin, "page_asin": raw.get("asin"), "url": raw.get("url"), "title": title or None,
            "variant": " / ".join(v["label"] for v in variants if v.get("label")) or None,
            "price": price, "variant_price": var_price, "buybox_price": page_price,
            "availability": availability, "available": availability == "in_stock", "availability_text": avail_text or None,
            "buy_button": buyable, "offer": "used" if used else "new", "condition": condition,
            "prime": bool(raw.get("prime") or (variant or {}).get("prime")),
            "shipping": shipping, "delivery": delivery or None,
            "seller": _field(seller_text, r"Shipper / Seller") or _field(seller_text, r"Sold by"),
            "ships_from": _field(seller_text, r"Ships from"),
            "stock_left": int(left.group(1)) if left else None, "notes": notes, "checked_at": now()}


def search_url(query: str, lo=None, hi=None, page: int = 1, sort: str = "price-asc-rank") -> str:
    p = {"k": query, "s": sort}
    if lo is not None and hi is not None:
        p["rh"] = f"p_36:{int(round(lo * 100))}-{int(round(hi * 100))}"
    if page > 1:
        p["page"] = page
    return f"{BASE}/s?" + urlencode(p)


def refine_url(results_url: str, lo=None, hi=None, sort: str | None = "price-asc-rank") -> str:
    """The results page the search box produced, with the sort and price range applied (what choosing them in the
    page's sort menu and price filter does). Keeps Amazon's own params (k, crid, sprefix, ref); drops page."""
    parts = urlsplit(results_url)
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in ("s", "rh", "page", "low-price", "high-price")]
    if sort:
        q.append(("s", sort))
    if lo is not None and hi is not None:
        q.append(("rh", f"p_36:{int(round(lo * 100))}-{int(round(hi * 100))}"))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), ""))


def query_of(url: str) -> str | None:
    parts = urlsplit(url)
    return dict(parse_qsl(parts.query)).get("k") if parts.path.startswith("/s") else None


# ── session ──────────────────────────────────────────────────────────────────

class AmazonSession:
    def __init__(self, server, out_dir: Path, nav_params: dict | None = None):
        self.s = server
        self.out = out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        self.page_loads = 0
        self.nav_params = nav_params or {}
        self.on_person_needed = None  # callback(message): e.g. the price watch's sound + notification

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

    async def guard(self) -> dict:
        st = await self.js(STATE_JS)
        if needs_person(st):
            st = await self.wait_for_person(st)
        return st

    async def wait_for_person(self, st: dict) -> dict:
        """A robot check, the "I am human" checkbox page or a sign-in: only the PERSON may complete it - the script
        never ticks, types or clicks anything there. Tell them (badge + callback: sound and notification), bring the
        Amazon tab forward, then poll until the page is clear. Not cleared within human_wait_s: Blocked."""
        what = needs_person(st)
        wait_s = int(S.get("human_wait_s", 300))
        msg = f"Amazon shows {what}: please complete it yourself in the Amazon tab (waiting up to {wait_s // 60} min)"
        await self.feedback(msg)
        if self.on_person_needed:
            try:
                self.on_person_needed(msg)
            except Exception:
                pass
        await module_kit.bring_to_front(self.s)
        loop = asyncio.get_running_loop()
        end = loop.time() + wait_s
        while loop.time() < end:
            await asyncio.sleep(3)
            cur = await self._state()
            if cur and not needs_person(cur):
                await self.feedback("check completed by you - continuing")
                return cur
        raise Blocked(f"{what} at {st['url']} not completed within {wait_s // 60} min - complete it in Chrome "
                      "(never bypassed); Amazon is retried next cycle")

    async def goto(self, url: str, ready_keys: list[str], timeout: float = 25.0) -> dict:
        await self._pause()
        for attempt in range(2):
            await module_kit._command(self.s, "navigate", {"url": url, "dismiss_banners": False, **self.nav_params}, timeout_ms=20000)
            ok = await module_kit._poll(lambda: self._present(ready_keys), timeout, 0.6)  # a read mid-load fails: keep polling
            st = await self.guard()
            if ok:
                return st
            if attempt == 0:
                await self.feedback("page slow - reloading once")
        raise RuntimeError(f"none of {ready_keys} appeared at {url}")

    async def _pause(self) -> None:
        if self.page_loads:
            await module_kit.human_pause(S)  # random, person-like pacing between page loads
        self.page_loads += 1

    async def read_down(self, bursts=None) -> dict:
        """Read down the results like a person - mouse-wheel flicks with pauses, sometimes a notch back up - until the
        Next link is in view (or a random number of flicks)."""
        r = await module_kit.read_down(MODULE, self.s, "next_page", tuple(bursts or S.get("read_bursts", [4, 14])))
        await self.feedback(f"Read down the results ({r['bursts']} wheel flicks, {r['via']}, {r['end']})")
        return r

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

    async def _results_for(self, query: str, not_url: str | None = None) -> bool:
        st = await self._state()
        if st is None:
            return False
        if needs_person(st):
            return True  # stop polling; guard() waits for the person
        q = query_of(st["url"])
        return (st["url"] != not_url and q is not None and q.strip().lower() == query.strip().lower()
                and bool(await self._present(["result_card", "no_results"])))

    async def search_form(self, query: str, timeout: float = 25.0) -> dict:
        """Search like a person: click the header search box, clear it, type the query (CDP insertText), check the
        box holds exactly the query, then click the search button with a trusted CDP click. Evidence: a results
        page for this query. Returns the page state."""
        if getattr(self, "on_product", False):
            # trusted typing can't reach a product page (another extension's frame blocks the debugger there, live
            # 2026-10-28): go back to the home page first, like clicking the Amazon logo
            await self.goto(f"{BASE}/", ["search_box"])
            self.on_product = False
        await self._pause()
        if not await module_kit._poll(lambda: self._present(["search_box"]), 10.0, 0.5):
            # no search box here (e.g. a page without the header): go home once, like clicking the logo
            await self.guard()
            await self.goto(MODULE.manifest["app_url"], ["search_box"])
        if not await module_kit._poll(lambda: self._present(["search_box"]), 10.0, 0.5):
            await self.guard()
            raise RuntimeError("the Amazon search box is not on the page")
        await self.feedback(f"Typing '{query}' in the search box, then clicking the search button")
        self.last_via = "search box + button"
        try:
            before = await module_kit.type_and_submit(MODULE, self.s, query, "search_box", "search_submit", S)
            if not await module_kit._poll(lambda: self._results_for(query, before), timeout, 0.6):
                raise RuntimeError(f"clicking search did not open the results for {query!r}")
        except RuntimeError as e:
            # Typing can't reach a hidden tab (the user is using Chrome): open the same search by its URL - and say so.
            if not S.get("form_fallback_url", True):
                raise
            await self.feedback(f"typing didn't take ({e}) - opening the search URL instead")
            await self.goto(search_url(query), ["result_card", "no_results"])
            self.last_via = f"search URL (typing failed: {str(e)[:90]})"
        return await self.guard()

    async def click_next(self, page: int, timeout: float = 12.0) -> str | None:
        """Go to the next results page by clicking the Next link (trusted click); if the click doesn't move the page,
        open the link's own URL. Evidence: the URL's page number. Returns how it got there ('click' / 'link url')."""
        await module_kit.bring_to_front(self.s)
        await self.read_down((8, 40))  # read on down to the Next link first...
        await self._pause()     # ...then a person-like pause before moving on
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

    async def _on_page(self, page: int) -> bool:
        st = await self._state()
        if st is None:
            return False
        return dict(parse_qsl(urlsplit(st["url"]).query)).get("page") == str(page) and \
            bool(await self._present(["result_card", "no_results"]))

    async def product(self, asin: str, settle: float = 8.0) -> dict:
        """Open a product page (like opening the result) and read what can actually be bought: the selected option's
        real price, availability (buy button + availability line), new/used, Prime, delivery, seller. Read-only."""
        await self.feedback(f"Checking product page {asin}: real price, availability, Prime")
        await self.goto(f"{BASE}/dp/{asin}", ["product_title"])
        self.on_product = True
        if S.get("search_via", "form") == "form":  # glance down the page (falls back to scrollBy: no debugger here)
            await module_kit.human_scroll(self.s, max_bursts=random.randint(1, 3))
        deadline = asyncio.get_running_loop().time() + settle
        raw = await self.js(PRODUCT_JS)
        while raw.get("ready") != "complete" or not (raw.get("core_price") or raw.get("availability") or raw.get("buybox")):
            if asyncio.get_running_loop().time() > deadline:
                break
            await asyncio.sleep(0.8)
            raw = await self.js(PRODUCT_JS)
        res = parse_product(raw, asin)
        await self.feedback(f"{asin}: {res['availability']}, ${res['price']}, {res['condition']}, Prime {'yes' if res['prime'] else 'no'}")
        return res

    async def cards(self, settle: float = 10.0) -> dict:
        """Read the cards once the page is complete and the card count is stable (late cards render after the first)."""
        deadline = asyncio.get_running_loop().time() + settle
        data, last = await self.js(CARDS_JS), None
        while asyncio.get_running_loop().time() < deadline:
            if data.get("ready") == "complete" and len(data["cards"]) == last:
                break
            last = len(data["cards"])
            await asyncio.sleep(0.8)
            data = await self.js(CARDS_JS)
        return data


async def search_products(sess: AmazonSession, query: str, lo=None, hi=None, max_pages=None, sort: str = "price-asc-rank") -> dict:
    """Search, read every card, follow pages until none is left, a page adds nothing, or max_pages.

    settings.search_via = "form" (default): type the query in the search box and click the search button, apply the
    sort + price range to the results page, click Next for more pages. The tab comes to the front for the typing
    and clicks. "url": open the search URL directly (works in a background tab)."""
    max_pages = max_pages or S["max_pages"]
    via_form = S.get("search_via", "form") == "form"
    seen, items, excluded, pages, deliver_to = set(), [], [], [], None
    for page in range(1, max_pages + 1):
        await sess.feedback(f"Searching: {query} (${lo}-{hi}) page {page}")
        if not via_form:
            await sess.goto(search_url(query, lo, hi, page, sort), ["result_card", "no_results"])
            how = "url"
        elif page == 1:
            st = await sess.search_form(query)
            how = getattr(sess, "last_via", "search box + button")
            refined = refine_url(st["url"], lo, hi, sort)
            if refined != st["url"]:
                await sess.goto(refined, ["result_card", "no_results"])
                how += ", then sort + price range"
        else:
            how = await sess.click_next(page)
            if not how:
                break
        if via_form:  # look at the page like a person before reading it
            await module_kit.bring_to_front(sess.s)
            await sess.read_down()
        data = await sess.cards()
        deliver_to = deliver_to or data.get("deliver_to")
        new = [c for c in data["cards"] if c["asin"] not in seen]
        for c in new:
            seen.add(c["asin"])
            it = parse_card(c)
            (excluded if it["excluded"] else items).append(it)
        pages.append({"page": page, "via": how, "url": data["url"], "cards": len(data["cards"]), "new": len(new),
                      "count_text": data.get("count"), "no_results": data.get("no_results")})
        await sess.feedback(f"{query}: page {page} - {len(new)} cards, {len(items)} kept so far")
        if not new or not data.get("next"):
            break
    return {"query": query, "min": lo, "max": hi, "fetched_at": now(), "deliver_to": deliver_to, "pages": pages,
            "items": items, "excluded": excluded}


# ── runner ───────────────────────────────────────────────────────────────────

async def open_tab(server, action: str = "search_products", activate: bool = True):
    """Open Amazon in a new tab of our own, run preflight (binds the order to that tab), return the Preflight."""
    await module_kit._command(server, "navigate", {"url": MODULE.manifest["app_url"], "new_tab": True,
                                                   "activate": activate, "dismiss_banners": False}, timeout_ms=20000)
    return await module_kit.preflight(MODULE, server, action)


async def with_session(action: str, fn, out_dir: Path):
    import warnings
    warnings.filterwarnings("ignore")
    from ai_web_auto_backend.automation_controller import BrowserAgent
    agent = BrowserAgent()
    print("Waiting for the AI Web-Auto extension on ws://localhost:8765 ...", flush=True)
    await agent.connect(timeout=60)
    try:
        pf = await open_tab(agent.server, action)
        print(pf.summary(), flush=True)
        if not pf.ok:
            raise SystemExit(1)
        sess = AmazonSession(agent.server, out_dir)
        try:
            return await fn(sess)
        finally:
            await close_tab(agent.server)
    finally:
        await agent.stop()


async def close_tab(server) -> None:
    """Close the tab this order opened (the bound one). Older extensions without close_tab leave it open."""
    try:
        await module_kit._command(server, "close_tab", timeout_ms=5000)
    except Exception:
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--min", type=float)
    p.add_argument("--max", type=float)
    p.add_argument("--pages", type=int)
    p = sub.add_parser("product", help="read product pages: real price of the selected option, availability, Prime")
    p.add_argument("asins", nargs="+")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.cmd == "product":
        out = MODULE_DIR / S["downloads_dir"] / f"{datetime.now():%Y-%m-%d}-products"

        async def read_all(sess):
            return [await sess.product(x) for x in a.asins]
        res = asyncio.run(with_session("check_product", read_all, out))
        (out / f"products-{datetime.now():%H%M%S}.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
        for r in res:
            price = f"${r['price']:.2f}" if r["price"] is not None else "  —   "
            ship = "free" if r["shipping"] == 0 else (f"+${r['shipping']:.2f}" if r["shipping"] else "ship ?")
            print(f"  {r['asin']} {price:>10} {ship:>8} {r['availability']:<17} {r['condition']:<18} "
                  f"Prime {'yes' if r['prime'] else 'no ':<3} seller {r['seller']} | {r['variant'] or ''} {'; '.join(r['notes'])}")
        return 0
    slug = re.sub(r"[^a-z0-9]+", "-", a.query.lower()).strip("-")[:40]
    out = MODULE_DIR / S["downloads_dir"] / f"{datetime.now():%Y-%m-%d}-search-{slug}"
    res = asyncio.run(with_session("search_products", lambda sess: search_products(sess, a.query, a.min, a.max, a.pages), out))
    (out / "search.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nDelivering to: {res['deliver_to']}")
    for it in sorted(res["items"], key=lambda i: (i["price"] is None, i["price"] or 0)):
        price = f"${it['price']:.2f}" if it["price"] is not None else "  —   "
        ship = "free" if it["shipping"] == 0 else (f"+${it['shipping']:.2f}" if it["shipping"] else "ship ?")
        print(f"  {price:>10} {ship:>8} {it['availability']:<12} {it['condition']:<8} {it['asin']}  {it['title'][:70]}")
    print(f"\n{len(res['items'])} items ({len(res['excluded'])} excluded) -> {out / 'search.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
