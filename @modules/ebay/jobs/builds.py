"""Compose complete builds from listings that each cover one or more parts (single parts, CPU+board+RAM
bundles, complete workstations), validate them per stage, and pick the comparison builds.

Every check is tri-state: PASS (evidence), FAIL (evidence of a problem), UNVERIFIED (no evidence). An
unverified check never counts as a pass. Stage "now" = 1 GPU + 1 NVMe + 64GB; stage "upgrade" = 2 GPUs.
"""
from __future__ import annotations

import itertools
import math
import re

PARTS = ["gpu", "cpu", "motherboard", "ram", "psu", "ssd", "case", "cooler"]
PASS, FAIL, UNVERIFIED = "PASS", "FAIL", "UNVERIFIED"
SLOT_MM = 20.32  # one expansion-slot pitch


def _rx(p):
    return re.compile(p, re.I)


def covers(job: dict, rec: dict) -> list[str]:
    return job["components"][rec["component"]].get("covers", [rec["component"]])


def usable(recs: list[dict], comp: str, min_trust: int, top: int) -> list[dict]:
    """Listings of one component that may enter a build: pass first; review (trust >= min) only if no pass."""
    ok = [r for r in recs if r["component"] == comp and r["status"] == "pass" and r.get("landed_cost")]
    rv = [dict(r, _review=True) for r in recs if r["component"] == comp and r["status"] == "review"
          and r.get("landed_cost") and r["trust"]["score"] >= min_trust]
    ok = sorted(ok, key=lambda r: (r["landed_cost"], -r["trust"]["score"]))[:top]
    rv = sorted(rv, key=lambda r: (r["landed_cost"], -r["trust"]["score"]))[:top]
    return ok + rv  # review listings compete but make the build INVESTIGATE


# ── facts per build ──────────────────────────────────────────────────────────

def gpu_family(job, gpu_rec) -> dict:
    return dict(job["catalog"].get("gpu_families", {}).get(gpu_rec["component"], {}))


def platform_of(job, parts) -> tuple[str, dict]:
    key = next((job["components"][k].get("platform") for k in parts if job["components"][k].get("platform")), "am4")
    if parts.get("workstation"):
        key = (parts["workstation"].get("catalog") or {}).get("platform") or key
    return key, dict(job["catalog"].get("platforms", {}).get(key, {}))


def psu_watts(parts) -> int | None:
    src = parts.get("workstation") or parts.get("psu")
    if not src:
        return None
    W = r"\b(4[0-9]{2}|[5-9][0-9]{2}|1[0-6][0-9]{2})\s?W(?:att)?s?\b"
    text = f"{src.get('title')} | " + " | ".join(f"{k}: {v}" for k, v in (src.get("specs") or {}).items())
    m = re.search(W, text, re.I)
    if m:
        return int(m.group(1))
    if parts.get("workstation"):  # workstation listings often state the PSU only in the description
        m = re.search(W + r"[^.\n]{0,30}(?:PSU|power\s*supply)|(?:PSU|power\s*supply)[^.\n]{0,30}" + W,
                      src.get("description_excerpt") or "", re.I)
        if m:
            return int(next(g for g in m.groups() if g))
        default = (parts["workstation"].get("catalog") or {}).get("default_psu")
        return int(default) if default else None
    return None


def board_of(parts) -> dict:
    for k in ("motherboard", "bundle_am4", "bundle_am5", "cpumb_am5"):
        if parts.get(k):
            return parts[k].get("catalog") or {}
    return {}


def combo_of(parts):
    """The listing that sells CPU and board together (bundle or combo), if any."""
    return next((v for k, v in parts.items() if k.startswith(("bundle_", "cpumb_"))), None)


def gpu_dims(gpu: dict) -> dict:
    """Length / thickness / power connector stated in the listing itself (title, specifics, description)."""
    text = f"{gpu.get('title')} | " + " | ".join(f"{k}: {v}" for k, v in (gpu.get("specs") or {}).items()) + " | " + (gpu.get("description_excerpt") or "")
    out = {}
    m = re.search(r"\b(\d{3}(?:\.\d)?)\s*[x×*]\s*(\d{2,3}(?:\.\d)?)\s*[x×*]\s*(\d{2}(?:\.\d)?)\s*mm", text, re.I)
    if m:
        out.update(length_mm=float(m.group(1)), thickness_mm=float(m.group(3)))
    m = re.search(r"(?:Max\s+GPU\s+Length|Card\s+Length|Length)\s*[:\-]?\s*(\d{3}(?:\.\d)?)\s*mm", text, re.I)
    if m and "length_mm" not in out:
        out["length_mm"] = float(m.group(1))
    m = re.search(r"(?:Power\s+(?:Cable\s+Requirement|Connector)s?)\s*[:\-]?\s*([0-9x ]+\s*-?\s*(?:16|12|8|6\+2|6)\s*-?\s*pin)", text, re.I)
    if m:
        out["connector"] = re.sub(r"\s+", " ", m.group(1)).strip()
    return out


def compat(job: dict, parts: dict) -> list[dict]:
    """Rows: {stage, check, result, detail, source}. parts maps component key -> listing record."""
    rows = []

    def row(stage, check, ok, detail, source=None):
        rows.append({"stage": stage, "check": check, "result": UNVERIFIED if ok is None else (PASS if ok else FAIL),
                     "detail": detail, "source": source})

    gpu = next(v for k, v in parts.items() if k.startswith("gpu"))
    fam = gpu_family(job, gpu)
    gcat = {**(gpu.get("catalog") or {}), **{k: v for k, v in gpu_dims(gpu).items()}}  # the listing's own numbers win
    if gcat.get("thickness_mm") and not (gpu.get("catalog") or {}).get("slots"):
        gcat["slots"] = round(gcat["thickness_mm"] / SLOT_MM, 2)
    if gcat.get("connector") and re.search(r"16|12\s*-?\s*pin", gcat["connector"]):
        rows.append({"stage": "now", "check": "GPU power connector", "result": UNVERIFIED, "source": None,
                     "detail": f"card uses {gcat['connector']} - needs the 8-pin-to-16-pin adapter from the card's box (confirm it is included)"})
    ws = parts.get("workstation")
    wsc = (ws or {}).get("catalog") or {}
    case = (parts.get("case") or {}).get("catalog") or {}
    cooler = (parts.get("cooler") or {}).get("catalog") or {}
    board = board_of(parts)
    watts = psu_watts(parts)
    plat_key, plat = platform_of(job, parts)

    # Platform: socket, memory type, 64GB
    if ws:
        row("now", "Complete workstation", bool(wsc) or None,
            f"{wsc.get('name') or 'model not in catalog'}; CPU, board, RAM, PSU, case and cooling come with the system",
            wsc.get("source"))
        ram_ok = bool(re.search(r"\b(64|96|128|192|256)\s?GB\b", f"{ws.get('title')} {ws.get('specs')}", re.I))
        row("now", "RAM >= 64GB installed", ram_ok or None, "from title/item specifics" if ram_ok else "RAM amount not stated")
    else:
        row("now", "CPU socket = board socket", True, f"{plat_key.upper()} enforced by listing rules")
        row("now", "RAM type matches platform", True, "DDR5 for AM5, DDR4 UDIMM non-ECC for AM4 (listing rules)")

    # BIOS
    if not ws:
        cpu_title = ((parts.get("cpu") or combo_of(parts) or {}).get("title") or "")
        if combo_of(parts):
            b = combo_of(parts)
            tested = bool(re.search(r"\btest(ed)?\b.{0,40}(together|bios|post|boot)|\bbios\b.{0,60}(version|updated|latest)",
                                    f"{b.get('title')} {b.get('description_excerpt')} {b.get('specs')}", re.I))
            row("now", "BIOS supports the CPU", True if tested else None,
                "seller states the bundle was tested together / BIOS updated" if tested else
                "bundle not stated as tested together - ask for a BIOS-screen photo")
        elif "3700X" in cpu_title and re.search(r"X570|B550", (parts.get("motherboard") or {}).get("title") or "", re.I):
            row("now", "BIOS supports the CPU", True, "Ryzen 3000 is supported by every X570/B550 BIOS", board.get("source"))
        elif board.get("bios_flashback"):
            row("now", "BIOS supports the CPU", True, "USB BIOS Flashback / Q-Flash Plus updates the BIOS without a CPU",
                board.get("source"))
        else:
            row("now", "BIOS supports the CPU", None,
                f"needs {board.get('ryzen5000_min_bios') or 'a BIOS newer than the board may have'}; no Flashback - ask the seller")

    # GPU power now
    need_conn = fam.get("connectors")
    if ws:
        psu = (wsc.get("psus") or {}).get(str(watts)) if watts else None
        need8, need6 = fam.get("aux8", 1), fam.get("aux6", 0)
        if not watts:
            row("now", "Workstation PSU powers the GPU", None, "PSU wattage not stated in the listing - the low-wattage option of "
                "this model may have no GPU power connector; ask the seller", wsc.get("source"))
        elif not psu:
            row("now", "Workstation PSU powers the GPU", None, f"{watts} W is not a PSU option in the vendor documents", wsc.get("source"))
        else:
            conns = psu["aux8"] + (psu.get("aux6", 0) if need8 == 0 else 0)
            ok = conns >= max(need8, need6) and (psu["per_card_w"] is None or psu["per_card_w"] >= (fam.get("power_w") or 0))
            if psu["aux8"] == 0 and psu.get("aux6") and need8:
                ok = None  # 6-pin only: an 8-pin card needs an adapter the vendor does not list
            row("now", "Workstation PSU powers the GPU", ok,
                f"{watts} W PSU: {psu['note']}; GPU needs {fam.get('connectors')} at {fam.get('power_w')} W", wsc.get("source"))
        if wsc.get("kit_note") and (fam.get("power_w") or 0) >= 140:
            row("now", "Vendor cooling kit for this GPU class", None, wsc["kit_note"] + " - confirm it is installed", wsc.get("source"))
        blob = f"{ws.get('title')} {ws.get('specs')} {ws.get('description_excerpt')}"
        four = re.search(r"\b(4|8)\s*x\s*(8|16)\s?GB|\b(8|16)\s?GB\s*x\s*(4|8)\b", blob, re.I)
        two = re.search(r"\b2\s*x\s*32\s?GB|\b32\s?GB\s*x\s*2\b", blob, re.I)
        row("now", "RAM fills 4 channels (CPU offload bandwidth)", True if four else (False if two else None),
            "4+ DIMMs installed" if four else ("2x32GB: only 2 of 4 channels used - half the CPU memory bandwidth" if two
                                               else "DIMM layout not stated - ask; 4 or 8 DIMMs give the full 4-channel bandwidth"))
    else:
        row("now", "PSU wattage for one GPU", (watts >= (fam.get("psu_w") or 650)) if watts else None,
            f"PSU {watts or '?'} W vs {fam.get('psu_w') or '?'} W recommended for {fam.get('name')}", fam.get("source"))
        pr = parts.get("psu") or {}
        cab = pr.get("status") == "pass" and not pr.get("_review")
        row("now", "PSU native PCIe cables", True if cab else None,
            "listing confirms the modular cables are included" if cab else "cables not confirmed - ask for a photo")

    # Fit now
    if ws:
        row("now", "Onboard M.2 slot for the NVMe", wsc.get("m2_ok"), wsc.get("m2") or "M.2 NVMe support not verified for this model",
            wsc.get("m2_source") or wsc.get("source"))
    glen = gcat.get("length_mm") or fam.get("length_mm_typ")
    gmax = case.get("gpu_max_mm") or wsc.get("max_gpu_mm")
    row("now", "GPU length fits", (glen <= gmax) if (glen and gmax) else None,
        f"card {glen or '?'} mm{' (typical for the family; exact SKU not identified)' if not gcat.get('length_mm') and glen else ''}"
        f", max {gmax or '?'} mm", gcat.get("source") or case.get("source") or wsc.get("source"))
    if not ws:
        kh, kmax = cooler.get("height_mm"), case.get("cooler_max_mm")
        row("now", "Cooler height fits", (kh <= kmax) if (kh and kmax) else None, f"cooler {kh or '?'} mm, case max {kmax or '?'} mm",
            cooler.get("source"))
        if plat_key == "am5":
            row("now", "Cooler mounting", None, "AM5 mounting kit must be confirmed for the cooler listing")
        else:
            row("now", "Cooler mounting", cooler.get("am4"), "AM4 kit included per manufacturer" if cooler.get("am4") else "not verified",
                cooler.get("source"))
        st1 = board.get("stage1_config")
        row("now", "Stage-1 slot placement", True if st1 else None, st1 or "board placement rules not verified", board.get("manual_url"))

    # Stage 2: second GPU
    two_w = 2 * (fam.get("power_w") or 0) + (plat.get("cpu_w") or 125) + 100
    if ws:
        psu = (wsc.get("psus") or {}).get(str(watts)) if watts else None
        need8 = fam.get("aux8", 1)
        cpus2 = bool(re.search(r"\b2\s*x\s*(Xeon|Gold|Silver|Platinum)|dual\s+(cpu|processor|xeon)|\b2X\s+Gold|&\s*5122", ws.get("title") or "", re.I))
        if not psu:
            row("upgrade", "PSU powers two GPUs", None, "PSU option not identified", wsc.get("source"))
        else:
            limit = wsc["two_cpu_limit_w"] if cpus2 and wsc.get("two_cpu_limit_w") else psu["two_card_w"]
            conns_ok = (psu["aux8"] + (psu.get("aux6", 0) if need8 == 0 else 0)) >= 2 * max(need8, fam.get("aux6", 0))
            ok = (conns_ok and limit >= (fam.get("power_w") or 0)) if limit is not None else (None if conns_ok else False)
            row("upgrade", "PSU powers two GPUs", ok,
                f"{watts} W PSU: {psu['note']}" + (f"; two CPUs installed: {limit} W single-wide limit" if cpus2 and wsc.get("two_cpu_limit_w") else "")
                + f"; two cards need 2x {fam.get('connectors')} at {fam.get('power_w')} W", wsc.get("source"))
        t_slots = math.ceil((gcat.get("slots") or fam.get("slots_typ") or 2) - 0.1)
        row("upgrade", "Two GPUs fit the vendor's dual-GPU slots", True if t_slots <= 2 else None,
            f"{wsc.get('dual_slots')}; {t_slots}-slot cards" + ("" if t_slots <= 2 else " - thicker than the vendor's double-wide limit; "
                                                                 "check the exact card"), wsc.get("source"))
    else:
        row("upgrade", "PSU wattage for two GPUs", (watts >= two_w) if watts and fam.get("power_w") else None,
            f"PSU {watts or '?'} W vs ~{two_w} W (2x{fam.get('power_w') or '?'} W GPU + CPU + 100 W headroom)")
        st2 = board.get("stage2_config") or {}
        row("upgrade", "Second GPU slot works with the NVMe installed", st2.get("possible"),
            (f"{st2.get('gpu_slots')}: {st2.get('widths')}; NVMe in {st2.get('nvme_slot')}"
             + (f"; SATA lost: {st2['sata_lost']}" if st2.get("sata_lost") else "")) if st2 else "shared-resource rules not verified",
            board.get("manual_url"))
        pos = board.get("gpu_positions")  # e.g. [2, 5] - slot positions of the two GPU slots
        thick = gcat.get("slots") or (round(gcat["thickness_mm"] / SLOT_MM, 2) if gcat.get("thickness_mm") else None) \
            or fam.get("slots_typ")
        cslots = case.get("slots")
        if pos and thick and cslots:
            t = math.ceil(thick - 0.1)  # 41-42 mm cards are sold as 2-slot (2 x 20.32 = 40.6 mm)
            needs = pos[1] + t - 1
            gap = pos[1] - (pos[0] + t)
            row("upgrade", "Second GPU physically fits", needs <= cslots,
                f"lower GPU at slot {pos[1]}, {thick}-slot card needs {needs} case slots; case has {cslots}", board.get("manual_url"))
            row("upgrade", "Air gap between the two GPUs", gap >= 1,
                f"{gap} free slot(s) between cards" + (" - the cards touch or overlap: the upper card starves for air" if gap <= 0 else ""))
        else:
            row("upgrade", "Second GPU physically fits", None,
                f"needs board slot positions ({'ok' if pos else 'missing'}), card thickness ({thick or 'missing'}), case slots ({cslots or 'missing'})")
    return rows


def summarize(rows):
    now = [r for r in rows if r["stage"] == "now"]
    up = [r for r in rows if r["stage"] == "upgrade"]
    return {"works_now": not any(r["result"] == FAIL for r in now),
            "now_unverified": sum(r["result"] == UNVERIFIED for r in now),
            "dual_ready": "NO" if any(r["result"] == FAIL for r in up) else ("UNVERIFIED" if any(r["result"] == UNVERIFIED for r in up) else "YES"),
            "fails": [r["check"] for r in rows if r["result"] == FAIL]}


# ── enumeration ──────────────────────────────────────────────────────────────

CORE = {"cpu", "motherboard", "ram"}
REST = ("psu", "case", "cooler")


def _candidates(recs, comp, min_trust, top):
    c = usable(recs, comp, min_trust, top)
    if comp == "motherboard":  # keep the cheapest x8/x8 boards in play for the dual-GPU comparison
        x8 = [r for r in usable(recs, comp, min_trust, 50) if (r.get("catalog") or {}).get("x8x8")]
        c += [r for r in x8[:3] if r not in c]
    return c


def platform_options(job, recs, min_trust):
    """Each option: component -> listing, together covering cpu, motherboard, ram, psu, case, cooler, on ONE platform."""
    comps = job["components"]
    rest = {k: usable(recs, k, min_trust, 3) for k in REST}
    rest_combos = [dict(zip(REST, c)) for c in itertools.product(*[rest[k] for k in REST])] if all(rest.values()) else []
    opts = []
    for plat in sorted({c.get("platform") for c in comps.values() if c.get("platform")}):
        pcs = {k: c for k, c in comps.items() if c.get("platform") == plat}
        for k, c in pcs.items():  # complete systems
            if {"psu", "case"} <= set(c["covers"]):
                opts += [{k: w} for w in usable(recs, k, min_trust, 12)]
        core = [k for k, c in pcs.items() if set(c["covers"]) <= CORE]
        for r in (1, 2, 3):
            for combo in itertools.combinations(core, r):
                cov = [set(comps[k]["covers"]) for k in combo]
                if sum(len(s) for s in cov) != 3 or set().union(*cov) != CORE:
                    continue  # must partition cpu/motherboard/ram exactly
                for picks in itertools.product(*[_candidates(recs, k, min_trust, 3) for k in combo]):
                    for rc in rest_combos:
                        opts.append({**dict(zip(combo, picks)), **rc})
    return opts


def _plat_cost(opt):
    return sum(r["landed_cost"] for r in opt.values())


def _preselect(job, opts, per_platform=10):
    """Cheapest options per platform, plus the cheapest ones with an x8/x8 board, so no platform is crowded out."""
    groups = {}
    for o in opts:
        key = next((job["components"][k].get("platform") for k in o if job["components"][k].get("platform")), "?")
        groups.setdefault(key, []).append(o)
    keep = []
    for g in groups.values():
        g.sort(key=_plat_cost)
        keep += g[:per_platform]
        keep += [o for o in g if (o.get("motherboard") or {}).get("catalog", {}) and o["motherboard"]["catalog"].get("x8x8")
                 and o not in keep][:4]
    return keep


def enumerate_builds(job, recs, min_trust=55, max_builds=20000):
    gpus = [g for k in job["components"] if job["components"][k].get("covers") == ["gpu"] for g in usable(recs, k, min_trust, 3)]
    ssds = usable(recs, "ssd", min_trust, 2)
    out = []
    for plat in _preselect(job, platform_options(job, recs, min_trust)):
        for g in gpus:
            for s in ssds:
                parts = {g["component"]: g, **plat, "ssd": s}
                rows = compat(job, parts)
                sm = summarize(rows)
                total = round(sum(p["landed_cost"] for p in parts.values()), 2)
                fam = gpu_family(job, g)
                _, pl = platform_of(job, parts)
                out.append({"parts": {k: v["item_id"] for k, v in parts.items()}, "total": total,
                            "gpu_family": fam.get("name") or g["component"], "vram_gb": fam.get("vram_gb"),
                            "gpu_bw": fam.get("bandwidth_gbs"), "cpu_mem_bw": pl.get("mem_bw_gbs"), "platform": pl.get("name"),
                            "min_trust": min(p["trust"]["score"] for p in parts.values()),
                            "review_parts": [k for k, v in parts.items() if v.get("_review")],
                            "compatibility": rows, **sm})
                if len(out) >= max_builds:
                    return out
    return out


def second_gpu_cost(job, recs, build, min_trust=55):
    """Landed price of a second card of the same family, plus a PSU swap if the stage-2 wattage check fails."""
    comp = next(k for k in build["parts"] if k.startswith("gpu"))
    first = build["parts"][comp]
    by_id = {r["item_id"]: r for r in recs}
    base = {k: by_id[i] for k, i in build["parts"].items() if k != comp}

    def fits(card):  # the second card must also pass this platform's stage-1 checks (length, power)
        return not any(r["result"] == FAIL for r in compat(job, {comp: card, **base}) if r["stage"] == "now")
    others = [r for r in usable(recs, comp, min_trust, 20) if r["item_id"] != first and not r.get("_review") and fits(r)]
    card = others[0]["landed_cost"] if others else None
    psu_fix = any(r["check"].startswith("PSU wattage for two") and r["result"] == FAIL for r in build["compatibility"])
    return {"second_card": card, "second_card_item": others[0]["item_id"] if others else None,
            "psu_upgrade_needed": psu_fix, "note": None if card else "no second listing of this card read"}


def pick(builds, budget):
    """The comparison builds. Only builds that work now and fit the budget compete."""
    ok = [b for b in builds if b["works_now"] and b["total"] <= budget]
    verified = lambda b: not b["review_parts"] and b["now_unverified"] == 0
    score = lambda b: ((b["vram_gb"] or 0), (b["gpu_bw"] or 0), verified(b), -(b["now_unverified"]), -b["total"])
    out = {}
    if not ok:
        return out
    fv = [b for b in ok if verified(b)]
    if fv:
        out["best_fully_verified"] = max(fv, key=lambda b: (score(b), b["min_trust"]))
    out["best_overall"] = max(ok, key=lambda b: (score(b), b["dual_ready"] == "YES", b["min_trust"]))
    worthwhile = [b for b in ok if (b["vram_gb"] or 0) >= 12 and b["now_unverified"] <= 2]
    clean = [b for b in worthwhile if not b["review_parts"]]
    if clean or worthwhile:
        out["cheapest_worthwhile"] = min(clean or worthwhile, key=lambda b: (b["total"], -b["min_trust"]))
    dual = [b for b in ok if b["dual_ready"] == "YES"] or [b for b in ok if b["dual_ready"] == "UNVERIFIED"]
    if dual:
        out["best_dual_gpu"] = max(dual, key=lambda b: (b["dual_ready"] == "YES", not b["review_parts"],
                                                         -(b["total"] + (b.get("upgrade_total") or 0)), b["min_trust"]))
    big = [b for b in ok if (b["vram_gb"] or 0) >= 24]
    if big:
        out["best_24gb_single"] = min(big, key=lambda b: (b["now_unverified"], b["total"]))
    for name, lo, hi in (("tier_A", 1000, 1200), ("tier_B", 1200, 1350), ("tier_C", 1350, 1500)):
        t = [b for b in ok if lo <= b["total"] <= hi]
        if t:
            out[name] = max(t, key=lambda b: (not b["review_parts"], score(b), b["min_trust"]))
    return out
