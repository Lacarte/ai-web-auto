"""Watch products on several stores (eBay, Amazon): poll saved searches on a timer, alert (sound + Windows
notification) when a matching offer appears, comes back in stock or drops in price, and compare the best
landed price per product across stores. Alert only - never buys, bids, adds to cart or messages a seller.

    python price_watch.py watches/llm-parts.json            # loop (Ctrl+C to stop; closes its tabs)
    python price_watch.py watches/llm-parts.json --once     # one cycle
    python price_watch.py watches/llm-parts.json --report   # rebuild the dashboard from the saved state
    python price_watch.py watches/llm-parts.json --test-alert

Each store gets ONE tab of its own (opened once, then reused and loaded in the background). The first cycle of
a new watch records a baseline and alerts nothing (unless "alert_on_first_cycle": true).
State and reports: @downloads/watch-<name>/ (state.json, alerts.jsonl, compare.json, dashboard.html).
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
MODULES = MODULE_DIR.parent
for p in (MODULES, MODULES.parent, MODULES / "ebay", MODULES / "amazon", MODULES / "bestbuy", MODULES / "pcserverandparts"):
    sys.path.insert(0, str(p))

MIN_INTERVAL_MIN = 5  # never poll faster than this
STORES = ("ebay", "amazon", "bestbuy", "pcsp")
GLOBAL_REJECT = [r"\bbox only\b|\bempty box\b|read (the )?description"]
# Dropped only for a store whose queries ask for parts ("for parts"): the user's rule - parts only when asked for.
PARTS_REJECT = [r"\bfor parts\b|\bparts only\b|not working|\bbroken\b|\bdefective\b|\bas[- ]is\b|\buntested\b"]
PARTS_QUERY = re.compile(r"\bfor\s*parts?\b|\bparts\s*only\b|\bparts\s*/\s*repair\b|\bnot\s*working\b", re.I)


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── normalize store results (pure) ───────────────────────────────────────────

def norm_ebay(it: dict) -> dict:
    return {"store": "ebay", "key": f"ebay:{it['id']}", "id": it["id"], "url": it["url"], "title": it.get("title") or "",
            "price": it.get("price"), "shipping": it.get("shipping"), "condition": it.get("condition"),
            "availability": "in_stock", "available": True, "auction": it.get("auction", False),
            "price_is_range": it.get("price_is_range", False), "fewer_words": it.get("fewer_words", False),
            "excluded": it.get("excluded"), "seller": it.get("seller"),
            "feedback_pct": it.get("feedback_pct"), "feedback_count": it.get("feedback_count"),
            "located_in": it.get("located_in"),
            **({k: it.get(k) for k in ("current_bid", "bids", "time_left", "seconds_left", "ends")} if it.get("auction") else {}),
            "trust": f"{it.get('feedback_pct')}% ({it.get('feedback_count')})" if it.get("feedback_count") is not None else None}


def norm_amazon(it: dict) -> dict:
    return {"store": "amazon", "key": f"amazon:{it['asin']}", "id": it["asin"], "url": it["url"], "title": it.get("title") or "",
            "price": it.get("price"), "shipping": it.get("shipping"), "condition": it.get("condition"),
            "availability": it.get("availability"), "available": it.get("available", False), "auction": False,
            "price_is_range": False, "fewer_words": False, "excluded": it.get("excluded"),
            "rating": it.get("rating"), "reviews": it.get("reviews"), "stock_left": it.get("stock_left"),
            "offers_from": it.get("offers_from"), "list_price": it.get("list_price"),
            "prime_only_free": it.get("prime_only_free"), "sponsored": it.get("sponsored"), "prime": it.get("prime"),
            "trust": f"{it['rating']}★ ({it.get('reviews') or 0})" if it.get("rating") is not None else None}


def norm_bestbuy(it: dict) -> dict:
    return {"store": "bestbuy", "key": f"bestbuy:{it['sku']}", "id": it["sku"], "url": it["url"], "title": it.get("title") or "",
            "price": it.get("price"), "shipping": it.get("shipping"), "condition": it.get("condition"),
            "availability": it.get("availability"), "available": it.get("available", False), "auction": False,
            "price_is_range": False, "fewer_words": False, "excluded": it.get("excluded"),
            "rating": it.get("rating"), "reviews": it.get("reviews"), "comp_value": it.get("comp_value"),
            "more_options_from": it.get("more_options_from"), "shipping_text": it.get("shipping_text"),
            "pickup_text": it.get("pickup_text"), "sponsored": it.get("sponsored"),
            "trust": f"{it['rating']}★ ({it.get('reviews') or 0})" if it.get("rating") is not None else None}


def norm_pcsp(it: dict) -> dict:
    """pcserverandparts.com: shipping is not shown on listings (None); a build-your-own row is a base price."""
    return {"store": "pcsp", "key": f"pcsp:{it['id']}", "id": it["id"], "url": it["url"], "title": it.get("title") or "",
            "price": it.get("price"), "shipping": it.get("shipping"), "condition": it.get("condition"),
            "availability": it.get("availability"), "available": it.get("available", False), "auction": False,
            "price_is_range": False, "fewer_words": False, "excluded": it.get("excluded"), "was": it.get("was"),
            "stock_left": it.get("stock"), "configurable": it.get("configurable"), "trust": None}


# ── decisions (pure) ─────────────────────────────────────────────────────────

def total_of(it: dict) -> float | None:
    if it.get("price") is None or it.get("shipping") is None:
        return None
    return round(it["price"] + it["shipping"], 2)


def rules_for(w: dict, product: dict, store: str) -> dict:
    """The product's rules with its per-store overrides; reject_sets expand to the watch's named patterns."""
    r = {k: v for k, v in product.items() if k not in STORES}
    r.update(product.get(store) or {})
    r["parts_ok"] = any(PARTS_QUERY.search(q) for q in queries_for(product, store))
    r["reject"] = [*GLOBAL_REJECT, *([] if r["parts_ok"] else PARTS_REJECT), *r.get("reject", [])]
    for name in r.get("reject_sets", []):
        r["reject"] += w.get("reject_sets", {}).get(name, [])
    r["flag"] = list(r.get("flag", []))
    for name in r.get("flag_sets", []):
        r["flag"] += w.get("flag_sets", {}).get(name, [])
    return r


def matches(r: dict, it: dict, card_only: bool = False) -> str | None:
    """Why this offer does NOT match the product (None = it matches). card_only: judge the search card alone (used to
    pick which Amazon offers get their product page checked)."""
    title = it.get("title") or ""
    if it.get("excluded") and not any(re.search(p, it["excluded"], re.I) for p in r.get("allow_excluded", [])):
        return it["excluded"]  # allow_excluded: store exclusions this product doesn't mind (e.g. a P520 with "No GPU")
    if it.get("auction"):
        return "auction (a bid is not a price)"
    for p in r.get("require", []):
        if not re.search(p, title, re.I):
            return f"title lacks /{p}/"
    for p in r.get("reject", []):
        if re.search(p, title, re.I):
            return f"title matches /{p}/"
    if not it.get("available"):
        return f"not available ({it.get('availability')})"
    if it.get("price_is_range"):
        return "price is a range (options)"
    total = total_of(it)
    if total is None:
        return "price or shipping unknown"
    if r.get("max_price") and total > r["max_price"]:
        return f"${total:.2f} > max ${r['max_price']}"
    if r.get("min_price") and total < r["min_price"]:
        return f"${total:.2f} < min ${r['min_price']}"
    conds = r.get("conditions")
    if conds and not any(c.lower() in (it.get("condition") or "").lower() for c in conds):
        return f"condition '{it.get('condition')}'"
    if it["store"] == "ebay":
        if (it.get("feedback_count") or 0) < r.get("min_feedback", 25):
            return f"seller has {it.get('feedback_count')} ratings"
        if (it.get("feedback_pct") or 0) < r.get("min_feedback_pct", 97):
            return f"seller {it.get('feedback_pct')}% positive"
    if it["store"] in ("amazon", "bestbuy"):
        if r.get("min_reviews") and (it.get("reviews") or 0) < r["min_reviews"]:
            return f"{it.get('reviews') or 0} reviews"
        if r.get("min_rating") and (it.get("rating") or 0) < r["min_rating"]:
            return f"rating {it.get('rating')}"
        if r.get("require_prime") and not it.get("prime"):
            return "not Prime"
        if r.get("_verify") and not card_only and not it.get("verified"):
            return f"product page not read ({it['page_error']})" if it.get("page_error") else "not yet checked on its product page"
    return None


def notes_for(r: dict, it: dict) -> list[str]:
    out, total = [], total_of(it)
    if r.get("suspicious_below") and total is not None and total < r["suspicious_below"]:
        out.append(f"CHECK: far below the usual price (< ${r['suspicious_below']}) - verify seller, photos, returns")
    for p in r.get("flag", []):
        m = re.search(p, it.get("title") or "", re.I)
        if m:
            out.append(f"CHECK: title says '{m.group(0)}'")
    for p in r.get("prefer", []):
        if re.search(p, it.get("title") or "", re.I):
            out.append(f"has /{p}/")
    if it.get("excluded"):
        out.append(f"store module flagged: {it['excluded']} (allowed for this product)")
    if it.get("prime_only_free"):
        out.append("free delivery for Prime members")
    if it.get("store") == "amazon" and it.get("verified"):
        out.append(f"checked on the product page {it['verified'][5:16].replace('T', ' ')}"
                   + (f": option '{it['variant']}'" if it.get("variant") else "") + (f", seller {it['seller']}" if it.get("seller") else ""))
        out += [f"CHECK: {n}" for n in it.get("page_notes") or []]
    if it.get("store") == "bestbuy":
        if it.get("more_options_from"):
            out.append(f"more options (e.g. Open-Box) from ${it['more_options_from']['price']:.2f}")
        if it.get("shipping_text") and it.get("price") is not None and it.get("shipping") is None:
            out.append(f"shipping: {it['shipping_text']}")
    if it.get("located_in") and it["located_in"] != "United States":
        out.append(f"ships from {it['located_in']}")
    if it.get("stock_left"):
        out.append(f"only {it['stock_left']} left")
    return out


PAGE_PENDING = ("not yet checked on its product page", "product page not read")


def diff(state: dict, pname: str, r: dict, items: list[dict], baseline: bool, cycle_no: int, tax: float) -> list[dict]:
    """Merge one store search into the state; return alert events (none on a baseline cycle)."""
    events = []
    for it in items:
        if it.get("fewer_words"):
            continue
        sk = f"{pname}|{it['key']}"
        why_not = matches(r, it)
        total = total_of(it)
        snap = {"at": now(), "price": it.get("price"), "shipping": it.get("shipping"), "total": total,
                "condition": it.get("condition"), "availability": it.get("availability")}
        rec = state["items"].get(sk)
        ev = None
        if rec is None:
            rec = state["items"][sk] = {"product": pname, "store": it["store"], "id": it["id"], "key": it["key"],
                                        "url": it["url"], "title": it["title"], "first_seen": now(), "history": [snap],
                                        "seen_in_baseline": baseline}
            if why_not is None:
                ev = "NEW"
        else:
            last = rec["history"][-1]
            if (last.get("total"), last.get("condition"), last.get("availability")) != (total, it.get("condition"), it.get("availability")):
                rec["history"].append(snap)
                rec["history"] = rec["history"][-60:]
            if why_not is None:
                was_available = last.get("availability") in (None, "in_stock")
                waited_for_page = (rec.get("why_not") or "").startswith(PAGE_PENDING)
                if total is not None and last.get("total") is not None and total < last["total"] - 0.009:
                    ev = "PRICE DROP"
                elif waited_for_page:
                    # first confirmed on its product page: new to the user unless it was already listed at the baseline
                    ev = None if rec.get("seen_in_baseline") else "NEW"
                elif not was_available:
                    ev = "BACK IN STOCK"
                elif not rec.get("matches"):
                    ev = "NOW MATCHES"
            rec.pop("gone_since", None)
        rec.update(title=it["title"], url=it["url"], matches=why_not is None, why_not=why_not, last_seen=now(),
                   seen_cycle=cycle_no, notes=notes_for(r, it), current={k: it.get(k) for k in (
                       "price", "shipping", "condition", "availability", "trust", "seller", "rating", "reviews",
                       "stock_left", "offers_from", "list_price", "sponsored", "prime", "offer", "variant", "verified",
                       "located_in", "card", "more_options_from", "comp_value", "shipping_text", "pickup_text",
                       "price_is_range", "feedback_pct", "feedback_count")})
        if ev and not baseline:
            e = {"type": ev, "product": pname, "store": it["store"], "id": it["id"], "url": it["url"], "title": it["title"],
                 "total": total, "landed_est": round(total * (1 + tax), 2), "condition": it.get("condition"),
                 "trust": it.get("trust"), "notes": rec["notes"]}
            if it["store"] == "amazon":
                e["prime"] = it.get("prime")
            if ev == "PRICE DROP":
                e["was"] = rec["history"][-2]["total"]
            events.append(e)
    return events


# ── Amazon product pages: the real price and availability (pure) ─────────────

def _age_hours(ts: str | None) -> float:
    if not ts:
        return 1e9
    return (datetime.now() - datetime.fromisoformat(ts)).total_seconds() / 3600


def page_fresh(pg: dict | None, it: dict, max_age_h: float) -> bool:
    """A product-page read still counts if it is recent and the search card's price hasn't changed since."""
    return bool(pg) and _age_hours(pg.get("checked_at")) < max_age_h and pg.get("card_price") == it.get("price")


def apply_page(it: dict, pg: dict) -> dict:
    """Put a product-page read over the search card's values: the page is the truth (real price of the selected
    option, availability from the buy button + availability line, new/used, Prime, seller). A failed read leaves the
    card values and records the error (the offer then can't match)."""
    it["card"] = {k: it.get(k) for k in ("price", "shipping", "availability", "prime")}
    if pg.get("error"):
        it["page_error"] = pg["error"][:120]
        return it
    it.update(availability=pg["availability"], available=pg["available"], prime=pg.get("prime"),
              condition=pg.get("condition") or it.get("condition"), offer=pg.get("offer"), seller=pg.get("seller"),
              variant=pg.get("variant"), verified=pg.get("checked_at"), page_notes=pg.get("notes") or [])
    if pg.get("price") is not None:
        it["price"] = pg["price"]
    if pg.get("shipping") is not None:
        it["shipping"] = pg["shipping"]
    if pg.get("stock_left"):
        it["stock_left"] = pg["stock_left"]
    if it["card"]["price"] is not None and pg.get("price") is not None and abs(it["card"]["price"] - pg["price"]) > 0.009:
        it["page_notes"] = [*it["page_notes"], f"search card said ${it['card']['price']:.2f}"]
    return it


# ── eBay auctions: tracked apart, a current bid is never a price (pure) ──────

def auction_matches(r: dict, it: dict) -> str | None:
    """Why this auction is NOT shown for the product (None = shown). Same title, exclusion and seller rules as Buy
    It Now offers; the current bid + shipping must still be under max_price (else it can't end under it)."""
    title = it.get("title") or ""
    if it.get("excluded") and not any(re.search(p, it["excluded"], re.I) for p in r.get("allow_excluded", [])):
        return it["excluded"]
    for p in r.get("require", []):
        if not re.search(p, title, re.I):
            return f"title lacks /{p}/"
    for p in r.get("reject", []):
        if re.search(p, title, re.I):
            return f"title matches /{p}/"
    bid = it.get("current_bid")
    if bid is None:
        return "no current bid shown"
    if r.get("max_price") and bid + (it.get("shipping") or 0) > r["max_price"]:
        return f"current bid ${bid + (it.get('shipping') or 0):.2f} with shipping already over max ${r['max_price']}"
    conds = r.get("conditions")
    if conds and not any(c.lower() in (it.get("condition") or "").lower() for c in conds):
        return f"condition '{it.get('condition')}'"
    if (it.get("feedback_count") or 0) < r.get("min_feedback", 25):
        return f"seller has {it.get('feedback_count')} ratings"
    if (it.get("feedback_pct") or 0) < r.get("min_feedback_pct", 97):
        return f"seller {it.get('feedback_pct')}% positive"
    return None


def track_auctions(state: dict, pname: str, r: dict, items: list[dict], cycle_no: int) -> list[dict]:
    """Record this cycle's auctions for a product (time left, current bid, bids; watchers come from the listing page).
    Returns the shown (matching) auction records, ending soonest first."""
    book = state.setdefault("auctions", {})
    shown = []
    for it in items:
        if it.get("fewer_words") or not it.get("auction"):
            continue
        sk = f"{pname}|{it['key']}"
        why_not = auction_matches(r, it)
        secs = it.get("seconds_left")
        ends_at = datetime.fromtimestamp(time.time() + secs).isoformat(timespec="minutes") if secs is not None else None
        rec = book.get(sk) or {"product": pname, "store": "ebay", "id": it["id"], "key": it["key"], "first_seen": now(), "history": []}
        snap = {"at": now(), "bid": it.get("current_bid"), "bids": it.get("bids"), "time_left": it.get("time_left")}
        if not rec["history"] or (rec["history"][-1].get("bid"), rec["history"][-1].get("bids")) != (snap["bid"], snap["bids"]):
            rec["history"] = [*rec["history"], snap][-40:]
        rec.update(title=it["title"], url=it["url"], matches=why_not is None, why_not=why_not, last_seen=now(),
                   seen_cycle=cycle_no, ends_at=ends_at or rec.get("ends_at"), notes=notes_for(r, {**it, "price": it.get("current_bid")}),
                   current={k: it.get(k) for k in ("current_bid", "shipping", "bids", "time_left", "seconds_left", "ends",
                                                   "condition", "trust", "seller", "located_in")})
        book[sk] = rec
        if why_not is None:
            shown.append(rec)
    shown.sort(key=lambda a: a.get("ends_at") or "9999")
    return shown


def apply_auction_details(rec: dict, det: dict) -> None:
    """Listing-page facts over the card's: watchers (None when eBay shows no demand signal), bids, bid, time left."""
    rec["details"] = det
    cur = rec["current"]
    for k in ("current_bid", "bids", "time_left", "seconds_left", "ends"):
        if det.get(k) is not None:
            cur[k] = det[k]
    cur["watchers"] = det.get("watchers")
    if det.get("seconds_left") is not None:
        rec["ends_at"] = datetime.fromtimestamp(time.time() + det["seconds_left"]).isoformat(timespec="minutes")
    if det.get("ended"):
        rec["ended"] = True
    snap = {"at": det.get("read_at") or now(), "bid": cur.get("current_bid"), "bids": cur.get("bids"),
            "time_left": cur.get("time_left"), "watchers": det.get("watchers")}
    last = rec["history"][-1] if rec["history"] else None
    if last and (last.get("bid"), last.get("bids")) == (snap["bid"], snap["bids"]):
        last["watchers"] = snap["watchers"]  # same bidding state: the listing page adds the watcher count
    else:
        rec["history"] = [*rec["history"], snap][-40:]


def auctions_now(state: dict, pname: str, cycle_no: int) -> list[dict]:
    """Auctions shown for a product this cycle and not yet ended, ending soonest first."""
    cut = datetime.now().isoformat(timespec="minutes")
    out = [a for a in state.get("auctions", {}).values() if a["product"] == pname and a.get("seen_cycle") == cycle_no
           and a.get("matches") and not a.get("ended") and (a.get("ends_at") or "9999") >= cut]
    return sorted(out, key=lambda a: a.get("ends_at") or "9999")


def mark_gone(state: dict, pname: str, store: str, cycle_no: int) -> None:
    """Offers of this product+store not seen this cycle: sold, ended, out of the range, or pushed past the pages read."""
    for rec in state["items"].values():
        if rec["product"] == pname and rec["store"] == store and rec.get("seen_cycle") != cycle_no and not rec.get("gone_since"):
            rec["gone_since"] = now()


def compare(state: dict, pname: str, cycle_no: int, tax: float) -> dict:
    """Current matching offers for a product, cheapest landed first, and the best per store."""
    cur = [r for r in state["items"].values() if r["product"] == pname and r.get("seen_cycle") == cycle_no and r.get("matches")]
    for r in cur:
        t = total_of(r["current"])
        r["total"], r["landed_est"] = t, round(t * (1 + tax), 2)
    cur.sort(key=lambda r: r["total"])
    by_store = {}
    for r in cur:
        by_store.setdefault(r["store"], r)
    pending = [r for r in state["items"].values() if r["product"] == pname and r.get("seen_cycle") == cycle_no
               and (r.get("why_not") or "").startswith(PAGE_PENDING)]
    unavailable = [r for r in state["items"].values() if r["product"] == pname and r.get("seen_cycle") == cycle_no
                   and r["store"] == "amazon" and (r.get("current") or {}).get("verified")
                   and (r.get("why_not") or "").startswith("not available")]
    return {"product": pname, "matches": len(cur), "best": cur[0] if cur else None, "by_store": by_store, "offers": cur,
            "auctions": auctions_now(state, pname, cycle_no), "pending_pages": len(pending), "unavailable": unavailable}


# ── deals: what goes in the opportunity banner (pure) ───────────────────────

def load_configured(d: Path, max_age_h: float = 24) -> list[dict]:
    """Selectable eBay listings whose options were chosen on the listing page (config_check.py writes configured.json):
    only page-confirmed prices younger than max_age_h count."""
    f = d / "configured.json"
    if not f.exists():
        return []
    rows = json.loads(f.read_text(encoding="utf-8")).get("items", [])
    return [r for r in rows if r.get("confirmed") and _age_hours(r.get("checked_at")) < max_age_h]


def deals_for(w: dict, comps: list[dict], configured: list[dict] | None = None, at: datetime | None = None) -> list[dict]:
    """Offers at or under a product's deal_price (price + shipping), cheapest first:
    - "buy": a matching Buy It Now / store offer (Amazon only once its product page confirmed it - compare() keeps only matches);
    - "configured": a selectable eBay listing priced with the required options SELECTED on the page (never its "from" price);
    - "auction": an eBay auction whose current bid + shipping is under it and that ends within deal_auction_hours
      (default 24). A current bid is not a price: it will rise."""
    at = at or datetime.now()
    out = []
    for c in comps:
        p = w["products"].get(c["product"]) or {}
        deal = p.get("deal_price")
        if not deal:
            continue
        base = {"product": c["product"], "deal_price": deal}
        for r in c.get("offers", []):
            if r.get("total") is not None and r["total"] <= deal:
                cur = r.get("current") or {}
                out.append({**base, "kind": "buy", "store": r["store"], "url": r["url"], "title": r["title"], "total": r["total"],
                            "condition": cur.get("condition"), "notes": r.get("notes") or [],
                            **({"prime": cur.get("prime")} if r["store"] == "amazon" else {})})
        for x in configured or []:
            if x.get("product") != c["product"] or x.get("page_price") is None or x.get("shipping") is None:
                continue
            total = round(x["page_price"] + x["shipping"], 2)
            if total <= deal:
                out.append({**base, "kind": "configured", "store": "ebay", "url": x["url"], "title": x["title"], "total": total,
                            "condition": x.get("condition"), "notes": [f"options selected on the page: {', '.join(x.get('options') or [])}"]
                            + (x.get("notes") or [])})
        hours = p.get("deal_auction_hours", 24)
        for a in c.get("auctions", []):
            row = auction_row(a)
            if row["bid_plus_ship"] is None or row["bid_plus_ship"] > deal or not a.get("ends_at"):
                continue
            left_h = (datetime.fromisoformat(a["ends_at"]) - at).total_seconds() / 3600
            if 0 <= left_h <= hours:
                out.append({**base, "kind": "auction", "store": "ebay", "url": a["url"], "title": a["title"],
                            "total": row["bid_plus_ship"], "condition": row["condition"], "time_left": row["time_left"],
                            "bids": row["bids"], "notes": ["current bid + shipping - NOT a price: it will rise until the end"]})
    for x in out:
        x["landed_est"] = round(x["total"] * (1 + w.get("tax_rate", 0.07)), 2)
    order = {"buy": 0, "configured": 1, "auction": 2}
    return sorted(out, key=lambda x: (order[x["kind"]], x["total"]))


def deal_events(state: dict, deals: list[dict]) -> list[dict]:
    """Alert once per deal (url + total): a new deal, or the same listing cheaper than when it was last alerted."""
    seen = state.setdefault("deals_alerted", {})
    events = []
    for x in deals:
        last = seen.get(x["url"])
        if last is not None and x["total"] >= last - 0.009:
            continue
        seen[x["url"]] = x["total"]
        kind = {"buy": "DEAL", "configured": "DEAL (configured)", "auction": "AUCTION DEAL (bid, not a price)"}[x["kind"]]
        events.append({"type": kind, "product": x["product"], "store": x["store"], "id": x["url"].rsplit("/", 1)[-1],
                       "url": x["url"], "title": x["title"], "total": x["total"], "landed_est": x["landed_est"],
                       "condition": x.get("condition"), "notes": [f"deal price ${x['deal_price']:.2f}"] + list(x.get("notes") or [])})
    return events


def deal_banner(deals: list[dict], w: dict) -> str:
    """The opportunity banner for the top of a dashboard / report (HTML)."""
    e = html.escape
    limits = " · ".join(f"{e(n)} ≤ ${p['deal_price']:.0f}" for n, p in w["products"].items() if p.get("deal_price") and p.get("enabled", True))
    if not deals:
        return (f"<section class='nodeal'><b>No opportunity this cycle.</b> <span class='muted'>Deal prices (price + shipping): "
                f"{limits}</span></section>")
    label = {"buy": "Buy now", "configured": "Buy now (options selected)", "auction": "Auction"}
    rows = "".join(
        f"<tr><td class='chk'>✓</td><td><b>{e(label[x['kind']])}</b><br><span class='st {x['store']}'>{x['store']}</span></td>"
        f"<td>{e(x['product'])}</td><td><a href='{e(x['url'])}' target='_blank'>{e(x['title'][:110])}</a>"
        + "".join(f"<div class='note'>{e(n)}</div>" for n in x.get("notes") or [])
        + (f"<div class='note'>{e(x.get('time_left') or '?')} left · {x.get('bids') if x.get('bids') is not None else '?'} bids</div>" if x["kind"] == "auction" else "")
        + f"</td><td class='num'><b>${x['total']:.2f}</b><br><span class='muted'>~${x['landed_est']:.2f} with tax</span></td>"
        f"<td class='num muted'>deal ≤ ${x['deal_price']:.0f}</td></tr>" for x in deals)
    return (f"<section class='opp'><h2>✓ Opportunity: {len(deals)} offer(s) at or under your deal price</h2>"
            f"<div class='muted'>Check seller, condition, returns and compatibility before buying. Nothing is bought automatically. "
            f"Deal prices: {limits}</div><table>{rows}</table></section>")


# ── alerts ───────────────────────────────────────────────────────────────────

def describe(ev: dict) -> tuple[str, str]:
    head = f"{ev['type']}: {ev['product']} on {ev['store']}" + (" - CHEAPEST NOW" if ev.get("cheapest") else "")
    price = f"${ev['total']:.2f}" + (f" (was ${ev['was']:.2f})" if ev.get("was") else "") + f" ~${ev['landed_est']:.2f} with tax"
    extra = f" | {'; '.join(ev['notes'])}" if ev.get("notes") else ""
    prime = (" | Prime" if ev["prime"] else " | not Prime") if "prime" in ev else ""
    return head, f"{price} | {ev.get('condition') or ''}{prime} | {ev['title'][:80]}{extra} | {ev['url']}"


def alert(title: str, message: str) -> None:
    from ebay_watch import alert as _alert  # sound + Windows toast, message-box fallback
    if title.startswith(("DEAL", "AUCTION DEAL")):
        from sounds import play_opportunity  # an opportunity has its own chime (also played by the deal monitor page)
        play_opportunity()
    _alert(title, message)


# ── reports ──────────────────────────────────────────────────────────────────

CSS = """body{font:14px/1.45 system-ui,Segoe UI,sans-serif;margin:24px;background:#f6f7f9;color:#1d2330}
h1{margin:0 0 4px}.sub{color:#5b6475;margin-bottom:18px}section{background:#fff;border-radius:10px;padding:14px 18px;margin:0 0 18px;box-shadow:0 1px 3px #0001}
h2{margin:0 0 6px;font-size:18px}.best{margin:4px 0 10px}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:6px 8px;border-top:1px solid #eceef2;vertical-align:top}
th{font-size:12px;color:#5b6475;text-transform:uppercase;letter-spacing:.03em}.num{text-align:right;white-space:nowrap}.st{font-weight:600;padding:1px 7px;border-radius:9px;font-size:12px}
.ebay{background:#e8f1fd;color:#0b4fa3}.amazon{background:#fff1dc;color:#8a4b00}.bestbuy{background:#fff7c2;color:#003b9c}.pcsp{background:#e9f7ef;color:#1d6b3a}.note{color:#9a5b00;font-size:12px}.none{color:#8a93a3}.ev{font-size:13px;margin:3px 0}
.drop{color:#0a7a30;font-weight:600}a{color:#0b4fa3;text-decoration:none}a:hover{text-decoration:underline}.muted{color:#8a93a3;font-size:12px}
.opp{border:2px solid #0a7a30;background:#effaf3}.opp h2{color:#0a7a30}.nodeal{background:#f1f2f5}.chk{color:#0a7a30;font-weight:700;font-size:16px}
tr.deal td{background:#effaf3}"""


def prime_label(cur: dict, store: str) -> str:
    if store != "amazon":
        return ""
    return "Prime" if cur.get("prime") else "no" if cur.get("prime") is False else "?"


def auction_row(a: dict) -> dict:
    """One auction for reports: time left vs current bid vs bids vs watchers. The bid is not a price."""
    cur = a["current"]
    bid, ship = cur.get("current_bid"), cur.get("shipping")
    return {"url": a["url"], "title": a["title"], "ends_at": a.get("ends_at"), "time_left": cur.get("time_left"),
            "ends": cur.get("ends"), "current_bid": bid, "shipping": ship,
            "bid_plus_ship": round(bid + ship, 2) if bid is not None and ship is not None else None,
            "bids": cur.get("bids"), "watchers": cur.get("watchers"),
            "watchers_read": (a.get("details") or {}).get("read_at"), "seller": cur.get("seller"), "trust": cur.get("trust"),
            "condition": cur.get("condition"), "located_in": cur.get("located_in"),
            "bid_history": [(h["at"][5:16].replace("T", " "), h.get("bid"), h.get("bids"), h.get("watchers")) for h in a["history"]][-6:]}


def report(w: dict, state: dict, d: Path, comps: list[dict]) -> Path:
    tax = w.get("tax_rate", 0.07)
    deals = deals_for(w, comps, load_configured(d))
    offer = lambda r: {k: r.get(k) for k in ("store", "url", "title", "total", "landed_est")} | {
        "condition": r["current"].get("condition"), **({"prime": r["current"].get("prime"), "checked": r["current"].get("verified")}
                                                       if r["store"] == "amazon" else {})}
    (d / "compare.json").write_text(json.dumps({"at": now(), "cycle": state["cycles"], "products": [
        {**{k: c[k] for k in ("product", "matches")}, "best": c["best"] and offer(c["best"]),
         "by_store": {s: offer(r) for s, r in c["by_store"].items()},
         "auctions": [auction_row(a) for a in c.get("auctions", [])],
         "amazon_unavailable": [{"url": r["url"], "title": r["title"], "why": r.get("why_not")} for r in c.get("unavailable", [])],
         "amazon_waiting_for_page_check": c.get("pending_pages", 0)} for c in comps], "deals": deals},
        indent=1, ensure_ascii=False), encoding="utf-8")
    e = html.escape
    parts = [f"<!DOCTYPE html><html><head><meta charset='utf-8'><meta http-equiv='refresh' content='60'>"
             f"<title>Price watch - {e(w['name'])}</title><style>{CSS}</style></head><body>",
             f"<h1>Price watch: {e(w['name'])}</h1><div class='sub'>Cycle {state['cycles']} at {e(state.get('last_cycle') or '')} · every "
             f"{w['interval_minutes']:g} min · landed = (price + shipping) × {1 + tax:.2f} (tax estimate) · alert only, nothing is bought"
             f"{' · Amazon delivers to ' + e(state['deliver_to']) if state.get('deliver_to') else ''}"
             f"{' · Best Buy store ' + e(state['bestbuy_store']) if state.get('bestbuy_store') else ''}</div>",
             deal_banner(deals, w)]
    if state.get("blocked"):
        parts.append("<section class='nodeal'><b>Skipped this cycle - needs you:</b> " + " · ".join(
            f"<span class='st {s}'>{s}</span> {e(b['why'][:140])}" for s, b in state["blocked"].items())
            + " <span class='muted'>(robot / human checks are never solved by the script; the store is retried next cycle)</span></section>")
    for c in comps:
        p = w["products"][c["product"]]
        deal = p.get("deal_price")
        best = c["best"]
        stores = " · ".join(f"<span class='st {s}'>{s}</span> ${r['total']:.2f}" for s, r in c["by_store"].items()) or "<span class='none'>no store has a match now</span>"
        parts.append(f"<section><h2>{e(c['product'])}</h2><div class='muted'>max ${p.get('max_price')} incl. shipping · "
                     f"{'deal ≤ $%s · ' % p['deal_price'] if deal else ''}"
                     f"{c['matches']} matching offer(s)</div><div class='best'>Best per store: {stores}</div>")
        if best:
            rows = []
            for r in c["offers"][:15]:
                cur, hist = r["current"], [h["total"] for h in r["history"] if h.get("total") is not None]
                trend = (f"<span class='drop'>↓ from ${max(hist):.2f}</span>" if hist and max(hist) > r["total"] + 0.009 else "")
                extra = []
                if cur.get("offers_from"):
                    extra.append(f"other offers from ${cur['offers_from']['price']:.2f} ({e(str(cur['offers_from']['count']))}, condition not shown)")
                is_deal = bool(deal) and r["total"] <= deal
                rows.append(f"<tr{' class=deal' if is_deal else ''}><td>{'<span class=chk>✓</span> ' if is_deal else ''}<span class='st {r['store']}'>{r['store']}</span></td>"
                            f"<td><a href='{e(r['url'])}' target='_blank'>{e(r['title'][:110])}</a>"
                            f"{''.join(f'<div class=note>{e(n)}</div>' for n in r.get('notes', []) + extra)}</td>"
                            f"<td>{e(cur.get('condition') or '')}</td><td>{e(prime_label(cur, r['store']))}</td><td>{e(cur.get('trust') or '')}</td>"
                            f"<td class='num'>${cur['price']:.2f}</td><td class='num'>{'free' if cur['shipping'] == 0 else '$%.2f' % cur['shipping']}</td>"
                            f"<td class='num'><b>${r['total']:.2f}</b><br><span class='muted'>~${r['landed_est']:.2f}</span> {trend}</td>"
                            f"<td class='muted'>{e(r['first_seen'][5:16].replace('T', ' '))}</td></tr>")
            parts.append("<table><tr><th>Store</th><th>Offer</th><th>Condition</th><th>Prime</th><th>Trust</th><th class='num'>Price</th>"
                         "<th class='num'>Ship</th><th class='num'>Total</th><th>First seen</th></tr>" + "".join(rows) + "</table>")
        if c.get("pending_pages"):
            parts.append(f"<div class='muted'>{c['pending_pages']} Amazon offer(s) waiting for their product-page check "
                         f"(real price + availability); they count only once checked.</div>")
        if c.get("unavailable"):
            parts.append("<div class='muted'>Shown in Amazon search but NOT buyable on the product page: " + " · ".join(
                f"<a href='{e(r['url'])}' target='_blank'>{e(r['title'][:50])}</a> ({e((r.get('current') or {}).get('availability') or '')})"
                for r in c["unavailable"][:8]) + "</div>")
        if c.get("auctions"):
            arows = []
            for a in map(auction_row, c["auctions"][:12]):
                money_ = lambda v: "?" if v is None else f"${v:.2f}"
                arows.append(f"<tr><td class='num'><b>{e(a['time_left'] or '?')}</b><br><span class='muted'>{e(a['ends'] or '')}</span></td>"
                             f"<td class='num'>{money_(a['current_bid'])}</td><td class='num'>{'free' if a['shipping'] == 0 else money_(a['shipping'])}</td>"
                             f"<td class='num'>{money_(a['bid_plus_ship'])}</td><td class='num'>{a['bids'] if a['bids'] is not None else '?'}</td>"
                             f"<td class='num'>{a['watchers'] if a['watchers'] is not None else ('not shown' if a['watchers_read'] else 'not read')}</td>"
                             f"<td><a href='{e(a['url'])}' target='_blank'>{e(a['title'][:90])}</a><div class='muted'>{e(a['condition'] or '')} · "
                             f"{e(a['trust'] or '')}{' · ' + e(a['located_in']) if a['located_in'] and a['located_in'] != 'United States' else ''}</div></td></tr>")
            parts.append("<h3 style='margin:14px 0 4px;font-size:15px'>eBay auctions - ending soonest (a current bid is NOT a price: it will rise)</h3>"
                         "<table><tr><th class='num'>Time left</th><th class='num'>Current bid</th><th class='num'>Ship</th><th class='num'>Bid + ship</th>"
                         "<th class='num'>Bids</th><th class='num'>Watchers</th><th>Listing</th></tr>" + "".join(arows) + "</table>")
        parts.append("</section>")
    alerts = []
    if (d / "alerts.jsonl").exists():
        alerts = [json.loads(l) for l in (d / "alerts.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()][-25:]
    parts.append("<section><h2>Recent alerts</h2>" + ("".join(
        f"<div class='ev'>{e(a['at'][5:16].replace('T', ' '))} · <b>{e(a['type'])}</b> · {e(a['product'])} · <span class='st {a['store']}'>{a['store']}</span> "
        f"${a['total']:.2f}{' (was $%.2f)' % a['was'] if a.get('was') else ''} · <a href='{e(a['url'])}' target='_blank'>{e(a['title'][:90])}</a></div>"
        for a in reversed(alerts)) or "<div class='none'>none yet</div>") + "</section></body></html>")
    out = d / "dashboard.html"
    out.write_text("".join(parts), encoding="utf-8")
    return out


# ── one cycle ────────────────────────────────────────────────────────────────

def queries_for(product: dict, store: str) -> list[str]:
    q = product.get("queries", [])
    return list(q.get(store, [])) if isinstance(q, dict) else list(q)


def _blocked(ex: Exception) -> bool:
    return type(ex).__name__ == "Blocked"


def block_store(blocked: dict, store: str, ex: Exception) -> None:
    """Record a store the person didn't unblock (robot check, "I am human", sign-in): skipped for the rest of the
    cycle, one alert, shown on the dashboard. Never solved or clicked by the script."""
    if store in blocked:
        return
    blocked[store] = {"at": now(), "why": str(ex)[:200]}
    print(f"  ! {store} skipped for this cycle: {ex}", flush=True)
    alert(f"{store} needs you", f"{ex} - the other stores continue; {store} is retried next cycle")


async def check_amazon_pages(check, state: dict, r: dict, items: list[dict]) -> None:
    """The user's rule: an Amazon offer counts only when its product page shows it can be bought, at the selected
    option's real price. Fresh reads are reused; the cheapest card-level matches without one are checked, at most
    verify_per_cycle per product (default 5) - the rest wait for the next cycle."""
    pages = state.setdefault("amazon_pages", {})
    max_age = float(r.get("verify_max_age_hours", 6))
    todo = []
    for it in items:
        pg = pages.get(it["id"])
        if page_fresh(pg, it, max_age):
            apply_page(it, pg)
        elif matches(r, it, card_only=True) is None:
            todo.append(it)
    todo.sort(key=lambda it: total_of(it) or 1e9)
    for it in todo[:int(r.get("verify_per_cycle", 5))]:
        card_price = it.get("price")
        try:
            pg = await check("amazon", "product", it["id"])
        except Exception as ex:
            if _blocked(ex):
                raise
            print(f"  ! amazon product page {it['id']}: {ex}", flush=True)
            pg = {"error": str(ex), "checked_at": now()}
        pages[it["id"]] = {**pg, "card_price": card_price}
        apply_page(it, pages[it["id"]])


def apply_ebay_page(it: dict, pg: dict) -> dict:
    """An eBay listing page over its search card: out of stock drops the offer; the page's price and shipping are
    the truth. A failed read leaves the card as it was."""
    if pg.get("error"):
        return it
    if pg.get("available") is False:
        it.update(available=False, availability="out_of_stock (listing page)")
    if pg.get("price") is not None and it.get("price") is not None and abs(pg["price"] - it["price"]) > 0.009:
        it["page_notes"] = [*(it.get("page_notes") or []), f"search card said ${it['price']:.2f}"]
        it["price"] = pg["price"]
    if pg.get("shipping") is not None:
        it["shipping"] = pg["shipping"]
    it["verified"] = pg.get("checked_at")
    return it


async def check_ebay_listings(check, state: dict, r: dict, items: list[dict]) -> None:
    """A search card can show a listing that can't be bought (item 800020842561, 2026-10-30: "This item is out of
    stock", price still on the card). The listing pages of the cheapest matching offers are read (ebay
    verify_per_cycle, default 3); fresh reads (verify_max_age_hours, default 6, same card price) are reused."""
    pages = state.setdefault("ebay_pages", {})
    max_age = float(r.get("verify_max_age_hours", 6))
    todo = []
    for it in items:
        pg = pages.get(it["id"])
        if page_fresh(pg, it, max_age):
            apply_ebay_page(it, pg)
        elif matches(r, it) is None:
            todo.append(it)
    todo.sort(key=lambda it: total_of(it) or 1e9)
    for it in todo[:int(r.get("verify_per_cycle", 3))]:
        card_price = it.get("price")
        try:
            pg = await check("ebay", "listing", it["id"])
        except Exception as ex:
            if _blocked(ex):
                raise
            print(f"  ! ebay listing page {it['id']}: {ex}", flush=True)
            continue  # unread: the card stands
        pages[it["id"]] = {**pg, "card_price": card_price}
        apply_ebay_page(it, pages[it["id"]])


async def read_auction_details(check, state: dict, r: dict, shown: list[dict]) -> None:
    """Watchers are on the listing page only: read it for the soonest-ending shown auctions whose details are older
    than auction_details_max_age_minutes (default 60), at most auction_details per product per cycle (default 3)."""
    max_age_h = float(r.get("auction_details_max_age_minutes", 60)) / 60
    todo = [a for a in shown if _age_hours((a.get("details") or {}).get("read_at")) >= max_age_h]
    for a in todo[:int(r.get("auction_details", 3))]:
        try:
            det = await check("ebay", "auction", a["id"])
        except Exception as ex:
            if _blocked(ex):
                raise
            print(f"  ! ebay auction {a['id']}: {ex}", flush=True)
            continue
        apply_auction_details(a, det)


async def cycle(search, w: dict, state: dict, d: Path, check=None) -> list[dict]:
    """search(store, query, lo, hi, pages[, kind="auction"]) -> normalized items; check(store, kind, id) -> a page read
    (kind "product": Amazon product page, "auction": eBay auction listing), or None to judge by search cards only.
    Returns the alert events of this cycle."""
    baseline = state["cycles"] == 0 and not w.get("alert_on_first_cycle")
    cycle_no = state["cycles"] + 1
    tax = w.get("tax_rate", 0.07)
    events, comps = [], []
    # A store that shows a robot / human check the person didn't clear is skipped for the rest of this cycle; the other
    # stores still run (never bypassed: the store's own session waited for the person first)
    state["blocked"] = blocked = {}
    # A different order every cycle (products, stores, queries), like a person checking things in no fixed order.
    shuffle = random.Random() if w.get("shuffle", True) else None
    order = list(w["products"].items())
    if shuffle:
        shuffle.shuffle(order)
    for pname, product in order:
        if product.get("enabled") is False:
            continue
        stores = list(product.get("stores", w.get("stores", STORES)))
        if shuffle:
            shuffle.shuffle(stores)
        for store in stores:
            if store in blocked:
                continue
            r = rules_for(w, product, store)
            r["_verify"] = store == "amazon" and check is not None and r.get("verify_pages", True)
            ok_searches = 0
            # eBay: each query as Buy It Now (price range) and as auctions (ending soonest, current bid up to the ceiling)
            tasks = [(q, "bin") for q in queries_for(product, store)]
            if store == "ebay" and r.get("auctions", True):
                tasks += [(q, "auction") for q in queries_for(product, store)]
            if shuffle:
                shuffle.shuffle(tasks)
            found, auctions = {}, {}
            for q, kind in tasks:
                try:
                    if kind == "auction":
                        res = await search(store, q, None, r.get("max_price") or r.get("max"), 1, kind="auction")
                    else:
                        res = await search(store, q, r.get("min"), r.get("max"), r.get("pages", 1))
                except Exception as ex:
                    if _blocked(ex):
                        block_store(blocked, store, ex)
                        break
                    print(f"  ! {store} '{q}' ({kind}): {ex}", flush=True)
                    continue
                if kind == "bin":
                    ok_searches += 1
                for it in res:
                    (auctions if it.get("auction") else found).setdefault(it["key"], it)
            items = list(found.values())
            if store == "ebay" and check is not None and r.get("verify_pages", True) and store not in blocked:
                try:
                    await check_ebay_listings(check, state, r, items)
                except Exception as ex:
                    if not _blocked(ex):
                        raise
                    block_store(blocked, store, ex)
            if r["_verify"] and store not in blocked:
                try:
                    await check_amazon_pages(check, state, r, items)
                except Exception as ex:
                    if not _blocked(ex):
                        raise
                    block_store(blocked, store, ex)  # the cards read so far stay unconfirmed: they can't match
            events += diff(state, pname, r, items, baseline, cycle_no, tax)
            if auctions:
                shown = track_auctions(state, pname, r, list(auctions.values()), cycle_no)
                if check is not None and r.get("auction_details", 3) and store not in blocked:
                    try:
                        await read_auction_details(check, state, r, shown)
                    except Exception as ex:
                        if not _blocked(ex):
                            raise
                        block_store(blocked, store, ex)
            if ok_searches and store not in blocked:  # a store cut short by a check: its unseen offers aren't gone
                mark_gone(state, pname, store, cycle_no)
        c = compare(state, pname, cycle_no, tax)
        comps.append(c)
        if c["best"]:
            for ev in events:
                if ev["product"] == pname and ev["url"] == c["best"]["url"]:
                    ev["cheapest"] = True  # the cheapest matching offer across all stores right now
    names = list(w["products"])
    comps.sort(key=lambda c: names.index(c["product"]))  # reports keep the watch file's order
    # deals alert even on the baseline cycle: an offer at or under the deal price is worth knowing about at once
    events += deal_events(state, deals_for(w, comps, load_configured(d)))
    state["cycles"] = cycle_no
    state["last_cycle"] = now()
    (d / "state.json").write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
    with (d / "alerts.jsonl").open("a", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps({"at": now(), **ev}, ensure_ascii=False) + "\n")
    report(w, state, d, comps)
    for ev in events:
        head, msg = describe(ev)
        print(f"\n*** {head}\n    {msg}", flush=True)
        alert(head, msg)
    print(f"[{now()}] cycle {cycle_no}{' (baseline - only deal alerts)' if baseline else ''}: {len(events)} alert(s)", flush=True)
    for c in comps:
        best = c["best"]
        line = f"${best['total']:.2f} on {best['store']} - {best['title'][:60]}" if best else "no matching offer now"
        print(f"    {c['product']:<26} {c['matches']:>3} match(es); best {line}", flush=True)
        if c.get("auctions"):
            a = auction_row(c["auctions"][0])
            print(f"    {'':<26} {len(c['auctions']):>3} auction(s); ending first: {a['time_left']} left, bid ${a['current_bid']:.2f}, "
                  f"{a['bids']} bids, watchers {a['watchers'] if a['watchers'] is not None else '?'} - {a['title'][:50]}", flush=True)
        if c.get("pending_pages") or c.get("unavailable"):
            print(f"    {'':<26} Amazon: {c.get('pending_pages', 0)} waiting for a product-page check, "
                  f"{len(c.get('unavailable', []))} shown in search but not buyable", flush=True)
    return events


# ── browser: one tab per store ───────────────────────────────────────────────

class StoreTabs:
    """Each store gets one tab of its own, opened once (in front, so preflight picks it) and then reused with
    background loads. A tab the user closed is reopened on the next search."""
    BG = {"activate": False, "dismiss_banners": False}

    def __init__(self, server, d: Path):
        self.s, self.d, self.tabs, self.sess = server, d, {}, {}

    async def _alive(self, tab_id) -> bool:
        import module_kit
        resp = await module_kit._command(self.s, "list_tabs")
        return any(t.get("id") == tab_id for t in (getattr(resp.data, "tabs", None) or []))

    async def ensure(self, store: str):
        import module_kit
        if store in self.tabs:
            self.s.unbind()
            if await self._alive(self.tabs[store]):
                self.s.bind(self.tabs[store])
                return self.sess[store]
            print(f"  · the {store} tab was closed - opening a new one", flush=True)
        if store == "ebay":
            import ebay_search as M
            await module_kit._command(self.s, "navigate", {"url": M.MODULE.manifest["app_url"], "new_tab": True,
                                                           "dismiss_banners": False}, timeout_ms=20000)
            pf = await module_kit.preflight(M.MODULE, self.s, "search_products")
            sess = M.EbaySession(self.s, self.d / "ebay", nav_params=self.BG)
        elif store == "amazon":
            import amazon_search as M
            pf = await M.open_tab(self.s)
            sess = M.AmazonSession(self.s, self.d / "amazon", nav_params=self.BG)
        elif store == "bestbuy":
            import bestbuy_search as M
            pf = await M.open_tab(self.s)
            sess = M.BestBuySession(self.s, self.d / "bestbuy", nav_params=self.BG)
        else:
            import pcsp_search as M
            pf = await M.open_tab(self.s)
            sess = M.PcspSession(self.s, self.d / "pcsp", nav_params=self.BG)
        print(pf.summary(), flush=True)
        if not pf.ok:
            raise RuntimeError(f"{store} preflight failed: {pf.problem}")
        self.tabs[store], self.sess[store] = pf.tab_id, sess
        return sess

    async def search(self, store: str, query: str, lo, hi, pages: int, kind: str = "bin"):
        sess = await self.ensure(store)
        if store == "ebay":
            import ebay_search as M
            # Buy It Now: price + shipping, lowest first; auctions: ending soonest
            res = await M.search_products(sess, query, lo, hi, max_pages=pages, sort=15, auction=kind == "auction")
            return [norm_ebay(i) for i in res["items"] + res["excluded"]]
        if store == "pcsp":
            import pcsp_search as M
            res = await M.search_products(sess, query, lo, hi, max_pages=pages)
            return [norm_pcsp(i) for i in res["items"]]
        if store == "bestbuy":
            import bestbuy_search as M
            res = await M.search_products(sess, query, lo, hi, max_pages=pages)
            self.bestbuy_store = res.get("store") or getattr(self, "bestbuy_store", None)
            return [norm_bestbuy(i) for i in res["items"] + res["excluded"]]
        import amazon_search as M
        res = await M.search_products(sess, query, lo, hi, max_pages=pages)
        self.deliver_to = res.get("deliver_to") or getattr(self, "deliver_to", None)
        return [norm_amazon(i) for i in res["items"] + res["excluded"]]

    async def check(self, store: str, kind: str, item_id: str) -> dict:
        """Read one page: an Amazon product page (real price, availability, Prime) or an eBay auction listing
        (bids, time left, watchers). Read-only."""
        sess = await self.ensure(store)
        if store == "amazon" and kind == "product":
            return await sess.product(item_id)
        if store == "ebay" and kind == "auction":
            import ebay_search as M
            return await M.read_auction(sess, item_id)
        if store == "ebay" and kind == "listing":
            import ebay_search as M
            M.set_variation_require(None)
            rec = await M.read_listing(sess, item_id)
            return {"available": rec["available"], "price": rec["price"], "shipping": rec["shipping_to_zip"],
                    "condition": rec["condition"], "checked_at": now()}
        raise ValueError(f"no {kind} check for {store}")

    async def close(self) -> None:
        import module_kit
        for store, tab in self.tabs.items():
            try:
                self.s.bind(tab)
                await module_kit._command(self.s, "close_tab", timeout_ms=5000)
            except Exception:
                pass
        self.s.unbind()


# ── main ─────────────────────────────────────────────────────────────────────

def pick_port(want: str | int = "auto") -> int:
    """'auto': 8765 unless another program already listens there (then 8766 - the extension's second address)."""
    if str(want) != "auto":
        return int(want)
    import socket
    for port in (8765, 8766):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)  # a closed loopback port takes ~2 s to refuse on Windows
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise SystemExit("ports 8765 and 8766 are both in use - stop the other backend first")


def load_watch(path: Path) -> dict:
    w = json.loads(path.read_text(encoding="utf-8"))
    w["interval_minutes"] = max(MIN_INTERVAL_MIN, float(w.get("interval_minutes", 10)))
    return w


def next_wait(w: dict, rng=random) -> float:
    """Seconds until the next cycle: interval_minutes varied by +/- interval_jitter (default 20%), never under the
    5-minute floor - so checks don't land on a fixed beat."""
    j = float(w.get("interval_jitter", 0.2))
    return max(MIN_INTERVAL_MIN * 60, w["interval_minutes"] * 60 * rng.uniform(1 - j, 1 + j))


def state_dir(w: dict) -> Path:
    d = MODULE_DIR / "@downloads" / f"watch-{w['name']}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_state(d: Path) -> dict:
    p = d / "state.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"items": {}, "cycles": 0, "created": now()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("watch", help="watches/<name>.json")
    ap.add_argument("--once", action="store_true", help="run one cycle and exit")
    ap.add_argument("--report", action="store_true", help="rebuild dashboard.html from the saved state and exit")
    ap.add_argument("--test-alert", action="store_true", help="play the alert and exit")
    ap.add_argument("--port", default="auto", help="backend port; auto = 8765, or 8766 when another program holds 8765 "
                                                    "(AI Web-Auto tries both)")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.test_alert:
        alert("Price watch: test", "If you see and hear this, alerts work.")
        time.sleep(2)
        return 0
    path = Path(a.watch) if Path(a.watch).exists() else MODULE_DIR / a.watch
    w = load_watch(path)
    d = state_dir(w)
    state = load_state(d)
    if a.report:
        comps = [compare(state, p, state["cycles"], w.get("tax_rate", 0.07)) for p in w["products"]]
        print(report(w, state, d, comps))
        return 0

    async def run():
        import warnings
        warnings.filterwarnings("ignore")
        from ai_web_auto_backend.automation_controller import BrowserAgent
        port = pick_port(a.port)
        agent = BrowserAgent(port=port)
        print(f"Waiting for the AI Web-Auto extension on ws://localhost:{port} ...", flush=True)
        await agent.connect(timeout=60)
        tabs = StoreTabs(agent.server, d)
        try:
            while True:
                await cycle(tabs.search, w, state, d, check=tabs.check)
                if getattr(tabs, "bestbuy_store", None):
                    state["bestbuy_store"] = tabs.bestbuy_store
                if getattr(tabs, "deliver_to", None) and state.get("deliver_to") != tabs.deliver_to:
                    state["deliver_to"] = tabs.deliver_to
                    (d / "state.json").write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
                print(f"    dashboard: {d / 'dashboard.html'}", flush=True)
                if a.once:
                    return
                wait = next_wait(w)
                print(f"    next check at {datetime.fromtimestamp(time.time() + wait):%H:%M:%S} (about every "
                      f"{w['interval_minutes']:g} min, varied; Ctrl+C to stop)", flush=True)
                await asyncio.sleep(wait)
        finally:
            await tabs.close()
            await agent.stop()

    backoff = 60
    while True:
        try:
            asyncio.run(run())
            return 0
        except KeyboardInterrupt:
            print("stopped")
            return 0
        except Exception as ex:
            if type(ex).__name__ == "Blocked":
                alert("Price watch stopped", f"{ex}")
                print(f"! {ex}", flush=True)
                return 3
            print(f"! session error: {ex}", flush=True)
            if a.once:
                return 1
        print(f"    reconnecting in {backoff} s", flush=True)
        time.sleep(backoff)
        backoff = min(backoff * 2, 900)


if __name__ == "__main__":
    sys.exit(main())
