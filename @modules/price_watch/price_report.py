"""One self-contained HTML price report from the saved state of one or more watches (read-only: no browser).

    python price_report.py OUT.html watches/llm-parts.json watches/llm-alternatives.json [--notes notes.json]

For every product of every watch, per store: offers within budget, offers over budget (what the market really asks),
offers shown but not buyable, offers waiting for a check, eBay auctions (current bid is NOT a price), and price stats.
Only the last cycle's observations are used. --notes adds the analyst's summary / recommendations / research sections
(JSON: {"title", "summary": [html...], "sections": [{"title", "html"}], "product_notes": {product: html}}).
"""
from __future__ import annotations

import argparse
import html
import json
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(MODULE_DIR))
import price_watch as PW  # noqa: E402

E = html.escape
STORE_NAMES = {"ebay": "eBay", "amazon": "Amazon", "bestbuy": "Best Buy", "pcsp": "PCSP"}


def money(v) -> str:
    return "—" if v is None else f"${v:,.2f}"


def classify(rec: dict) -> str:
    w = rec.get("why_not") or ""
    if rec.get("matches"):
        return "match"
    if re.match(r"^\$[\d,.]+ > max", w):
        return "over"
    if w.startswith("not available (configure)"):
        return "byo"  # build-your-own base price: not a complete system
    if w.startswith("not available"):
        return "unavailable"
    if w.startswith(PW.PAGE_PENDING):
        return "pending"
    if w.startswith(("seller has", "seller ")):
        return "trust"
    if w.startswith("price or shipping unknown") and rec["store"] == "pcsp" and (rec.get("current") or {}).get("price") is not None:
        return "price_only"  # PCSP shows no shipping on listings: judged by price alone, shown apart
    if w.startswith(("price or shipping unknown", "price is a range")):
        return "unknown"
    if re.match(r"^\$[\d,.]+ < min", w):
        return "under"
    return "rejected"  # title rules / store exclusions: not the product


def title_ok(w: dict | None, p: dict | None, store: str, title: str) -> bool:
    """Re-check the title against the watch's CURRENT require/reject rules (rules can tighten after a cycle ran, e.g.
    the user's 64GB-RAM rule for workstations)."""
    if not w or not p:
        return True
    r = PW.rules_for(w, p, store)
    return all(re.search(x, title or "", re.I) for x in r.get("require", [])) and         not any(re.search(x, title or "", re.I) for x in r.get("reject", []))


def product_data(state: dict, pname: str, tax: float, w: dict | None = None) -> dict:
    cyc = state.get("cycles", 0)
    p = (w or {}).get("products", {}).get(pname)
    recs = [r for r in state["items"].values() if r["product"] == pname and r.get("seen_cycle") == cyc]
    groups: dict[str, list] = {}
    for r in recs:
        cur = r.get("current") or {}
        t = PW.total_of(cur)
        cls = classify(r) if title_ok(w, p, r["store"], r.get("title")) else "rejected"
        r = {**r, "total": t, "landed": round(t * (1 + tax), 2) if t is not None else None, "cls": cls}
        groups.setdefault(r["cls"], []).append(r)
    for g in groups.values():
        g.sort(key=lambda r: (r["total"] is None, r["total"] or (r.get("current") or {}).get("price") or 0))
    auctions = [a for a in state.get("auctions", {}).values() if a["product"] == pname and a.get("seen_cycle") == cyc
                and title_ok(w, p, "ebay", a.get("title"))]
    shown = sorted([a for a in auctions if a.get("matches")], key=lambda a: a.get("ends_at") or "9999")
    over = sorted([a for a in auctions if not a.get("matches") and "already over max" in (a.get("why_not") or "")],
                  key=lambda a: (a.get("current") or {}).get("current_bid") or 0)
    relevant = [r for k in ("match", "over", "unavailable", "pending", "trust", "unknown", "price_only") for r in groups.get(k, [])]
    stats = {}
    for store in PW.STORES:
        prices = [(r.get("current") or {}).get("price") for r in relevant if r["store"] == store]
        prices = sorted(p for p in prices if p is not None)
        if prices:
            stats[store] = {"n": len(prices), "min": prices[0], "median": round(statistics.median(prices), 2), "max": prices[-1],
                            "buyable": sum(1 for r in relevant if r["store"] == store and r["cls"] in ("match", "over"))}
    return {"groups": groups, "auctions": shown, "auctions_over": over, "stats": stats,
            "rejected": len(groups.get("rejected", [])), "seen": len(recs)}


CSS = """
:root{--ink:#1c2230;--mute:#667085;--line:#e6e8ee;--bg:#f5f6f8;--ok:#0a7a30;--warn:#9a5b00;--bad:#b42318}
*{box-sizing:border-box}body{font:14px/1.5 system-ui,Segoe UI,sans-serif;margin:0;background:var(--bg);color:var(--ink)}
.wrap{max-width:1280px;margin:0 auto;padding:28px 24px 60px}h1{font-size:26px;margin:0 0 4px}h2{font-size:20px;margin:0 0 8px}
h3{font-size:15px;margin:16px 0 6px}.sub{color:var(--mute)}section{background:#fff;border-radius:12px;padding:18px 22px;margin:18px 0;box-shadow:0 1px 3px #0001}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}.card{border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card .k{font-size:12px;color:var(--mute);text-transform:uppercase;letter-spacing:.04em}.card .v{font-size:22px;font-weight:650;margin:2px 0}
table{border-collapse:collapse;width:100%;margin:4px 0 8px}th,td{text-align:left;padding:6px 8px;border-top:1px solid var(--line);vertical-align:top}
th{font-size:11.5px;color:var(--mute);text-transform:uppercase;letter-spacing:.03em;border-top:0}.num{text-align:right;white-space:nowrap}
.st{font-weight:600;padding:1px 8px;border-radius:9px;font-size:12px;white-space:nowrap}.ebay{background:#e8f1fd;color:#0b4fa3}
.amazon{background:#fff1dc;color:#8a4b00}.bestbuy{background:#fff7c2;color:#003b9c}.note{color:var(--warn);font-size:12px}
.muted{color:var(--mute);font-size:12px}.ok{color:var(--ok);font-weight:600}.bad{color:var(--bad);font-weight:600}a{color:#0b4fa3;text-decoration:none}
a:hover{text-decoration:underline}details{margin:6px 0}summary{cursor:pointer;color:#0b4fa3;font-weight:600}.pill{display:inline-block;padding:0 7px;border-radius:8px;font-size:12px;background:#eef0f4;margin-right:4px}
.callout{border-left:4px solid #0b4fa3;background:#f3f7fe;padding:10px 14px;border-radius:6px;margin:8px 0}.callout.warn{border-color:#c47a00;background:#fff8ec}
ul{margin:6px 0 6px 18px;padding:0}li{margin:3px 0}
"""


def offer_rows(rows: list[dict], limit: int | None = None, why: bool = False) -> str:
    out = []
    for r in rows[:limit] if limit else rows:
        cur = r.get("current") or {}
        prime = ("Prime" if cur.get("prime") else "no Prime") if r["store"] == "amazon" and cur.get("prime") is not None else ""
        extra = [n for n in r.get("notes") or []]
        if why and r.get("why_not"):
            extra.insert(0, r["why_not"])
        if cur.get("offers_from"):
            extra.append(f"other offers from {money(cur['offers_from'].get('price'))} (condition not shown)")
        avail = cur.get("availability") or ""
        out.append(
            f"<tr><td><span class='st {r['store']}'>{STORE_NAMES[r['store']]}</span></td>"
            f"<td><a href='{E(r['url'])}' target='_blank' rel='noopener'>{E(r['title'][:120])}</a>"
            + "".join(f"<div class='note'>{E(n)}</div>" for n in extra[:5]) + "</td>"
            f"<td>{E(cur.get('condition') or '')}{'<br><span class=muted>' + E(prime) + '</span>' if prime else ''}</td>"
            f"<td>{E(cur.get('trust') or cur.get('seller') or '')}{'<br><span class=muted>' + E(cur['located_in']) + '</span>' if cur.get('located_in') and cur.get('located_in') != 'United States' else ''}</td>"
            f"<td class='num'>{money(cur.get('price'))}</td>"
            f"<td class='num'>{'free' if cur.get('shipping') == 0 else money(cur.get('shipping'))}</td>"
            f"<td class='num'><b>{money(r['total'])}</b><br><span class='muted'>~{money(r['landed'])}</span></td>"
            f"<td class='muted'>{E(avail.replace('_', ' '))}</td></tr>")
    return ("<table><tr><th>Store</th><th>Listing</th><th>Condition</th><th>Seller / rating</th><th class='num'>Price</th>"
            "<th class='num'>Ship</th><th class='num'>Total / landed</th><th>Availability</th></tr>" + "".join(out) + "</table>")


def auction_rows(rows: list[dict]) -> str:
    out = []
    for a in rows:
        x = PW.auction_row(a)
        w = x["watchers"] if x["watchers"] is not None else ("not shown" if x["watchers_read"] else "not read")
        out.append(f"<tr><td class='num'><b>{E(x['time_left'] or '?')}</b><br><span class='muted'>{E(x['ends'] or '')}</span></td>"
                   f"<td class='num'>{money(x['current_bid'])}</td><td class='num'>{'free' if x['shipping'] == 0 else money(x['shipping'])}</td>"
                   f"<td class='num'>{money(x['bid_plus_ship'])}</td><td class='num'>{x['bids'] if x['bids'] is not None else '?'}</td>"
                   f"<td class='num'>{E(str(w))}</td><td><a href='{E(x['url'])}' target='_blank' rel='noopener'>{E(x['title'][:100])}</a>"
                   f"<div class='muted'>{E(x['condition'] or '')} · {E(x['trust'] or '')}"
                   f"{' · ' + E(x['located_in']) if x['located_in'] and x['located_in'] != 'United States' else ''}</div></td></tr>")
    return ("<table><tr><th class='num'>Time left</th><th class='num'>Current bid</th><th class='num'>Ship</th><th class='num'>Bid + ship</th>"
            "<th class='num'>Bids</th><th class='num'>Watchers</th><th>Listing</th></tr>" + "".join(out) + "</table>")


def snapshot_for(snap: dict, w: dict, p: dict) -> list[dict]:
    """Offers from the one-time store reads (snapshot) that are this product by its title rules (require / reject)."""
    r = PW.rules_for(w, p, "ebay")
    out = []
    for it in snap.get("items", []):
        t = it.get("title") or ""
        if all(re.search(x, t, re.I) for x in r.get("require", [])) and not any(re.search(x, t, re.I) for x in r.get("reject", [])):
            out.append(it)
    return sorted(out, key=lambda i: (i.get("price") is None, i.get("price") or 0))


def snapshot_rows(items: list[dict], budget) -> str:
    out = []
    for it in items:
        price = it.get("price")
        inb = price is not None and budget and price <= budget
        out.append(f"<tr><td><span class='pill'>{E(it['store_name'])}</span></td>"
                   f"<td><a href='{E(it['url'])}' target='_blank' rel='noopener'>{E((it.get('title') or '')[:120])}</a>"
                   + "".join(f"<div class='note'>{E(n)}</div>" for n in it.get("notes") or []) + "</td>"
                   f"<td>{E(it.get('condition') or '')}</td><td class='num'>{'<b class=ok>' if inb else ''}{money(price)}{'</b>' if inb else ''}</td>"
                   f"<td class='num'>{'free' if it.get('shipping') == 0 else money(it.get('shipping'))}</td>"
                   f"<td class='muted'>{E((it.get('availability') or '').replace('_', ' '))}</td></tr>")
    return ("<table><tr><th>Store</th><th>Listing</th><th>Condition</th><th class='num'>Price</th><th class='num'>Ship</th>"
            "<th>Availability</th></tr>" + "".join(out) + "</table>")


def render(watches: list[tuple[dict, dict]], notes: dict, snap: dict | None = None) -> str:
    snap = snap or {}
    parts = [f"<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>{E(notes.get('title', 'Price report'))}</title><style>{CSS}</style></head><body><div class='wrap'>"]
    when = max((s.get("last_cycle") or "") for _, s in watches)
    parts.append(f"<h1>{E(notes.get('title', 'Price report'))}</h1><div class='sub'>Data from live searches, last read {E(when.replace('T', ' '))} · "
                 "eBay + Amazon + Best Buy · read-only, nothing was bought, bid on or added to a cart</div>")
    if notes.get("summary"):
        parts.append("<section><h2>Summary</h2>" + "".join(notes["summary"]) + "</section>")
    # overview cards
    cards = []
    for w, st in watches:
        tax = w.get("tax_rate", 0.07)
        for pname in w["products"]:
            d = product_data(st, pname, tax, w)
            best = (d["groups"].get("match") or [None])[0]
            over = (d["groups"].get("over") or [None])[0]
            budget = w["products"][pname].get("max_price")
            v = f"{money(best['total'])} <span class='st {best['store']}'>{STORE_NAMES[best['store']]}</span>" if best else "<span class='bad'>none in budget</span>"
            cheapest = f"cheapest over budget: {money(over['total'])} ({STORE_NAMES[over['store']]})" if over else ""
            auc = f"{len(d['auctions'])} eBay auction(s) under budget" if d["auctions"] else ""
            cards.append(f"<div class='card'><div class='k'>{E(pname)}</div><div class='v'>{v}</div>"
                         f"<div class='muted'>budget {money(budget)} incl. shipping · {len(d['groups'].get('match', []))} offer(s) in budget"
                         f"{'<br>' + E(cheapest) if cheapest else ''}{'<br>' + E(auc) if auc else ''}</div></div>")
    parts.append("<section><h2>At a glance - cheapest buyable offer within budget</h2><div class='grid'>" + "".join(cards) + "</div></section>")
    for s in notes.get("sections_before", []):
        parts.append(f"<section><h2>{E(s['title'])}</h2>{s['html']}</section>")
    for w, st in watches:
        tax = w.get("tax_rate", 0.07)
        for pname, p in w["products"].items():
            d = product_data(st, pname, tax, w)
            g = d["groups"]
            parts.append(f"<section><h2>{E(pname)}</h2><div class='muted'>budget {money(p.get('max_price'))} incl. shipping · searched "
                         f"{money(p.get('min'))}-{money(p.get('max'))} · {d['seen']} listings read this cycle, {d['rejected']} were not this product "
                         f"(title rules) · watch '{E(w['name'])}', cycle {st.get('cycles')}</div>")
            if notes.get("product_notes", {}).get(pname):
                parts.append(f"<div class='callout'>{notes['product_notes'][pname]}</div>")
            stats = "".join(f"<tr><td><span class='st {s}'>{STORE_NAMES[s]}</span></td><td class='num'>{v['n']}</td><td class='num'>{money(v['min'])}</td>"
                            f"<td class='num'>{money(v['median'])}</td><td class='num'>{money(v['max'])}</td></tr>" for s, v in d["stats"].items())
            if stats:
                parts.append("<h3>Prices seen (listings that are this product)</h3><table style='max-width:640px'><tr><th>Store</th>"
                             "<th class='num'>Listings</th><th class='num'>Lowest</th><th class='num'>Median</th><th class='num'>Highest</th></tr>" + stats + "</table>")
            parts.append(f"<h3 class='ok'>Within budget, buyable ({len(g.get('match', []))})</h3>"
                         + (offer_rows(g["match"], 25) if g.get("match") else "<div class='muted'>None this cycle.</div>"))
            if g.get("price_only"):
                po = sorted(g["price_only"], key=lambda r: (r.get("current") or {}).get("price") or 0)
                inb = [r for r in po if (r.get("current") or {}).get("price") is not None and (r["current"]["price"] <= (p.get("max_price") or 1e12))]
                parts.append(f"<h3>PCSP (pcserverandparts.com) - price only, shipping not shown on the listing ({len(po)}; "
                             f"{len(inb)} at or under the budget by price)</h3>" + offer_rows(po, 20))
            if g.get("over"):
                parts.append(f"<h3>Over budget - what the market asks ({len(g['over'])}, cheapest first)</h3>" + offer_rows(g["over"], 15))
            if g.get("byo"):
                parts.append(f"<details><summary>Build-your-own base prices at PCSP ({len(g['byo'])}) - chassis/CPU base, add RAM, storage, GPU</summary>"
                             + offer_rows(sorted(g["byo"], key=lambda r: (r.get("current") or {}).get("price") or 0), 15) + "</details>")
            if d["auctions"] or d["auctions_over"]:
                parts.append(f"<h3>eBay auctions - time left vs current bid vs bids vs watchers ({len(d['auctions'])} under budget)</h3>"
                             "<div class='muted'>A current bid is not a price: it rises until the end.</div>"
                             + (auction_rows(d["auctions"][:15]) if d["auctions"] else "<div class='muted'>None under budget.</div>"))
                if d["auctions_over"]:
                    parts.append(f"<details><summary>{len(d['auctions_over'])} auction(s) already over budget</summary>"
                                 + auction_rows(d["auctions_over"][:15]) + "</details>")
            if g.get("unavailable"):
                parts.append(f"<details><summary>Shown but NOT buyable ({len(g['unavailable'])})</summary>" + offer_rows(g["unavailable"], 25, why=True) + "</details>")
            sn = snapshot_for(snap, w, p) if snap else []
            if sn:
                parts.append(f"<h3>Other stores - one read on {E((snap.get('read_at') or '')[:16].replace('T', ' '))} ({len(sn)} listing(s) of this "
                             f"product; green = at or under the budget by price)</h3>" + snapshot_rows(sn[:25], p.get("max_price")))
            other = g.get("trust", []) + g.get("unknown", []) + g.get("pending", [])
            if other:
                parts.append(f"<details><summary>Not counted: seller trust, unknown shipping, or not yet checked ({len(other)})</summary>"
                             + offer_rows(other, 25, why=True) + "</details>")
            parts.append("</section>")
    if snap.get("sources"):
        rows = "".join(f"<tr><td>{E(x['store_name'])}</td><td><a href='{E(x['url'])}' target='_blank' rel='noopener'>{E(x['url'][:90])}</a></td>"
                       f"<td>{'<span class=bad>BLOCKED - ' + E(x.get('blocked_reason') or 'robot check') + '</span>' if x.get('blocked') else E(x.get('status') or 'read')}</td>"
                       f"<td class='num'>{x.get('items', 0)}</td></tr>" for x in snap["sources"])
        parts.append("<section><h2>Other stores read once for this report</h2><div class='muted'>Read-only, one page each, at a human pace. "
                     "A robot check stops that store - never bypassed.</div><table><tr><th>Store</th><th>Page</th><th>Result</th>"
                     "<th class='num'>Listings</th></tr>" + rows + "</table></section>")
    for s in notes.get("sections", []):
        parts.append(f"<section><h2>{E(s['title'])}</h2>{s['html']}</section>")
    parts.append(f"<div class='muted'>Generated {datetime.now():%Y-%m-%d %H:%M} by @modules/price_watch/price_report.py from "
                 + ", ".join(f"watch-{E(w['name'])}/state.json" for w, _ in watches) + ".</div></div></body></html>")
    return "".join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out")
    ap.add_argument("watches", nargs="+")
    ap.add_argument("--notes")
    ap.add_argument("--snapshot", help="one-time reads of other stores (JSON: read_at, sources, items)")
    a = ap.parse_args(argv)
    watches = []
    for spec in a.watches:  # path[::-Product name[::-Other]] leaves those products out of the report
        wp, *skip = spec.split("::")
        path = Path(wp) if Path(wp).exists() else MODULE_DIR / wp
        w = PW.load_watch(path)
        for name in (x[1:] for x in skip if x.startswith("-")):
            w["products"].pop(name, None)
        watches.append((w, PW.load_state(PW.state_dir(w))))
    notes = json.loads(Path(a.notes).read_text(encoding="utf-8")) if a.notes else {}
    snap = json.loads(Path(a.snapshot).read_text(encoding="utf-8")) if a.snapshot else {}
    Path(a.out).write_text(render(watches, notes, snap), encoding="utf-8")
    print(Path(a.out).resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
