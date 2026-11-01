"""Price selectable eBay listings with the REQUIRED options selected on the listing page (read-only).

    python config_check.py watches/build-deals.json [--port auto] [--max 4] [--items ID,ID --product NAME]

A selectable listing's search card shows only its cheapest option ("$269.99 to $899.99"), which is never the price of
the configuration the user needs. For every product of the watch that has a "configure" block, this:
  1. takes this cycle's eBay offers of that product whose card shows a price range (cheapest first, --max per product),
     or the --items given;
  2. reads the listing page and picks, from the page's own option data, the cheapest in-stock combination with
     RAM >= configure.ram_gb_min, a power-supply option >= configure.psu_min_w when the listing has a PSU menu, and
     (second choice, priced too) a >= 1TB NVMe drive;
  3. SELECTS those options menu by menu on the page (trusted clicks) and records the price the page then shows;
  4. writes @downloads/watch-<name>/configured.json and rebuilds the dashboard. Only rows whose page price matched
     the selected combination count ("confirmed"); the dashboard ignores the rest.
Nothing is bought, carted or bid on. The eBay tab must be visible (Chrome drops input in a hidden tab): the script
waits, polling, until it is."""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
ROOT = MODULE_DIR.parents[1]
for p in (ROOT, ROOT / "@modules", ROOT / "@modules" / "ebay", MODULE_DIR):
    sys.path.insert(0, str(p))
import price_watch as PW  # noqa: E402

MENU = {"ram": r"\bRAM\b|memory|DDR", "storage": r"storage|\bSSD\b|drive|\bHDD\b|nvme|hard|disk", "cpu": r"\bCPU\b|processor",
        "gpu": r"\bGPU\b|graphic|video", "psu": r"power|\bPSU\b|supply", "os": r"\bOS\b|operating|windows"}


# ── option choice (pure) ─────────────────────────────────────────────────────

def menu_kind(label: str) -> str:
    for k, rx in MENU.items():
        if re.search(rx, label or "", re.I):
            return k
    return "other"


def gb_of(v: str) -> float | None:
    """'64GB (4x 16GB) DDR4' -> 64, '4x16GB' -> 64, '1TB NVMe' -> 1000."""
    m = re.search(r"(\d+)\s?x\s?(\d+)\s?GB", v or "", re.I)
    if m and not re.match(r"\s*\d+\s?(GB|TB)", v or "", re.I):
        return float(int(m.group(1)) * int(m.group(2)))
    m = re.search(r"(\d+(?:\.\d+)?)\s?(TB|GB)", v or "", re.I)
    if not m:
        return None
    return float(m.group(1)) * (1000 if m.group(2).upper() == "TB" else 1)


def nvme_tb(v: str) -> bool:
    """A storage option that is an NVMe / M.2 drive of 1TB or more (a SATA SSD or a hard disk does not count)."""
    v = v or ""
    if re.search(r"\bnone\b|no (drive|storage|ssd)", v, re.I):
        return False
    for part in re.split(r"\+|,|&| and ", v):
        size = gb_of(part)
        if size and size >= 1000 and re.search(r"nvme|m\.2|pcie", part, re.I) and not re.search(r"\bhdd\b|7\.2k|sata", part, re.I):
            return True
    return False


def watts(v: str) -> int | None:
    m = re.search(r"(\d{3,4})\s?W", v or "", re.I)
    return int(m.group(1)) if m else None


RAM_IN_TEXT = re.compile(r"\b(\d{1,3})\s?GB\s*(?:\(\s*\d+\s*x\s*\d+\s*GB\s*\)\s*)?(?:RAM|DDR\d?|memory|ECC|RDIMM|LRDIMM)\b", re.I)
# a variation RAM regex for read_listing: a size followed by SSD/NVMe/... is storage, not RAM ("128GB SSD")
RAM_REQUIRE = r"\b(64|96|128|192|256)\s?GB\b(?!\s*(?:\w+\s+)?(?:SSD|HDD|NVMe|M\.2|SATA|SAS|storage|drive|disk|flash))"


def menu_values(lab: dict[str, str]) -> dict[str, list[str]]:
    """Values per kind (ram / storage / psu / ...). A listing with ONE combined menu ("Specification: Intel W-2123
    64GB RAM No Drive/OS") has no RAM-labelled menu: its value is read for RAM, storage and PSU by content."""
    kinds: dict[str, list[str]] = {}
    for m, val in lab.items():
        kinds.setdefault(menu_kind(m), []).append(val)
    if "ram" not in kinds and len(lab) == 1:
        val = next(iter(lab.values()))
        ram = RAM_IN_TEXT.search(val)
        kinds = {"ram": [ram.group(0)] if ram else ["no RAM size"], "storage": [val]}
        if watts(val):
            kinds["psu"] = [val]
    return kinds


def choose(variations: list[dict], ram_gb_min: int = 64, psu_min_w: int = 0) -> dict:
    """From a listing's embedded option data (options_seen_full: {price, out_of_stock, variation_id, labeled:
    [{menu, value}]}) pick the cheapest in-stock combination with RAM >= ram_gb_min and a PSU option >= psu_min_w
    (when there is a PSU menu): 'any_storage', and the cheapest that also has a >= 1TB NVMe drive: 'with_nvme'.
    A listing whose PSU options are all below psu_min_w is priced with the best it has and marked psu_short."""
    vs = [v for v in variations or [] if v.get("price") and not v.get("out_of_stock") and v.get("labeled")]

    def pick(need_nvme: bool, psu_min: int):
        ok = []
        for v in vs:
            lab = {o["menu"]: o["value"] for o in v["labeled"]}
            kinds = menu_values(lab)
            if "ram" not in kinds or max((gb_of(x) or 0) for x in kinds["ram"]) < ram_gb_min:
                continue
            if "psu" in kinds and max((watts(x) or 0) for x in kinds["psu"]) < psu_min:
                continue
            if need_nvme and not any(nvme_tb(x) for x in kinds.get("storage", [])):
                continue
            ok.append({"embedded_price": float(v["price"]), "targets": lab, "variation_id": v.get("variation_id")})
        return min(ok, key=lambda x: x["embedded_price"]) if ok else None

    out = {"any_storage": pick(False, psu_min_w), "with_nvme": pick(True, psu_min_w), "psu_short": None}
    if psu_min_w and not out["any_storage"]:
        psu_vals = sorted({o["value"] for v in vs for o in v["labeled"] if menu_kind(o["menu"]) == "psu"})
        if psu_vals:  # a PSU menu without the wattage needed: price the best it offers, marked
            out = {"any_storage": pick(False, 0), "with_nvme": pick(True, 0),
                   "psu_short": f"no power-supply option of {psu_min_w}W or more in stock (offered: {', '.join(psu_vals)})"}
    out["menus"] = {m: menu_kind(m) for m in sorted({o["menu"] for v in vs for o in v["labeled"]})}
    out["ram_values"] = sorted({r for v in vs for r in menu_values({o["menu"]: o["value"] for o in v["labeled"]}).get("ram", [])})
    return out


def candidates(w: dict, state: dict, max_per_product: int = 4) -> list[dict]:
    """This cycle's eBay offers of the 'configure' products whose search card shows a price range, cheapest first.
    Title rules apart from the RAM size apply (a selectable title often doesn't name the RAM)."""
    out = []
    for pname, p in w["products"].items():
        cfg = p.get("configure")
        if not cfg or p.get("enabled") is False:
            continue
        r = PW.rules_for(w, p, "ebay")
        model = re.compile(cfg["model"], re.I)
        rows = []
        for rec in state.get("items", {}).values():
            cur = rec.get("current") or {}
            if (rec["product"] != pname or rec["store"] != "ebay" or rec.get("seen_cycle") != state.get("cycles")
                    or not cur.get("price_is_range") or cur.get("price") is None or not model.search(rec.get("title") or "")
                    or any(re.search(x, rec.get("title") or "", re.I) for x in r.get("reject", []))):
                continue
            if (cur.get("feedback_count") or 0) < r.get("min_feedback", 25) or (cur.get("feedback_pct") or 0) < r.get("min_feedback_pct", 97):
                continue
            rows.append({"product": pname, "id": rec["id"], "url": rec["url"], "title": rec["title"],
                         "from_price": cur["price"], "shipping": cur.get("shipping")})
        rows.sort(key=lambda x: x["from_price"] + (x["shipping"] or 0))
        out += rows[:max_per_product]
    return out


def merge(old: list[dict], new: list[dict], keep_days: float = 3) -> list[dict]:
    """configured.json rows: the newest read per listing; reads older than keep_days are dropped."""
    by = {x["url"]: x for x in old if PW._age_hours(x.get("checked_at")) < keep_days * 24}
    for x in new:
        by[x["url"]] = x
    return sorted(by.values(), key=lambda x: (x.get("product") or "", x.get("page_price") or 9e9))


# ── browser ──────────────────────────────────────────────────────────────────

VISIBLE_JS = "JSON.stringify(document.visibilityState)"  # module_kit._run_script json-parses string results


async def wait_visible(s, module_kit, max_min: float = 45) -> None:
    await module_kit.bring_to_front(s)
    told = False
    for _ in range(int(max_min * 12)):
        try:
            if await module_kit._run_script(s, VISIBLE_JS) == "visible":
                return
        except Exception:
            pass
        if not told:
            print("  WAITING: the eBay tab is hidden - bring the Chrome window with the eBay tab to the front", flush=True)
            told = True
        await asyncio.sleep(5)
        await module_kit.bring_to_front(s)
    raise RuntimeError(f"eBay tab stayed hidden for {max_min} min")


async def price_one(sess, s, M, module_kit, cand: dict, cfg: dict) -> dict:
    await wait_visible(s, module_kit)
    M.set_variation_require(RAM_REQUIRE)  # the reader's own pick only; the price used is the one the page shows after the walk
    rec = await M.read_listing(sess, cand["id"])
    base = {"product": cand["product"], "item_id": cand["id"], "url": rec["url"], "title": rec["title"], "from_price": cand["from_price"],
            "shipping": rec.get("shipping_to_zip"), "condition": rec.get("condition"), "available": rec.get("available"),
            "returns": re.sub(r"\s+", " ", rec.get("returns_text") or "")[:120], "seller": re.sub(r"\s+", " ", rec.get("seller_text") or "")[:80],
            "checked_at": datetime.now().isoformat(timespec="seconds")}
    ch = choose(rec.get("options_seen_full") or [], cfg.get("ram_gb_min", 64), cfg.get("psu_min_w", 0))
    base.update(menus=ch["menus"], ram_values=ch["ram_values"], notes=[ch["psu_short"]] if ch["psu_short"] else [])
    if not ch["any_storage"]:
        return {**base, "confirmed": False, "page_price": None,
                "why": f"no in-stock option with {cfg.get('ram_gb_min', 64)}GB RAM (RAM options: {', '.join(ch['ram_values']) or 'none read'})"}
    walks = {}
    for name in ("any_storage", "with_nvme"):
        c = ch[name]
        if not c or (name == "with_nvme" and c["targets"] == ch["any_storage"]["targets"]):
            continue
        await module_kit.human_pause({"pause_seconds": [2.0, 5.0]})
        await wait_visible(s, module_kit)
        wk = await M.walk_options(sess, {"url": rec["url"], "item_id": rec["item_id"]}, c["targets"])
        ok = bool(wk.get("ok") and wk.get("final_price") is not None and abs(wk["final_price"] - c["embedded_price"]) < 0.01)
        walks[name] = {"options": list(c["targets"].values()), "targets": c["targets"], "embedded_price": c["embedded_price"],
                       "page_price": wk.get("final_price"), "confirmed": ok, "page_url": wk.get("url"),
                       "detail": wk.get("detail") or ("page price matches the selected option" if ok else
                                                      f"page shows {wk.get('final_price')} vs option data {c['embedded_price']}")}
        print(f"    {name}: {' / '.join(c['targets'].values())} -> page ${wk.get('final_price')} "
              f"({'confirmed' if ok else 'NOT confirmed'})", flush=True)
    a = walks.get("any_storage") or {}
    nv = walks.get("with_nvme") or (a if ch["with_nvme"] and ch["with_nvme"]["targets"] == ch["any_storage"]["targets"] else None)
    notes = base["notes"] + ([f"with a 1TB NVMe option selected: ${nv['page_price']}" + ("" if nv.get("confirmed") else " (not confirmed)")]
                             if nv and nv is not a else ["includes a 1TB NVMe drive"] if nv is a else ["no 1TB NVMe option: add a drive"])
    return {**base, "confirmed": bool(a.get("confirmed")), "page_price": a.get("page_price"), "options": a.get("options"),
            "embedded_price": a.get("embedded_price"), "detail": a.get("detail"), "page_url": a.get("page_url"),
            "with_nvme": nv, "notes": notes}


async def main_async(a) -> int:
    import warnings
    warnings.filterwarnings("ignore")
    import module_kit
    import ebay_search as M
    from ai_web_auto_backend.core.server import WebSocketServer
    w = PW.load_watch(Path(a.watch) if Path(a.watch).exists() else MODULE_DIR / a.watch)
    d = PW.state_dir(w)
    state = PW.load_state(d)
    if a.items:
        if not a.product or a.product not in w["products"]:
            raise SystemExit("--items needs --product with one of: " + ", ".join(w["products"]))
        cands = [{"product": a.product, "id": i, "url": f"https://www.ebay.com/itm/{i}", "title": "", "from_price": None,
                  "shipping": None} for i in a.items.split(",")]
    else:
        cands = candidates(w, state, a.max)
    print(f"{len(cands)} selectable listing(s) to price", flush=True)
    if not cands:
        return 0
    port = PW.pick_port(a.port)
    s = WebSocketServer(port=port)
    await s.start()
    print(f"Waiting for the AI Web-Auto extension on ws://localhost:{port} ...", flush=True)
    await s.wait_for_connection(timeout=120)
    rows = []
    try:
        await module_kit._command(s, "navigate", {"url": M.MODULE.manifest["app_url"], "new_tab": True, "dismiss_banners": False}, timeout_ms=25000)
        pf = await module_kit.preflight(M.MODULE, s, "search_products")
        if not pf.ok:
            raise SystemExit(f"eBay preflight failed: {pf.problem}")
        sess = M.EbaySession(s, d / "ebay")
        (sess.out / "evidence").mkdir(parents=True, exist_ok=True)
        for c in cands:
            await module_kit.human_pause({"pause_seconds": [3.0, 7.0]})
            print(f"  {c['product']}: {c['id']} (search card from ${c['from_price']})", flush=True)
            try:
                rows.append(await price_one(sess, s, M, module_kit, c, w["products"][c["product"]].get("configure") or {}))
            except Exception as ex:
                rows.append({**c, "confirmed": False, "page_price": None, "why": f"error: {str(ex)[:160]}",
                             "checked_at": datetime.now().isoformat(timespec="seconds")})
            f = d / "configured.json"
            old = json.loads(f.read_text(encoding="utf-8")).get("items", []) if f.exists() else []
            f.write_text(json.dumps({"at": PW.now(), "items": merge(old, rows)}, indent=1, ensure_ascii=False), encoding="utf-8")
        M.set_variation_require(None)
    finally:
        try:
            await module_kit._command(s, "close_tab", timeout_ms=5000)
        except Exception:
            pass
        await s.stop()
    if state.get("cycles"):
        comps = [PW.compare(state, p, state["cycles"], w.get("tax_rate", 0.07)) for p in w["products"] if w["products"][p].get("enabled", True)]
        print("dashboard:", PW.report(w, state, d, comps), flush=True)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("watch")
    ap.add_argument("--port", default="auto")
    ap.add_argument("--max", type=int, default=4, help="selectable listings priced per product (cheapest first)")
    ap.add_argument("--items", help="comma-separated eBay item ids to price instead of this cycle's candidates")
    ap.add_argument("--product", help="the watch product the --items belong to")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    return asyncio.run(main_async(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
