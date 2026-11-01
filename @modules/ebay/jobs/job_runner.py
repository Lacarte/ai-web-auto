"""Run a shopping job (jobs/<name>.json): search -> read top listings -> rules -> builds -> report.

Phases cache their results in the run folder, so `--no-search` / `--no-read` re-use the last data
(e.g. to re-rank after the catalog was verified) without touching the browser.
"""
from __future__ import annotations

import asyncio
import html
import json
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

JOBS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(JOBS_DIR.parent))
import ebay_rules as R  # noqa: E402


# ── rules (pure) ─────────────────────────────────────────────────────────────

def _rx(p: str) -> re.Pattern:
    return re.compile(p, re.I)


def card_check(comp: dict, title: str) -> list[str]:
    """Reasons a search card is out for this component (title only)."""
    why = [f"title lacks /{p}/" for p in comp.get("require", []) if not _rx(p).search(title)]
    why += [f"title matches /{p}/" for p in comp.get("reject", []) if _rx(p).search(title)]
    return why


def spec_text(specs: dict) -> str:
    return " | ".join(f"{k}: {v}" for k, v in (specs or {}).items())


def listing_text(rec: dict) -> str:
    """Everything the seller wrote: title, item specifics (incl. Seller Notes), condition notes, description."""
    return " | ".join([rec.get("title") or "", spec_text(rec.get("specs")), rec.get("condition") or "",
                       rec.get("description_excerpt") or ""])


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def brand_mismatch(title: str, specs: dict) -> str | None:
    """Title names one maker, item specifics another (e.g. 'Deep Cool AK400' with Brand: Vetroo)."""
    brand = (specs or {}).get("Brand") or ""
    if not brand or _rx(r"unbranded|does not apply|generic|n/?a|oem").search(brand):
        return None
    if _norm(brand) in _norm(title) or any(_norm(w) in _norm(title) for w in re.split(r"[\s/,&-]+", brand) if len(w) > 3):
        return None
    return f"title vs item specifics: Brand is '{brand}'"


def listing_check(comp: dict, rec: dict) -> tuple[list[str], list[str]]:
    """(reject reasons, review notes) for a read listing: title rules, spec checks, mismatches."""
    title = rec.get("title") or ""
    specs = rec.get("specs") or {}
    st = spec_text(specs)
    reject = [f"title matches /{p}/" for p in comp.get("reject", []) if _rx(p).search(title)]
    for p in comp.get("require", []):
        if not (_rx(p).search(title) or _rx(p).search(st)):
            reject.append(f"neither title nor specs match /{p}/")
    for chk in comp.get("spec_checks", []):
        for k, v in specs.items():
            if not _rx(chk["field"]).search(k):
                continue
            if chk.get("reject_if") and _rx(chk["reject_if"]).search(v):
                reject.append(f"spec {k}: {v}")
            if chk.get("require") and not _rx(chk["require"]).search(v):
                reject.append(f"spec {k} is '{v}' - title claims otherwise")
    said = listing_text(rec)
    for d in comp.get("desc_reject", []):
        m = _rx(d["pattern"]).search(said)
        if m and not (d.get("unless") and _rx(d["unless"]).search(said)):
            a = max(0, m.start() - 40)
            reject.append(d.get("why", "listing says") + ": '" + re.sub(r"\s+", " ", said[a:m.end() + 40]).strip() + "'")
    review = []
    brand = brand_mismatch(title, specs) if len(comp.get("covers") or [1]) == 1 else None
    if brand:
        review.append(brand)
    for p, msg in comp.get("mismatch", []):
        if _rx(p).search(title) or _rx(p).search(st):
            review.append(msg)
    return reject, review


def catalog_match(catalog: dict, text: str) -> tuple[str | None, dict]:
    for name, entry in (catalog or {}).items():
        if entry.get("match") and _rx(entry["match"]).search(text):
            return name, entry
    return None, {}


def market(sold: list[dict]) -> dict | None:
    prices = sorted(i["price"] + (i.get("shipping") or 0) for i in sold if i.get("price") and not i.get("price_is_range"))
    if len(prices) < 4:
        return None
    q = statistics.quantiles(prices, n=4)
    return {"n": len(prices), "p25": round(q[0], 2), "median": round(q[1], 2), "p75": round(q[2], 2)}


def assess(job: dict, comp_key: str, rec: dict, mkt: dict | None, min_trust: int) -> dict:
    comp = job["components"][comp_key]
    reject, review = listing_check(comp, rec)
    if rec.get("excluded"):
        reject.append(rec["excluded"])
    reject += rec["trust"]["reject"]
    if rec.get("ended") or not rec.get("available", True):
        reject.append("listing ended / not available")
    if not rec.get("ships_to_zip", True):
        reject.append("does not ship to the ZIP")
    if rec.get("landed_cost") is None and not reject:
        review.append("landed cost unknown (shipping to ZIP not shown)")
    if rec.get("mismatches"):
        review += rec["mismatches"]
    oc = rec.get("option_confirm") or {}
    if oc.get("confirmed") is False:
        review.append(f"option price not confirmed in the page: {oc.get('detail')}")
    said = listing_text(rec)
    new_item = bool(re.match(r"\s*(New|Brand New|Open box|Certified Open Box|New \(Other\))", rec.get("condition") or "", re.I))
    for p, msg in comp.get("confirm_in_listing", []):
        if "test" in msg and new_item:
            continue  # new / open-box: testing evidence is not expected
        if not _rx(p).search(said):
            review.append(msg)
    if rec.get("desc_flags"):
        review.append("description mentions: " + " / ".join(rec["desc_flags"][:3]))
    sold_q = (comp.get("sold") or [""])[0]
    same_model = all(_norm(w) in _norm(rec.get("title")) for w in sold_q.split())  # sold comps are per model
    if mkt and rec.get("landed_cost") and same_model:
        base = rec["price"] + (rec.get("shipping_to_zip") or 0)
        rec["vs_market"] = round((base - mkt["median"]) / mkt["median"] * 100)
        if base < mkt["p25"] * 0.8:
            review.append(f"{-rec['vs_market']}% under the sold median - verify it is genuine")
    cat_name, cat = catalog_match(job["catalog"].get(comp.get("catalog_key") or "", {}),
                                  f"{rec.get('title')} | {spec_text(rec.get('specs'))}")
    rec["catalog"] = {"name": cat_name, **cat} if cat_name else None
    if comp.get("prefer"):
        rec["preferred_model"] = any(_rx(p).search(rec.get("title") or "") for p in comp["prefer"])
    status = "reject" if reject else ("review" if review or rec["trust"]["score"] < min_trust else "pass")
    rec.update(component=comp_key, status=status, reject_reasons=reject, review_notes=review)
    return rec


# ── builds ───────────────────────────────────────────────────────────────────

ORDER = ["gpu", "cpu", "motherboard", "ram", "psu", "ssd", "case", "cooler"]


def _cheapest(c):
    return min(c, key=lambda r: (r["landed_cost"], -r["trust"]["score"])) if c else None


def _value(c):
    """Highest trust among listings within 20% of the cheapest landed cost."""
    if not c:
        return None
    floor = min(r["landed_cost"] for r in c)
    near = [r for r in c if r["landed_cost"] <= floor * 1.2]
    return max(near, key=lambda r: (r["trust"]["score"], -r["landed_cost"]))


def compat(build: dict) -> list[dict]:
    """Cross-part checks; each row: check, result (pass/fail/unconfirmed), detail."""
    rows = []
    g, b, c, k, p = (build.get(x) or {} for x in ("gpu", "motherboard", "case", "cooler", "psu"))
    gc, bc, cc, kc = (x.get("catalog") or {} for x in (g, b, c, k))

    def row(check, ok, detail):
        rows.append({"check": check, "result": "unconfirmed" if ok is None else ("pass" if ok else "fail"), "detail": detail})

    row("CPU socket = board socket", True if b and build.get("cpu") else None, "AM4 Ryzen + AM4 board (both enforced by listing rules)")
    row("RAM: DDR4 UDIMM non-ECC 2x32GB", True if build.get("ram") else None, "enforced by title + item-specifics rules")
    src = f" ({bc.get('source')} {bc.get('page') or ''})".rstrip() if bc.get("source") else ""
    if bc.get("x8x8") is True:
        row("Second GPU slot", True, f"x8/x8 from the CPU{src}")
    elif bc.get("x8x8") is False:
        row("Second GPU slot", True, f"{bc.get('second_slot')} - fine for Ollama/llama.cpp layer split (only model "
            f"loading is slower); not for tensor-parallel (vLLM){src}")
    else:
        row("Second GPU slot", None, "board lane split not verified")
    cpu_name = ((build.get("cpu") or {}).get("catalog") or {}).get("name") or ""
    board = (b.get("title") or "")
    if "3700X" in cpu_name and re.search(r"X570|B550", board, re.I):
        row("BIOS supports the CPU", True, "Ryzen 3000 is supported by every X570/B550 BIOS")
    elif bc.get("bios_flashback"):
        row("BIOS supports the CPU", True, "USB BIOS Flashback / Q-Flash Plus updates the BIOS without a CPU"
            + (f" (Ryzen 5000 needs {bc['ryzen5000_min_bios']})" if bc.get("ryzen5000_min_bios") else ""))
    elif b:
        row("BIOS supports the CPU", None, f"Ryzen 5000 needs BIOS {bc.get('ryzen5000_min_bios') or 'from 2021+'}; "
            "board has no Flashback - ask the seller for the installed BIOS version")
    gl, gmax = (g.get("catalog") or {}).get("length_mm"), cc.get("gpu_max_mm")
    row("GPU length vs case", (gl <= gmax) if gl and gmax else None, f"card {gl or '?'} mm, case max {gmax or '?'} mm")
    kh, kmax = kc.get("height_mm"), cc.get("cooler_max_mm")
    row("Cooler height vs case", (kh <= kmax) if kh and kmax else None, f"cooler {kh or '?'} mm, case max {kmax or '?'} mm")
    row("Cooler AM4 mounting", kc.get("am4"), "per manufacturer" if kc.get("am4") is not None else "not verified")
    row("PSU 850 W for 2x RTX 3060 (~2x170 W + CPU)", True if p else None, "850 W covers two 3060s with headroom; "
        "native PCIe cables must be confirmed in the listing photos/description")
    row("Case slots for a second card", (cc.get("slots") or 0) >= 7 if cc.get("slots") else None,
        f"{cc.get('slots') or '?'} expansion slots")
    return rows


def make_builds(job: dict, recs: list[dict]) -> dict:
    ok = {k: [r for r in recs if r["component"] == k and r["status"] == "pass" and r.get("landed_cost")] for k in ORDER}
    fallback = []
    for k in ORDER:  # no clean pass: use the best 'review' listing, marked, rather than hide the part's cost
        if not ok[k]:
            rv = [r for r in recs if r["component"] == k and r["status"] == "review" and r.get("landed_cost")
                  and r["trust"]["score"] >= 55]
            if rv:
                ok[k] = rv
                fallback.append(k)
    builds = {}
    picks = {"cheapest": {k: _cheapest(ok[k]) for k in ORDER},
             "best_value": {k: _value(ok[k]) for k in ORDER}}
    dual = dict(picks["best_value"])
    x8 = [r for r in ok["motherboard"] if (r.get("catalog") or {}).get("x8x8")]
    dual["motherboard"] = _value(x8)
    pref_psu = [r for r in ok["psu"] if r.get("preferred_model")]
    dual["psu"] = _value(pref_psu) or dual["psu"]
    picks["dual_gpu_ready"] = dual
    for name, parts in picks.items():
        missing = [k for k in ORDER if not parts.get(k)]
        total = round(sum(p["landed_cost"] for p in parts.values() if p), 2)
        builds[name] = {"parts": {k: v["item_id"] if v else None for k, v in parts.items()}, "total_landed": total,
                        "complete": not missing, "missing": missing,
                        "budget_gap": round(total - job["budget"], 2) if not missing else None,
                        "review_parts": [k for k in fallback if parts.get(k)],
                        "compatibility": compat(parts)}
    return builds


# ── report ───────────────────────────────────────────────────────────────────

def _money(v):
    return f"${v:,.2f}" if isinstance(v, (int, float)) else "—"


def report_md(job, recs, builds, markets, failures, out) -> str:
    by_id = {r["item_id"]: r for r in recs}
    L = [f"# {job['title']}", "", f"Generated {datetime.now():%Y-%m-%d %H:%M} · ship to {job['ship_to_zip']} · budget {_money(job['budget'])} · "
         "landed = price + shipping + 7% FL tax (estimate). Read-only research: nothing was bought, bid on or added to cart.", ""]
    L += ["## Builds", "", "| Build | Total landed | vs budget | Complete |", "|---|---|---|---|"]
    for n, b in builds.items():
        gap = b["budget_gap"]
        L.append(f"| {n.replace('_', ' ')} | {_money(b['total_landed'])} | "
                 f"{'—' if gap is None else ('+' if gap > 0 else '') + _money(gap).replace('$-', '-$')} | "
                 f"{'yes' if b['complete'] else 'NO - missing ' + ', '.join(b['missing'])} |")
    for n, b in builds.items():
        L += ["", f"### {n.replace('_', ' ').title()} — {_money(b['total_landed'])}", "",
              "| Part | Listing | Landed | Seller | Trust | Evidence time |", "|---|---|---|---|---|---|"]
        for k in ORDER:
            r = by_id.get(b["parts"].get(k) or "")
            if not r:
                L.append(f"| {k} | **none verified** - see Search failures | | | | |")
                continue
            flag = " ⚠ review: " + "; ".join(r["review_notes"])[:120] if r["status"] == "review" else ""
            L.append(f"| {k} | [{r['title'][:70]}]({r['url']}) `{r['item_id']}`{flag} | {_money(r['landed_cost'])} | "
                     f"{(r.get('seller_text') or '').split('(')[0].strip()[:28]} {r.get('feedback_pct')}% ({r.get('feedback_count')}) | "
                     f"{r['trust']['score']} | {r['evidence']['fetched_at']} |")
        L += ["", "| Compatibility check | Result | Detail |", "|---|---|---|"]
        L += [f"| {c['check']} | {c['result']} | {c['detail']} |" for c in b["compatibility"]]
    L += ["", "## Sold-price baseline (last sold listings, price + shipping)", "", "| Part | n | 25% | median | 75% |", "|---|---|---|---|---|"]
    L += [f"| {k} | {m['n']} | {_money(m['p25'])} | {_money(m['median'])} | {_money(m['p75'])} |" for k, m in markets.items() if m]
    L += ["", "## Every listing read", ""]
    for k in ORDER:
        rows = sorted([r for r in recs if r["component"] == k], key=lambda r: (r["status"] != "pass", r.get("landed_cost") or 9e9))
        if not rows:
            continue
        L += [f"### {job['components'][k]['label']}", "", "| Status | Listing | Price | Ship | Landed | Cond. | Returns | Seller | Trust | Notes |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            notes = "; ".join(r["reject_reasons"] + r["review_notes"] + r["trust"]["why"])[:260].replace("|", "/")
            L.append(f"| {r['status']} | [{r['title'][:55]}]({r['url']}) | {_money(r['price'])} | {_money(r.get('shipping_to_zip'))} | "
                     f"{_money(r['landed_cost'])} | {r['condition'][:18]} | {(r.get('returns_text') or '')[:40].replace('|', '/')} | "
                     f"{r.get('feedback_pct')}% ({r.get('feedback_count')}) | {r['trust']['score']} | {notes} |")
        L.append("")
    if failures:
        L += ["## Search failures", ""] + [f"- **{k}**: {v['reason']} — try: {', '.join(v['links'])}" for k, v in failures.items()]
    L += ["", "## Ask the seller before buying", ""]
    for b in builds.values():
        for k, iid in b["parts"].items():
            r = by_id.get(iid or "")
            if r and r["review_notes"]:
                L.append(f"- `{iid}` ({k}): " + "; ".join(r["review_notes"]))
    L += ["", "Evidence per listing (page JSON + screenshot + sha256): `evidence/`."]
    text = "\n".join(dict.fromkeys(L) if False else L)
    (out / "report.md").write_text(text, encoding="utf-8")
    return text


def report_html(job, recs, builds, markets, out) -> Path:
    by_id = {r["item_id"]: r for r in recs}
    e = html.escape
    cards = []
    for n, b in builds.items():
        rows = "".join(
            f"<tr><td>{k}</td><td><a href='{e(r['url'])}' target='_blank'>{e(r['title'][:80])}</a><div class=m>#{r['item_id']} · "
            f"{e(r['condition'])} · {e((r.get('returns_text') or '')[:50])} · read {e(r['evidence']['fetched_at'])}</div></td>"
            f"<td class=n>{_money(r['landed_cost'])}</td><td class=n>{r['trust']['score']}</td></tr>"
            if (r := by_id.get(b["parts"].get(k) or "")) else f"<tr><td>{k}</td><td class=bad colspan=3>no verified listing</td></tr>"
            for k in ORDER)
        comp = "".join(f"<li class='{c['result']}'><b>{e(c['check'])}</b>: {e(c['result'])} — {e(c['detail'])}</li>" for c in b["compatibility"])
        gap = b["budget_gap"]
        cards.append(f"<section><h2>{e(n.replace('_', ' ').title())}</h2><p class=tot>{_money(b['total_landed'])}"
                     f"<span class='{'bad' if (gap or 0) > 0 else 'ok'}'> {('over' if (gap or 0) > 0 else 'under')} budget by "
                     f"{_money(abs(gap)) if gap is not None else '— (incomplete)'}</span></p><table>{rows}</table><ul class=cmp>{comp}</ul></section>")
    doc = f"""<!DOCTYPE html><html lang=en><meta charset=utf-8><title>{e(job['title'])}</title>
<style>body{{font:14px/1.45 system-ui,sans-serif;margin:24px;background:#f6f7f9;color:#1b1f24}}h1{{font-size:20px}}
section{{background:#fff;border:1px solid #dde1e6;border-radius:10px;padding:14px 18px;margin:14px 0}}h2{{margin:0 0 4px;font-size:17px}}
.tot{{font-size:22px;font-weight:600;margin:4px 0 10px}}.tot span{{font-size:14px;font-weight:400}}table{{border-collapse:collapse;width:100%}}
td{{border-top:1px solid #eef0f3;padding:6px 8px;vertical-align:top}}.n{{text-align:right;white-space:nowrap}}.m{{color:#667;font-size:12px}}
.bad{{color:#b3261e}}.ok{{color:#1e7b34}}ul.cmp{{columns:2;font-size:13px;padding-left:18px}}li.fail{{color:#b3261e}}li.unconfirmed{{color:#8a6100}}
a{{color:#0b57d0;text-decoration:none}}</style>
<h1>{e(job['title'])}</h1><p>Ship to {e(job['ship_to_zip'])} · budget {_money(job['budget'])} · landed = price + shipping + 7% FL tax (estimate) ·
generated {datetime.now():%Y-%m-%d %H:%M}. Read-only research — every purchase is yours to approve.</p>{''.join(cards)}
<p class=m>Full table of every listing read: report.md · structured data: results.json · evidence/: page JSON + screenshots.</p></html>"""
    p = out / "dashboard.html"
    p.write_text(doc, encoding="utf-8")
    return p


# ── orchestration ────────────────────────────────────────────────────────────

def run(args, MODULE, with_session, search_products, read_listing, run_dir, write) -> int:
    job_path = Path(args.job)
    if not job_path.exists():
        job_path = JOBS_DIR / Path(args.job).name
    job = json.loads(job_path.read_text(encoding="utf-8"))
    only = set(args.only.split(",")) if args.only else set(job["components"])
    out = run_dir(job["name"])
    read_top = args.read_top or job.get("read_top", 4)
    min_trust = MODULE.settings.get("min_trust", 55)
    cache_s, cache_l = out / "searches.json", out / "listings_raw.json"
    searches = json.loads(cache_s.read_text(encoding="utf-8")) if cache_s.exists() else {}
    listings = json.loads(cache_l.read_text(encoding="utf-8")) if cache_l.exists() else {}

    read_listing.__globals__["set_variation_require"](job.get("variation_require"))

    async def browse(sess):
        if not args.no_search:
            for k, comp in job["components"].items():
                if k not in only:
                    continue
                res = {"bin": [], "sold": None}
                for q, lo, hi in comp["searches"]:
                    res["bin"].append(await search_products(sess, q, lo, hi))
                if comp.get("sold"):
                    q, lo, hi = comp["sold"]
                    res["sold"] = await search_products(sess, q, lo, hi, sold=True, max_pages=1)
                searches[k] = res
                write(cache_s, searches)
        if not args.no_read:
            for k in [k for k in job["components"] if k in only and k in searches]:
                for iid in pick_to_read(job, k, searches[k], read_top):
                    if iid in listings:
                        continue
                    try:
                        rec = {"component": k, **await read_listing(sess, iid)}
                        if (rec.get("chosen_option") or {}).get("labeled"):  # multi-option: walk the menus to confirm
                            rec["option_confirm"] = await read_listing.__globals__["confirm_option"](sess, rec)
                        listings[iid] = rec
                    except Exception as ex:  # one bad listing must not stop the run
                        if type(ex).__name__ == "Blocked":
                            raise
                        print(f"  ! {iid}: {ex}")
                    write(cache_l, listings)

    if not (args.no_search and args.no_read):
        asyncio.run(with_session("search_products", browse, out))
    if getattr(args, "scan_variations", False):
        g = read_listing.__globals__
        PANEL = g["MODULE"].css("msku_panel")

        async def scan(sess):
            ids = [i for i, v in listings.items() if v.get("component") in job["components"]]
            for n, i in enumerate(ids, 1):
                v = listings[i]
                if v.get("variation_scan"):
                    continue
                try:
                    await sess.feedback(f"Option check {n}/{len(ids)}: {i}")
                    await sess.goto(v["url"], ["item_title"])
                    has = await sess.js("JSON.stringify(!!document.querySelector(" + json.dumps(PANEL) + "))")
                    if has:
                        rec = {"component": v["component"], **await read_listing(sess, i)}
                        if (rec.get("chosen_option") or {}).get("labeled"):
                            rec["option_confirm"] = await g["confirm_option"](sess, rec)
                        rec["variation_scan"] = {"has_menus": True, "checked_at": g["now"]()}
                        listings[i] = rec
                        oc = rec.get("option_confirm") or {}
                        print(f"  {i}: MENUS -> price {rec.get('price')} option {(rec.get('chosen_option') or {}).get('options')} "
                              f"confirmed={oc.get('confirmed')}", flush=True)
                    else:
                        v["variation_scan"] = {"has_menus": False, "checked_at": g["now"]()}
                        print(f"  {i}: single option", flush=True)
                except Exception as ex:
                    if type(ex).__name__ == "Blocked":
                        raise
                    print(f"  ! {i}: {ex}", flush=True)
                write(cache_l, listings)
        asyncio.run(with_session("read_listing", scan, out))
    if getattr(args, "refresh_selected", False) and (out / "results.json").exists():
        # Freshness pass: re-read every listing of the selected builds now, and confirm multi-option prices in the page.
        prev = json.loads((out / "results.json").read_text(encoding="utf-8"))
        ids = sorted({i for b in prev.get("selected", {}).values() for i in b["parts"].values()})
        comp_of = {i: listings[i]["component"] for i in ids if i in listings}
        confirm = read_listing.__globals__["confirm_option"]

        async def refresh(sess):
            for i in ids:
                try:
                    rec = {"component": comp_of.get(i), **await read_listing(sess, i)}
                    if (rec.get("chosen_option") or {}).get("labeled"):
                        rec["option_confirm"] = await confirm(sess, rec)
                    rec["refreshed"] = True
                    listings[i] = rec
                except Exception as ex:
                    if type(ex).__name__ == "Blocked":
                        raise
                    print(f"  ! {i}: {ex}")
                write(cache_l, listings)
        asyncio.run(with_session("read_listing", refresh, out))
    return finish(job, searches, listings, min_trust, out, write)


def pick_to_read(job: dict, k: str, res: dict, read_top: int) -> list[str]:
    comp = job["components"][k]
    seen, cands = set(), []
    for s in res["bin"]:
        for it in s["items"]:
            if it["id"] in seen or it.get("auction") or it.get("price") is None or (
                    it.get("price_is_range") and not comp.get("allow_price_range")):
                continue
            seen.add(it["id"])
            if card_check(comp, it["title"]) or R.exclusion_reason(it["title"], it.get("condition")):
                continue
            if it.get("shipping") is None:  # local pickup only, or shipping to the ZIP not shown
                continue
            if it.get("feedback_count") is not None and (it["feedback_count"] < 25 or (it.get("feedback_pct") or 0) < 97):
                continue
            cands.append(it)
    first = _rx(comp["read_first"]) if comp.get("read_first") else None  # e.g. PSU titles that mention cables
    cands.sort(key=lambda i: (bool(first) and not first.search(i["title"]), i["price"] + (i.get("shipping") or 0)))
    if comp.get("per_catalog_model"):  # the cheapest of each board model, so every lane layout gets a candidate
        per_model = {}
        for it in cands:
            name, _ = catalog_match(job["catalog"]["boards"], it["title"])
            if name and name not in per_model:
                per_model[name] = it["id"]
        return list(per_model.values())
    read_top = comp.get("read_top", read_top)
    picked = []
    for pat in comp.get("diversify", []):  # cheapest of each model first, so every option gets read
        hit = next((i for i in cands if _rx(pat).search(i["title"]) and i["id"] not in picked), None)
        if hit:
            picked.append(hit["id"])
    picked += [i["id"] for i in cands if i["id"] not in picked]
    return picked[:max(read_top, len(comp.get("diversify", [])))]


def sold_market(job, k, res):
    """Sold comps filtered by the component's own title rules (a broad query also returns bridges, PCs, laptops)."""
    if not res.get("sold") or k not in job["components"]:
        return None
    comp = job["components"][k]
    return market([i for i in res["sold"]["items"] if not card_check(comp, i["title"])
                   and not R.exclusion_reason(i["title"], i.get("condition"))])


def finish(job, searches, listings, min_trust, out, write) -> int:
    markets = {k: sold_market(job, k, v) for k, v in searches.items()}
    recs = [assess(job, v["component"], dict(v), markets.get(v["component"]), min_trust)
            for v in listings.values() if v["component"] in job["components"]]
    v2 = any(c.get("platform") for c in job["components"].values())
    failures = {}
    for k in (list(job["components"]) if v2 else ORDER):
        if not any(r["component"] == k and r["status"] == "pass" for r in recs):
            comp = job["components"][k]
            from urllib.parse import quote_plus
            links = [f"https://www.ebay.com/sch/i.html?_nkw={quote_plus(q)}&LH_BIN=1&LH_PrefLoc=1&_stpos={job['ship_to_zip']}"
                     for q, _, _ in comp["searches"][:2]]
            n_read = sum(r["component"] == k for r in recs)
            failures[k] = {"reason": f"{n_read} listing(s) read, none passed every rule" if n_read else "no candidate passed the search-card rules",
                           "links": links}
    if v2:
        import report_v2
        res = report_v2.finish_v2(job, recs, markets, out, write, failures)
        for n, b in res["selected"].items():
            print(f"{n:<20} {_money(b['total']):>11}  {b['decision']:<14} {b['gpu_family']:<22} dual={b['dual_ready']}")
        for f in ("report.md", "dashboard.html", "results.json"):
            print(f"-> {out / f}")
        return 0
    builds = make_builds(job, recs)
    write(out / "results.json", {"job": job["name"], "generated_at": datetime.now().isoformat(timespec="seconds"),
                                 "budget": job["budget"], "builds": builds, "markets": markets, "failures": failures,
                                 "listings": recs})
    report_md(job, recs, builds, markets, failures, out)
    report_html(job, recs, builds, markets, out)
    for n, b in builds.items():
        print(f"{n:<16} {_money(b['total_landed']):>11}  complete={b['complete']}  gap={b['budget_gap']}  missing={b['missing']}")
    print(f"-> {out / 'report.md'}\n-> {out / 'dashboard.html'}\n-> {out / 'results.json'}")
    return 0
