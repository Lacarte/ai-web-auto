"""Decisions, structured results, report.md and dashboard.html for multi-platform jobs (builds.py).

Totals are computed once from line items and re-checked (check_totals) before anything is written, so the
dashboard can't disagree with results.json.
"""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

import builds as B

BUY, WAIT, INVESTIGATE, REJECT = "BUY CANDIDATE", "WAIT", "INVESTIGATE", "REJECT"


def item_decision(rec: dict, mkt: dict | None) -> tuple[str, str]:
    if rec["status"] == "reject":
        return REJECT, "; ".join(rec["reject_reasons"])[:300]
    if rec["status"] == "review" or rec.get("variations"):
        return INVESTIGATE, "; ".join(rec["review_notes"] or ["trust below threshold"])[:300]
    base = (rec.get("price") or 0) + (rec.get("shipping_to_zip") or 0)
    if mkt and rec.get("vs_market") is not None and base > mkt["p75"] * 1.1:
        return WAIT, f"{rec['vs_market']:+d}% vs sold median ${mkt['median']:.0f} - above the usual range"
    return BUY, "verified listing, price within the sold range" if mkt else "verified listing (no sold baseline for this model)"


def build_decision(b: dict, budget: float) -> tuple[str, str]:
    if not b["works_now"]:
        return REJECT, "fails: " + ", ".join(b["fails"])
    if b["total"] > budget:
        return WAIT, f"over budget by ${b['total'] - budget:,.2f}"
    bad = [p for p, d in b["part_decisions"].items() if d != BUY]
    if bad or b["now_unverified"]:
        return INVESTIGATE, (f"parts to confirm: {', '.join(bad)}; " if bad else "") + f"{b['now_unverified']} unverified stage-1 check(s)"
    return BUY, "all parts verified listings; stage-1 checks pass"


def capability(job, b) -> dict:
    """What the build can do: MEASURED GPU numbers (with sources) and ESTIMATED offload / power figures."""
    comp = next(k for k in b["parts"] if k.startswith("gpu"))
    bench = (job["catalog"].get("benchmarks") or {}).get("per_gpu", {}).get(comp, {})
    fam = job["catalog"].get("gpu_families", {}).get(comp, {})
    bw = b.get("cpu_mem_bw") or 0
    moe = round(0.65 * bw / 1.09, 1) if bw else None  # Qwen3-30B-A3B Q4: ~1.09 GB of routed experts per token
    watts = (fam.get("power_w") or 0) + 110
    kwh = round((watts * 4 + 60 * 20) * 30 / 1000, 1)  # 4 h/day under load + 20 h idle at ~60 W
    return {"measured": bench, "moe_offload_ceiling_tps": moe,
            "moe_note": "ESTIMATED: all routed experts of Qwen3-30B-A3B Q4 in system RAM, tg <= 0.65 x RAM bandwidth / 1.09 GB "
                        f"({bw} GB/s). Measured on a 3060 + DDR4 dual-channel with some experts on the GPU: ~45 t/s (Qwen3.6-35B-A3B).",
            "inference_draw_w": watts, "monthly_kwh": kwh, "monthly_cost_usd_at_0_15": round(kwh * 0.15, 2),
            "power_note": "ESTIMATED: GPU board power + ~110 W platform under load; 4 h/day load, 20 h/day idle at ~60 W; $0.15/kWh assumed (check your FPL rate)"}


def check_totals(results: dict) -> None:
    by_id = {r["item_id"]: r for r in results["listings"]}
    for name, b in results["selected"].items():
        s = round(sum(by_id[i]["landed_cost"] for i in b["parts"].values()), 2)
        assert abs(s - b["total"]) < 0.01, f"{name}: total {b['total']} != sum of parts {s}"
        assert abs(b["budget_remaining"] - round(results["budget"] - b["total"], 2)) < 0.01, name


def finish_v2(job, recs, markets, out, write, failures) -> dict:
    budget = job["budget"]
    decisions = {}
    for r in recs:
        d, why = item_decision(r, markets.get(r["component"]))
        r["decision"], r["decision_why"] = d, why
        decisions[r["item_id"]] = d
    all_builds = B.enumerate_builds(job, recs)
    for b in all_builds:
        b["part_decisions"] = {k: decisions[i] for k, i in b["parts"].items()}
    chosen = B.pick(all_builds, budget)
    by_id = {r["item_id"]: r for r in recs}
    selected = {}
    for name, b in chosen.items():
        up = B.second_gpu_cost(job, recs, b)
        d, why = build_decision(b, budget)
        selected[name] = {**{k: v for k, v in b.items() if k != "part_decisions"}, "part_decisions": b["part_decisions"],
                          "budget_remaining": round(budget - b["total"], 2), "decision": d, "decision_why": why,
                          "upgrade": up, "upgrade_total": round(b["total"] + (up["second_card"] or 0), 2) if up["second_card"] else None,
                          "capability": capability(job, b)}
    # cheapest complete platform (everything but GPU and SSD) per platform type, for the comparison table
    plat_cmp = {}
    for b in all_builds:
        if not b["works_now"]:
            continue
        key = b.get("platform") or "?"
        cost = round(sum(by_id[i]["landed_cost"] for k, i in b["parts"].items() if not k.startswith("gpu") and k != "ssd"), 2)
        if key not in plat_cmp or cost < plat_cmp[key]["platform_cost"]:
            plat_cmp[key] = {"platform_cost": cost, "parts": {k: i for k, i in b["parts"].items() if not k.startswith("gpu") and k != "ssd"},
                             "cpu_mem_bw_gbs": b.get("cpu_mem_bw")}
    gpu_market = {}
    for k, c in job["components"].items():
        if c.get("covers") == ["gpu"]:
            fam = job["catalog"].get("gpu_families", {}).get(k, {})
            cands = sorted([r for r in recs if r["component"] == k and r.get("landed_cost")], key=lambda r: r["landed_cost"])
            ok = [r for r in cands if r["decision"] in (BUY, WAIT)]
            gpu_market[k] = {"name": fam.get("name") or c["label"], "vram_gb": fam.get("vram_gb"), "bandwidth_gbs": fam.get("bandwidth_gbs"),
                             "power_w": fam.get("power_w"), "sold": markets.get(k), "cheapest_verified": ok[0]["item_id"] if ok else None,
                             "cheapest_verified_landed": ok[0]["landed_cost"] if ok else None, "note": c.get("note")}
    results = {"job": job["name"], "generated_at": datetime.now().isoformat(timespec="seconds"), "budget": budget,
               "ship_to": job["ship_to_zip"], "tax_rate": 0.07, "tax_basis": job.get("tax_basis"),
               "selected": selected, "platform_comparison": plat_cmp, "gpu_market": gpu_market, "markets": markets,
               "builds_evaluated": len(all_builds), "builds_working_now": sum(b["works_now"] for b in all_builds),
               "builds_under_budget": sum(b["works_now"] and b["total"] <= budget for b in all_builds),
               "failures": failures, "benchmarks": job["catalog"].get("benchmarks"), "listings": recs}
    check_totals(results)
    write(out / "results.json", results)
    (out / "report.md").write_text(report_md(job, results), encoding="utf-8")
    (out / "dashboard.html").write_text(dashboard(job, results), encoding="utf-8")
    return results


# ── rendering ────────────────────────────────────────────────────────────────

TITLES = {"best_overall": "Best overall under budget", "best_fully_verified": "Best fully verified (no open questions)", "cheapest_worthwhile": "Cheapest worthwhile",
          "best_dual_gpu": "Best future dual-GPU", "best_24gb_single": "Best single 24GB GPU",
          "tier_A": "Tier A · $1,000–1,200", "tier_B": "Tier B · $1,200–1,350", "tier_C": "Tier C · $1,350–1,500"}


def _m(v):
    return f"${v:,.2f}" if isinstance(v, (int, float)) else "—"


def report_md(job, R) -> str:
    by_id = {r["item_id"]: r for r in R["listings"]}
    L = [f"# {job['title']}", "",
         f"Generated {R['generated_at']} · ship to {R['ship_to']} · budget {_m(R['budget'])} (hard ceiling) · "
         f"landed = (price + shipping) × 1.07 FL tax ({R.get('tax_basis') or 'estimate'}). Read-only research: nothing bought, bid, "
         "messaged or carted. Labels: VERIFIED (manufacturer doc / listing), MEASURED (published benchmark), ESTIMATED, UNVERIFIED.", "",
         f"{R['builds_evaluated']} complete builds evaluated; {R['builds_working_now']} work with one GPU; {R['builds_under_budget']} of those fit the budget.", ""]
    if "best_24gb_single" not in R["selected"]:
        g = R["gpu_market"].get("gpu_3090", {})
        L += [f"**No 24GB build fits {_m(R['budget'])}:** RTX 3090 sold median {_m((g.get('sold') or {}).get('median'))}; "
              f"RTX A5000 sold median {_m(((R['gpu_market'].get('gpu_a5000') or {}).get('sold') or {}).get('median'))}.", ""]
    L += ["## Selected builds", "", "| Build | Decision | Total | Remaining | GPU | VRAM | Platform | Dual-GPU | 2nd card | Total with 2nd |", "|---|---|---|---|---|---|---|---|---|---|"]
    for n, b in R["selected"].items():
        L.append(f"| {TITLES.get(n, n)} | **{b['decision']}** | {_m(b['total'])} | {_m(b['budget_remaining'])} | {b['gpu_family']} | {b['vram_gb']} GB | "
                 f"{b['platform']} | {b['dual_ready']} | {_m(b['upgrade']['second_card'])} | {_m(b['upgrade_total'])} |")
    for n, b in R["selected"].items():
        L += ["", f"### {TITLES.get(n, n)} — {_m(b['total'])} — {b['decision']}", "", f"_{b['decision_why']}_", "",
              "| Part | Listing | Price | Ship | Landed | Seller | Returns | Decision | Read at |", "|---|---|---|---|---|---|---|---|---|"]
        for k, iid in b["parts"].items():
            r = by_id[iid]
            seller = (r.get("seller_text") or "").split("\n")[0][:28]
            L.append(f"| {k} | [{r['title'][:64]}]({r['url']}) `{iid}` | {_m(r['price'])} | {_m(r.get('shipping_to_zip'))} | {_m(r['landed_cost'])} | "
                     f"{seller} {r.get('feedback_pct')}% ({r.get('feedback_count')}) | {r.get('return_days')} d | {r['decision']} | {r['evidence']['fetched_at']} |")
        L += ["", "| Stage | Check | Result | Detail | Source |", "|---|---|---|---|---|"]
        L += [f"| {c['stage']} | {c['check']} | **{c['result']}** | {c['detail']} | {c.get('source') or ''} |" for c in b["compatibility"]]
        cap = b["capability"]
        mb = cap["measured"]
        L += ["", "**Inference evidence for this GPU** (MEASURED unless marked): " + "; ".join(
                  f"{k}: {v}" for k, v in mb.items() if not k.startswith("src") and k != "moe_note") +
              (" — sources: " + ", ".join(v for k, v in mb.items() if k.startswith("src")) if mb else ""),
              f"- MoE expert-offload ceiling: {cap['moe_offload_ceiling_tps']} t/s — {cap['moe_note']}",
              f"- Power: ~{cap['inference_draw_w']} W under inference; ~{cap['monthly_kwh']} kWh/month ≈ ${cap['monthly_cost_usd_at_0_15']}/month — {cap['power_note']}"]
        up = b["upgrade"]
        L += ["", f"Second GPU: {_m(up['second_card'])} (listing {up['second_card_item'] or '—'})"
              + ("; PSU replacement needed" if up["psu_upgrade_needed"] else "") + (f"; {up['note']}" if up.get("note") else "")]
    L += ["", "## Platform comparison (everything except GPU and SSD, cheapest verified option)", "", "| Platform | Cost | CPU memory bandwidth |", "|---|---|---|"]
    L += [f"| {k} | {_m(v['platform_cost'])} | {v.get('cpu_mem_bw_gbs') or '—'} GB/s |" for k, v in R["platform_comparison"].items()]
    L += ["", "## GPU market (eBay US, sold comps filtered to the model)", "", "| GPU | VRAM | Bandwidth | Board power | Sold 25% / median / 75% | Cheapest verified |", "|---|---|---|---|---|---|"]
    for k, g in R["gpu_market"].items():
        s = g.get("sold") or {}
        L.append(f"| {g['name']} | {g.get('vram_gb') or '—'} GB | {g.get('bandwidth_gbs') or '—'} GB/s | {g.get('power_w') or '—'} W | "
                 f"{_m(s.get('p25'))} / {_m(s.get('median'))} / {_m(s.get('p75'))} | {_m(g.get('cheapest_verified_landed'))} {g.get('note') or ''} |")
    if R.get("benchmarks"):
        L += ["", "## Inference evidence", "", "```json", json.dumps(R["benchmarks"], indent=1)[:6000], "```"]
    L += ["", "## Every listing read", "", "| Decision | Component | Listing | Landed | Trust | Why |", "|---|---|---|---|---|---|"]
    for r in sorted(R["listings"], key=lambda r: (r["component"], [BUY, WAIT, INVESTIGATE, REJECT].index(r["decision"]), r.get("landed_cost") or 9e9)):
        L.append(f"| {r['decision']} | {r['component']} | [{r['title'][:60]}]({r['url']}) | {_m(r['landed_cost'])} | {r['trust']['score']} | "
                 f"{r['decision_why'][:160].replace('|', '/')} |")
    if job.get("dr_import"):
        L += ["", "## Forwarding to the Dominican Republic (NOT included in the budget)", "", job["dr_import"]["note"] + f" Source: {job['dr_import']['source']}"]
    if R["failures"]:
        L += ["", "## Searches without a passing listing", ""] + [f"- **{k}**: {v['reason']}" for k, v in R["failures"].items()]
    return "\n".join(L)


CSS = """
:root{--bg:#f5f6f8;--card:#fff;--ink:#16191d;--mut:#5f6b7a;--line:#e3e7ec;--ok:#1d7a3e;--warn:#9a6300;--bad:#b3261e;--acc:#1f5fd1}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;background:var(--bg);color:var(--ink)}
header{padding:22px 28px;background:#0f1b2d;color:#fff}header h1{margin:0 0 4px;font-size:20px}header p{margin:0;color:#c5cfdb;font-size:13px}
main{padding:20px 28px;max-width:1500px;margin:auto}.kpis{display:flex;gap:12px;flex-wrap:wrap;margin:0 0 18px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;min-width:160px}.kpi b{display:block;font-size:20px}
.kpi span{color:var(--mut);font-size:12px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(440px,1fr));gap:16px}
section.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
.card h2{font-size:15px;margin:0 0 2px}.total{font-size:26px;font-weight:650}.sub{color:var(--mut);font-size:12px;margin-bottom:8px}
.badge{display:inline-block;padding:1px 8px;border-radius:99px;font-size:11px;font-weight:650;letter-spacing:.02em;vertical-align:middle}
.BUY{background:#e3f4e8;color:var(--ok)}.WAIT{background:#fff3d6;color:var(--warn)}.INVESTIGATE{background:#e8eefc;color:var(--acc)}.REJECT{background:#fde7e5;color:var(--bad)}
.PASS{color:var(--ok);font-weight:650}.FAIL{color:var(--bad);font-weight:650}.UNVERIFIED{color:var(--warn);font-weight:650}
table{border-collapse:collapse;width:100%;font-size:13px}th{text-align:left;color:var(--mut);font-weight:600;border-bottom:1px solid var(--line);padding:6px}
td{border-top:1px solid var(--line);padding:6px;vertical-align:top}td.n{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}.m{color:var(--mut);font-size:11.5px}details{margin-top:8px}summary{cursor:pointer;color:var(--acc)}
h3{font-size:14px;margin:22px 0 8px}.specs{display:flex;gap:14px;flex-wrap:wrap;margin:6px 0 10px;font-size:12.5px}.specs div b{display:block;font-size:15px}
"""


def dashboard(job, R) -> str:
    e = html.escape
    by_id = {r["item_id"]: r for r in R["listings"]}
    dcls = lambda d: d.split()[0]
    cards = []
    for n, b in R["selected"].items():
        rows = "".join(
            f"<tr><td>{e(k)}</td><td><a href='{e(by_id[i]['url'])}' target='_blank'>{e(by_id[i]['title'][:72])}</a>"
            f"<div class=m>#{i} · {e((by_id[i].get('condition') or '').splitlines()[0][:24])} · returns {by_id[i].get('return_days')} d · "
            f"seller {by_id[i].get('feedback_pct')}% ({by_id[i].get('feedback_count')}) · read {e(by_id[i]['evidence']['fetched_at'])}</div></td>"
            f"<td class=n>{_m(by_id[i]['price'])}<div class=m>+{_m(by_id[i].get('shipping_to_zip'))} ship</div></td>"
            f"<td class=n><b>{_m(by_id[i]['landed_cost'])}</b></td><td><span class='badge {dcls(by_id[i]['decision'])}'>{e(by_id[i]['decision'])}</span></td></tr>"
            for k, i in b["parts"].items())
        comp = "".join(f"<tr><td>{e(c['stage'])}</td><td>{e(c['check'])}</td><td class='{c['result']}'>{c['result']}</td>"
                       f"<td>{e(c['detail'])}{(' <a href=' + chr(39) + e(c['source']) + chr(39) + ' target=_blank>source</a>') if c.get('source', '') and str(c.get('source')).startswith('http') else ''}</td></tr>"
                       for c in b["compatibility"])
        up = b["upgrade"]
        cards.append(f"""<section class=card><h2>{e(TITLES.get(n, n))} <span class='badge {dcls(b['decision'])}'>{e(b['decision'])}</span></h2>
<div class=total>{_m(b['total'])}</div><div class=sub>{_m(b['budget_remaining'])} left of {_m(R['budget'])} · {e(b['decision_why'])}</div>
<div class=specs><div><b>{b['vram_gb'] or '?'} GB</b>VRAM · {e(b['gpu_family'])}</div><div><b>{b['gpu_bw'] or '?'} GB/s</b>GPU bandwidth</div>
<div><b>{b['cpu_mem_bw'] or '?'} GB/s</b>CPU RAM bandwidth · {e(b['platform'] or '')}</div><div><b>{e(b['dual_ready'])}</b>dual-GPU ready</div>
<div><b>{_m(up['second_card'])}</b>2nd card → {_m(b['upgrade_total'])}</div></div>
<table><tr><th>Part</th><th>Listing</th><th>Price</th><th>Landed</th><th>Decision</th></tr>{rows}</table>
<details><summary>Compatibility: {sum(c['result'] == 'PASS' for c in b['compatibility'])} pass · {sum(c['result'] == 'UNVERIFIED' for c in b['compatibility'])} unverified · {sum(c['result'] == 'FAIL' for c in b['compatibility'])} fail</summary>
<table><tr><th>Stage</th><th>Check</th><th>Result</th><th>Detail</th></tr>{comp}</table></details></section>""")
    gm = "".join(f"<tr><td>{e(g['name'])}</td><td class=n>{g.get('vram_gb') or '—'} GB</td><td class=n>{g.get('bandwidth_gbs') or '—'}</td>"
                 f"<td class=n>{_m((g.get('sold') or {}).get('median'))}</td><td class=n>{_m(g.get('cheapest_verified_landed'))}</td><td class=m>{e(g.get('note') or '')}</td></tr>"
                 for g in R["gpu_market"].values())
    pc = "".join(f"<tr><td>{e(k)}</td><td class=n>{_m(v['platform_cost'])}</td><td class=n>{v.get('cpu_mem_bw_gbs') or '—'} GB/s</td></tr>"
                 for k, v in R["platform_comparison"].items())
    allrows = "".join(f"<tr><td><span class='badge {dcls(r['decision'])}'>{e(r['decision'])}</span></td><td>{e(r['component'])}</td>"
                      f"<td><a href='{e(r['url'])}' target=_blank>{e(r['title'][:70])}</a></td><td class=n>{_m(r['landed_cost'])}</td>"
                      f"<td class=n>{r['trust']['score']}</td><td class=m>{e(r['decision_why'][:180])}</td></tr>"
                      for r in sorted(R["listings"], key=lambda r: (r["component"], r.get("landed_cost") or 9e9)))
    best = R["selected"].get("best_overall")
    return f"""<!DOCTYPE html><html lang=en><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>{e(job['title'])}</title><style>{CSS}</style>
<header><h1>{e(job['title'])}</h1><p>eBay US · delivered to {e(R['ship_to'])} · landed = (price + shipping) × 1.07 FL tax · generated {e(R['generated_at'])} ·
read-only research — every purchase needs your approval</p></header><main>
<div class=kpis><div class=kpi><b>{_m(R['budget'])}</b><span>hard ceiling</span></div>
<div class=kpi><b>{_m(best['total']) if best else '—'}</b><span>best overall build</span></div>
<div class=kpi><b>{R['builds_evaluated']}</b><span>complete builds evaluated</span></div>
<div class=kpi><b>{R['builds_under_budget']}</b><span>work now and fit the budget</span></div>
<div class=kpi><b>{len(R['listings'])}</b><span>listings read with evidence</span></div></div>
<div class=grid>{''.join(cards)}</div>
<h3>GPU market (sold comps filtered to the model)</h3><section class=card><table><tr><th>GPU</th><th>VRAM</th><th>GB/s</th><th>Sold median</th><th>Cheapest verified</th><th></th></tr>{gm}</table></section>
<h3>Platform cost without GPU and SSD (cheapest verified)</h3><section class=card><table><tr><th>Platform</th><th>Cost</th><th>CPU RAM bandwidth</th></tr>{pc}</table></section>
<h3>Every listing read</h3><section class=card><table><tr><th>Decision</th><th>Part</th><th>Listing</th><th>Landed</th><th>Trust</th><th>Why</th></tr>{allrows}</table></section>
<p class=m>Structured data: results.json · reasoning and sources: report.md · page evidence: evidence/ (JSON + screenshot + sha256 per listing).</p></main></html>"""
