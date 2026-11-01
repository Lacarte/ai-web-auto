"""pcserverandparts.com (PCSP) listing reader through the ai-web-auto platform (read-only: never adds to cart or
configures a build).

    python pcsp_search.py read "https://pcserverandparts.com/refurbished-graphics-cards/?_bc_fsnf=1&Graphics+Card+Memory=16GB"
    python pcsp_search.py read "thinkstation p620"          # search words -> search.php?search_query=

Three listing layouts (live 2026-10-29): product cards ('Was: $461.99 | $193.02 | 10 in stock | Add to cart'), ready
systems (specs | price | stock | Add to cart) and build-your-own rows ('FROM | $1,364.98 | Custom build | Configure' -
a base price, not a complete system). Shipping cost is not shown on listings: it stays unknown (None).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

MODULE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(MODULE_DIR.parent))
sys.path.insert(0, str(MODULE_DIR.parent.parent))
import module_kit  # noqa: E402

MODULE = module_kit.load(MODULE_DIR)
S = MODULE.settings
BASE = "https://pcserverandparts.com"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Blocked(RuntimeError):
    """Robot check / access denied: the human must act; never bypassed."""


STATE_JS = r"""JSON.stringify((() => ({url: location.href, title: document.title, ready: document.readyState,
  not_found: /Not Found/i.test(document.title),
  blocked: /access denied|are you a human|verify you are human|captcha|attention required/i.test(((document.body || {}).innerText || '').slice(0, 3000) + document.title)}))())"""

ROWS_JS = r"""JSON.stringify((() => {
  const seen = new Set(), rows = [];
  for (const r of document.querySelectorAll(@@listing@@)) {
    const id = r.getAttribute('data-entity-id');
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const t = r.querySelector(@@listing_title@@), a = r.matches('a[href]') ? r : r.querySelector(@@listing_link@@);
    rows.push({id, sku: r.getAttribute('data-sku'), layout: (r.className || '').toString().trim().split(/\s+/)[0],
      title: t ? (t.innerText || '').replace(/\s+/g, ' ').trim() : null, href: a ? a.href : null,
      buttons: [...r.querySelectorAll(@@add_to_cart@@)].map(b => (b.innerText || '').trim()).filter(Boolean),
      lines: (r.innerText || '').split('\n').map(s => s.trim()).filter(Boolean)});
  }
  const next = document.querySelector(@@next_page@@);
  return {url: location.href, ready: document.readyState, title: document.title, rows, next: next ? next.href : null};
})())"""

PRICE_LINE = re.compile(r"^\$\s?([\d,]+(?:\.\d{2})?)$")
NOISE = re.compile(r"^(SAVE \$|FROM$|Custom build$|Configure$|Add to cart$|Was:|Now:|\(Inc\. Tax\)|[A-Z0-9]{8,}$|\d+\+? in stock$)")


def parse_row(row: dict, tax: float | None = None) -> dict:
    """One listing -> a normalized item (pure). The price is the line that is a bare $ amount and not a 'Was:' price;
    a build-your-own row's price is a base ('from') price and the item is not counted as buyable as listed."""
    lines = row.get("lines") or []
    text = " | ".join(lines)
    price, prev = None, ""
    for l in lines:
        m = PRICE_LINE.match(l)
        if m and prev.strip() not in ("Was:", "Was"):  # a bare amount right after a lone 'Was:' label is the old price
            price = float(m.group(1).replace(",", ""))
            break
        prev = l
    was = re.search(r"Was:\s*\$\s?([\d,]+(?:\.\d{2})?)", text)
    configurable = "Configure" in row.get("buttons", []) or "Custom build" in lines or "FROM" in lines or row.get("layout") == "pcspByoL__row"
    stock = re.search(r"(\d+\+?) in stock", text)
    out_of_stock = bool(re.search(r"out of stock|sold out", text, re.I))
    title = row.get("title") or ""
    if not title:  # ready / BYO rows: the text up to the price is the description
        parts = []
        for l in lines:
            if PRICE_LINE.match(l) or l.startswith("Was:"):
                break
            if not NOISE.match(l):
                parts.append(l)
        title = " | ".join(parts)
    can_buy = any(re.match(r"Add to cart", b, re.I) for b in row.get("buttons", []))
    if out_of_stock:
        availability = "out_of_stock"
    elif configurable:
        availability = "configure"  # base price of a build-your-own: not a complete, buyable system as listed
    elif price is None:
        availability = "no_price"
    elif can_buy:
        availability = "in_stock"
    else:
        availability = "unknown"
    cond = re.search(r"\b(Used|Refurbished|New|Open Box)\b", title, re.I)
    item = {"id": row["id"], "sku": row.get("sku"), "url": row.get("href") or BASE, "title": title, "price": price,
            "was": float(was.group(1).replace(",", "")) if was else None, "shipping": None,
            "availability": availability, "available": availability == "in_stock", "configurable": configurable,
            "stock": stock.group(1) if stock else None, "condition": cond.group(1).title() if cond else None,
            "auction": False}
    item["excluded"] = None
    return item


def listing_url(query: str) -> str:
    return query if query.startswith("http") else f"{BASE}/search.php?" + urlencode({"search_query": query})


class PcspSession:
    def __init__(self, server, out_dir: Path, nav_params: dict | None = None):
        self.s, self.out, self.page_loads, self.nav_params = server, out_dir, 0, nav_params or {}
        out_dir.mkdir(parents=True, exist_ok=True)

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

    async def _present(self, keys):
        try:
            return await module_kit._any_present(MODULE, self.s, keys)
        except RuntimeError:
            return []

    async def goto(self, url: str, timeout: float = 25.0) -> dict:
        if self.page_loads:
            await module_kit.human_pause(S)
        self.page_loads += 1
        await module_kit._command(self.s, "navigate", {"url": url, "dismiss_banners": False, **self.nav_params}, timeout_ms=20000)

        async def ready():
            try:
                st = await self.js(STATE_JS, retries=0)
            except RuntimeError:
                return None
            return st if (st.get("not_found") or st.get("blocked") or (st.get("ready") == "complete" and await self._present(["listing"]))) else None
        st = await module_kit._poll(ready, timeout, 0.7) or await self.js(STATE_JS)
        if st.get("blocked"):
            raise Blocked(f"robot check / access denied at {st['url']} - solve it in Chrome (never bypassed)")
        return st


async def search_form(sess: PcspSession, query: str, timeout: float = 25.0) -> str:
    """Type the words into the header search box and click Search, like a person (the user asked, 2026-10-29). If typing
    can't reach the page (hidden tab / another extension's frame), open the search URL instead - and say so."""
    try:
        if not await module_kit._poll(lambda: sess._present(["site_search"]), 10.0, 0.5):
            await sess.goto(MODULE.manifest["app_url"])
        await sess.feedback(f"Typing '{query}' in the search box, then clicking Search")
        if sess.page_loads:
            await module_kit.human_pause(S)
        sess.page_loads += 1
        before = await module_kit.type_and_submit(MODULE, sess.s, query, "site_search", "search_submit", S)

        async def results():
            try:
                st = await sess.js(STATE_JS, retries=0)
            except RuntimeError:
                return None
            return st if st["url"] != before and "search_query=" in st["url"] and st["ready"] == "complete" else None
        if not await module_kit._poll(results, timeout, 0.7):
            raise RuntimeError(f"clicking Search did not open the results for {query!r}")
        return "search box + Search button"
    except RuntimeError as e:
        if not S.get("form_fallback_url", True):
            raise
        await sess.feedback(f"typing didn't take ({e}) - opening the search URL instead")
        await sess.goto(listing_url(query))
        return f"search URL (typing failed: {str(e)[:80]})"


async def search_products(sess: PcspSession, query: str, lo=None, hi=None, max_pages=None) -> dict:
    """Read every listing of a category / search URL, or search words typed into the site's search box.
    lo/hi are not sent: the caller's rules decide."""
    url, items, pages = listing_url(query), [], []
    typed = not query.startswith("http") and S.get("search_via", "form") == "form"
    for page in range(1, (max_pages or S.get("max_pages", 2)) + 1):
        await sess.feedback(f"PCSP: {query} page {page}")
        if page == 1 and typed:
            how = await search_form(sess, query)
            st = await sess.js(STATE_JS)
            if st.get("blocked"):
                raise Blocked(f"robot check / access denied at {st['url']} - solve it in Chrome (never bypassed)")
            await module_kit._poll(lambda: sess._present(["listing"]), 10.0, 0.6)
        else:
            how = "url"
            st = await sess.goto(url)
        if st.get("not_found"):
            pages.append({"page": page, "url": st["url"], "not_found": True})
            break
        await module_kit.bring_to_front(sess.s)
        r = await module_kit.human_scroll(sess.s, max_bursts=25)
        data = await sess.js(ROWS_JS)
        items += [parse_row(x) for x in data["rows"]]
        pages.append({"page": page, "via": how, "url": data["url"], "rows": len(data["rows"]), "wheel_flicks": r["bursts"]})
        if not data.get("next"):
            break
        url = data["next"]
    return {"query": query, "fetched_at": now(), "pages": pages, "items": items, "excluded": []}


async def open_tab(server, action: str = "read_listing", activate: bool = True):
    await module_kit._command(server, "navigate", {"url": MODULE.manifest["app_url"], "new_tab": True,
                                                   "activate": activate, "dismiss_banners": False}, timeout_ms=20000)
    return await module_kit.preflight(MODULE, server, action)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("read")
    p.add_argument("query")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    async def run():
        import warnings
        warnings.filterwarnings("ignore")
        from ai_web_auto_backend.automation_controller import BrowserAgent
        agent = BrowserAgent()
        await agent.connect(timeout=60)
        try:
            pf = await open_tab(agent.server)
            print(pf.summary(), flush=True)
            if not pf.ok:
                raise SystemExit(1)
            try:
                return await search_products(PcspSession(agent.server, MODULE_DIR / S["downloads_dir"]), a.query)
            finally:
                await module_kit._command(agent.server, "close_tab", timeout_ms=5000)
        finally:
            await agent.stop()
    res = asyncio.run(run())
    for it in sorted(res["items"], key=lambda i: (i["price"] is None, i["price"] or 0)):
        print(f"  {('$%.2f' % it['price']) if it['price'] is not None else '—':>10} {it['availability']:<12} {it['stock'] or '':>4}  {it['title'][:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
