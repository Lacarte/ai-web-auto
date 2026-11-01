"""Best Buy US product search through the ai-web-auto platform (read-only: never adds to cart, buys, or changes the
store / location).

    python bestbuy_search.py search "rtx 3060 12gb"

Every order runs module_kit.preflight first (Best Buy tab found, bound, ready). Page scripts use placeholders for the
selectors registered in knowledge/locators.json. Results go to @downloads/<date>-search-<query>/.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

MODULE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(MODULE_DIR.parent))
sys.path.insert(0, str(MODULE_DIR.parent.parent))
import module_kit  # noqa: E402

MODULE = module_kit.load(MODULE_DIR)
S = MODULE.settings
BASE = "https://www.bestbuy.com"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Blocked(RuntimeError):
    """Robot check / access denied: the human must act; never bypassed."""


# ── page scripts ─────────────────────────────────────────────────────────────

STATE_JS = r"""JSON.stringify((() => {
  const head = ((document.body || {}).innerText || '').slice(0, 3000);
  return {url: location.href, title: document.title, ready: document.readyState,
    blocked: /Access Denied|unusual activity|are you a human|verify you are a human|captcha/i.test(head + ' ' + document.title)};
})())"""

FEEDBACK_JS = r"""(() => { let b = document.getElementById('awa-bestbuy-badge');
  if (!b) { b = document.createElement('div'); b.id = 'awa-bestbuy-badge';
    b.style.cssText = 'position:fixed;z-index:2147483647;bottom:12px;right:12px;max-width:440px;padding:8px 12px;'
      + 'background:#0046bee6;color:#fff;font:13px/1.4 system-ui;border-radius:8px;pointer-events:none;box-shadow:0 2px 10px #0007';
    document.documentElement.appendChild(b); }
  b.textContent = 'AI Web-Auto · Best Buy · ' + __MSG__; return 'ok'; })()"""

CARDS_JS = r"""JSON.stringify((() => {
  const txt = (root, sel) => { const e = root.querySelector(sel); return e ? (e.innerText || e.textContent || '').replace(/\s+/g, ' ').trim() : null; };
  const out = [];
  for (const card of document.querySelectorAll(@@result_card@@)) {
    // the first product link is the image (no text); the title is the longest link text
    const links = [...card.querySelectorAll(@@card_link@@)].map(a => ({href: a.href, text: (a.innerText || a.textContent || '').replace(/\s+/g, ' ').trim()}));
    const link = links.sort((a, b) => b.text.length - a.text.length)[0];
    out.push({sku: card.getAttribute('data-product-id'),
      title: link && link.text ? link.text : null,
      href: link ? link.href : null,
      price: (card.querySelector(@@card_price@@) || {}).textContent || null,
      comp_value: txt(card, @@card_comp_value@@),
      shipping: txt(card, @@card_shipping@@), pickup: txt(card, @@card_pickup@@),
      unavailable: !!card.querySelector(@@card_unavailable@@),
      rating: txt(card, @@card_rating@@),
      buttons: [...card.querySelectorAll('button')].map(b => ((b.innerText || '').trim() + (b.disabled ? ' [disabled]' : ''))).filter(Boolean),
      lines: (card.innerText || '').split('\n').map(s => s.trim()).filter(Boolean).slice(0, 30)});
  }
  const store = ((document.querySelector(@@store_name@@) || {}).innerText || '').match(/Your store\s*\n?\s*([^\n]+)/);
  return {url: location.href, ready: document.readyState, store: store ? store[1].trim() : null, cards: out};
})())"""


# ── card parsing (pure) ──────────────────────────────────────────────────────

def money(text: str | None) -> float | None:
    m = re.search(r"\$\s?([\d,]+\.\d{2})", text or "")
    return float(m.group(1).replace(",", "")) if m else None


EXCLUDE_TITLE = re.compile(r"\bfor parts\b|\bparts only\b|not working|\bbroken\b|\bdefective\b|\bas[- ]is\b", re.I)


def parse_card(card: dict, tax: float | None = None) -> dict:
    """One search card -> a normalized item. Availability comes from the card's own messages; unknowns stay None.

    Live 2026-10-29: 'Pickup unavailable' + 'Shipping unavailable' + 'Find a Store' = can't be bought online or picked
    up at the user's store; a disabled 'Unavailable' button = not sold at all; 'Add to cart' = buyable."""
    text = " | ".join(card.get("lines") or [])
    buttons = card.get("buttons") or []
    ship_msg, pick_msg = card.get("shipping") or "", card.get("pickup") or ""
    price = money(card.get("price"))
    sold_out = card.get("unavailable") or any(re.match(r"(Sold Out|Unavailable|Coming Soon)", b, re.I) for b in buttons)
    can_add = any(re.match(r"Add to cart", b, re.I) and "[disabled]" not in b for b in buttons)
    ship_ok = bool(ship_msg) and not re.search(r"unavailable", ship_msg, re.I)
    pick_ok = bool(pick_msg) and not re.search(r"unavailable", pick_msg, re.I)
    if sold_out:
        availability = "out_of_stock"
    elif price is None:
        availability = "no_price"
    elif can_add or ship_ok:
        availability = "in_stock"
    elif pick_ok:
        availability = "pickup_only"
    elif ship_msg or pick_msg:
        availability = "unavailable_here"  # 'Shipping unavailable' + 'Pickup unavailable' for the user's store
    else:
        availability = "unknown"
    shipping = 0.0 if re.search(r"\bfree\b", ship_msg, re.I) else None
    rating = re.search(r"Rating ([\d.]+) out of 5 stars with ([\d,]+) reviews?", card.get("rating") or text)
    more = re.search(r"More options from \$\s?([\d,]+\.\d{2})", text)
    title = re.sub(r"^(New!|Top Rated|Best Selling|Sponsored)\s*-\s*", "", card.get("title") or "")  # badge prefix ('New! - PNY ...')
    item = {
        "id": card["sku"], "sku": card["sku"], "url": card.get("href") or f"{BASE}/site/searchpage.jsp?st={card['sku']}",
        "title": title, "price": price, "comp_value": money(card.get("comp_value")),
        "shipping": shipping, "shipping_text": ship_msg or None, "pickup_text": pick_msg or None,
        "availability": availability, "available": availability == "in_stock",
        "condition": "Refurbished" if re.search(r"\brefurbished\b", title, re.I) else "New",
        "more_options_from": {"price": float(more.group(1).replace(",", "")),
                              "note": "other offers (e.g. Open-Box), condition not shown on the card"} if more else None,
        "rating": float(rating.group(1)) if rating else None,
        "reviews": int(rating.group(2).replace(",", "")) if rating else None,
        "sponsored": "Sponsored" in text[:200], "auction": False,
    }
    item["excluded"] = "title: parts / not working" if EXCLUDE_TITLE.search(title) else None
    tax = S.get("sales_tax_rate", 0.07) if tax is None else tax
    item["landed_estimate"] = round((price + shipping) * (1 + tax), 2) if price is not None and shipping is not None else None
    return item


def search_url(query: str) -> str:
    return f"{BASE}/site/searchpage.jsp?" + urlencode({"st": query, "intl": "nosplash"})


def query_of(url: str) -> str | None:
    parts = urlsplit(url)
    return dict(parse_qsl(parts.query)).get("st") if "searchpage" in parts.path else None


# ── session ──────────────────────────────────────────────────────────────────

class BestBuySession:
    def __init__(self, server, out_dir: Path, nav_params: dict | None = None):
        self.s = server
        self.out = out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        self.page_loads = 0
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

    async def guard(self) -> dict:
        st = await self.js(STATE_JS)
        if st.get("blocked"):
            raise Blocked(f"robot check / access denied at {st['url']} - solve it in Chrome, then run again (never bypassed)")
        return st

    async def _pause(self) -> None:
        if self.page_loads:
            await module_kit.human_pause(S)
        self.page_loads += 1

    async def _present(self, keys: list[str]) -> list[str]:
        try:
            return await module_kit._any_present(MODULE, self.s, keys)
        except RuntimeError:
            return []

    async def _state(self) -> dict | None:
        try:
            return await self.js(STATE_JS, retries=0)
        except RuntimeError:
            return None

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

    async def _results_for(self, query: str, not_url: str | None = None) -> bool:
        st = await self._state()
        if st is None:
            return False
        if st.get("blocked"):
            return True  # stop polling; guard() reports it
        q = query_of(st["url"])
        return (st["url"] != not_url and q is not None and q.strip().lower() == query.strip().lower()
                and bool(await self._present(["result_card"])))

    async def search_form(self, query: str, timeout: float = 25.0) -> dict:
        """Type the query in the header box and click Submit search (trusted input). If typing can't reach the page
        (hidden tab), open the same search by URL - and record it (self.last_via)."""
        await self._pause()
        if not await module_kit._poll(lambda: self._present(["search_box"]), 10.0, 0.5):
            # no search box here (e.g. a page without the header): go home once, like clicking the logo
            await self.guard()
            await self.goto(MODULE.manifest["app_url"], ["search_box"])
        if not await module_kit._poll(lambda: self._present(["search_box"]), 10.0, 0.5):
            await self.guard()
            raise RuntimeError("the Best Buy search box is not on the page")
        await self.feedback(f"Typing '{query}' in the search box, then clicking Submit search")
        self.last_via = "search box + Submit search"
        try:
            before = await module_kit.type_and_submit(MODULE, self.s, query, "search_box", "search_submit", S)
            if not await module_kit._poll(lambda: self._results_for(query, before), timeout, 0.6):
                raise RuntimeError(f"Submit search did not open the results for {query!r}")
        except RuntimeError as e:
            if not S.get("form_fallback_url", True):
                raise
            await self.feedback(f"typing didn't take ({e}) - opening the search URL instead")
            await self.goto(search_url(query), ["result_card"])
            self.last_via = f"search URL (typing failed: {str(e)[:90]})"
        return await self.guard()

    async def read_all(self) -> dict:
        """Read down to the page end with wheel flicks: Best Buy renders a card's content only once it is scrolled
        into view, so the whole list is read like a person scrolling through it."""
        await module_kit.bring_to_front(self.s)
        r = await module_kit.human_scroll(self.s, max_bursts=60)
        await self.feedback(f"Read down the results ({r['bursts']} wheel flicks, {r['via']}, {r['end']})")
        return r

    async def cards(self, settle: float = 8.0) -> dict:
        """Read the cards once the page is complete and every card has rendered (or the settle time passes)."""
        deadline = asyncio.get_running_loop().time() + settle
        data = await self.js(CARDS_JS)
        while asyncio.get_running_loop().time() < deadline:
            if data.get("ready") == "complete" and all(c.get("title") for c in data["cards"]):
                break
            await asyncio.sleep(0.8)
            data = await self.js(CARDS_JS)
        return data


async def search_products(sess: BestBuySession, query: str, lo=None, hi=None, max_pages=None) -> dict:
    """Search and read one results page (no pagination was seen for these searches). lo/hi are not sent to Best Buy:
    the caller's price rules decide."""
    via_form = S.get("search_via", "form") == "form"
    await sess.feedback(f"Searching: {query}")
    if via_form:
        await sess.search_form(query)
        how = getattr(sess, "last_via", "search box + Submit search")
        n = (await sess.read_all())["bursts"]
    else:
        await sess.goto(search_url(query), ["result_card"])
        how, n = "url", None
        await sess.js("(() => { scrollTo(0, document.documentElement.scrollHeight); return 1; })()")
    data = await sess.cards()
    items, excluded, seen = [], [], set()
    for c in data["cards"]:
        if not c.get("sku") or c["sku"] in seen or not c.get("title"):
            continue
        seen.add(c["sku"])
        it = parse_card(c)
        (excluded if it["excluded"] else items).append(it)
    unrendered = sum(1 for c in data["cards"] if not c.get("title"))
    await sess.feedback(f"{query}: {len(items)} cards read" + (f", {unrendered} never rendered" if unrendered else ""))
    return {"query": query, "fetched_at": now(), "store": data.get("store"),
            "pages": [{"page": 1, "via": how, "url": data["url"], "cards": len(data["cards"]), "unrendered": unrendered,
                       **({"wheel_flicks": n} if n is not None else {})}],
            "items": items, "excluded": excluded}


# ── runner ───────────────────────────────────────────────────────────────────

async def open_tab(server, action: str = "search_products", activate: bool = True):
    await module_kit._command(server, "navigate", {"url": MODULE.manifest["app_url"], "new_tab": True,
                                                   "activate": activate, "dismiss_banners": False}, timeout_ms=20000)
    return await module_kit.preflight(MODULE, server, action)


async def close_tab(server) -> None:
    try:
        await module_kit._command(server, "close_tab", timeout_ms=5000)
    except Exception:
        pass


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
        sess = BestBuySession(agent.server, out_dir)
        try:
            return await fn(sess)
        finally:
            await close_tab(agent.server)
    finally:
        await agent.stop()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("search")
    p.add_argument("query")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    slug = re.sub(r"[^a-z0-9]+", "-", a.query.lower()).strip("-")[:40]
    out = MODULE_DIR / S["downloads_dir"] / f"{datetime.now():%Y-%m-%d}-search-{slug}"
    res = asyncio.run(with_session("search_products", lambda sess: search_products(sess, a.query), out))
    (out / "search.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nStore: {res['store']}")
    for it in sorted(res["items"], key=lambda i: (i["price"] is None, i["price"] or 0)):
        price = f"${it['price']:.2f}" if it["price"] is not None else "  —   "
        print(f"  {price:>10} {it['availability']:<17} {it['sku']:>9}  {it['title'][:70]}"
              + (f"  [more options from ${it['more_options_from']['price']:.2f}]" if it["more_options_from"] else ""))
    print(f"\n{len(res['items'])} items ({len(res['excluded'])} excluded) -> {out / 'search.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
