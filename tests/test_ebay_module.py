"""eBay module: rules, card parsing, listing assessment, job rules (planted traps), builds, and search
pagination with a fake session (no browser)."""
import asyncio
import json
import sys
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "@modules" / "ebay"
sys.path[:0] = [str(MOD), str(MOD / "jobs"), str(ROOT / "@modules")]

import module_kit  # noqa: E402
import ebay_rules as R  # noqa: E402
import ebay_search as E  # noqa: E402
import job_runner as J  # noqa: E402

JOB = json.loads((MOD / "jobs" / "llm-workstation.json").read_text(encoding="utf-8"))

PARTS_CARD = {"id": "111111111111", "lines": ["MSI GeForce RTX 3060 12GB Ventus 2X OC GPU - B8, Not Working", "Opens in a new window or tab",
                                             "Parts Only", "$189.97", "Buy It Now", "Free delivery in 2-4 days",
                                             "Located in United States", "respec.io 99.7% positive (26K)", "derosnopS"]}
GOOD_CARD = {"id": "128147436297", "lines": ["ASUS Nvidia GeForce RTX 3060 12GB Graphics Card GPU PC (LHR)", "Opens in a new window or tab",
                                            "ASUS · NVIDIA GeForce RTX 3060 · 12 GB", "Pre-Owned", "$299.97", "Buy It Now", "+$10.00 delivery",
                                            "Located in United States", "Free returns", "uventure 99.8% positive (8.9K)"]}


def raw_item(**over):
    raw = {"url": "https://www.ebay.com/itm/128147436297", "item_number": "128147436297",
           "title": "ASUS Nvidia GeForce RTX 3060 12GB Graphics Card", "price_text": "US $299.97", "bin_text": "US $299.97",
           "bid_text": None, "bin_button": True, "bid_button": False, "condition": "Condition: Used Used",
           "quantity": "Quantity: 2 available3 sold", "seller": "L Theory Inc (8956) 99.8% positive",
           "shipping": "Shipping: Free USPS Ground Advantage®. Located in: North Las Vegas, Nevada, United States",
           "returns": "Returns: 30 days returns. Seller pays for return shipping.", "confidence": "Top Rated Plus",
           "store": "", "ratings": "", "specs": {"Brand": "ASUS", "Memory Size": "12 GB", "Chipset/GPU Model": "NVIDIA GeForce RTX 3060"},
           "photos": [f"https://i.ebayimg.com/images/g/{i}/s-l1600.webp" for i in range(6)], "ended": False,
           "description_url": "https://itm.ebaydesc.com/itmdesc/128147436297", "fetched_at": "2026-10-28T00:00:00"}
    raw.update(over)
    return raw


class RulesTest(unittest.TestCase):
    def test_exclusions(self):
        for title, cond in [("RTX 3060 12GB for parts", "Used"), ("RTX 3060 12GB", "For parts or not working"),
                            ("RTX 3060 12GB NOT WORKING", None), ("RTX 3060 - Parts Only", None), ("RTX 3060 no display", None),
                            ("RTX 3060 12GB untested", None), ("RTX 3060 box only", None)]:
            self.assertIsNotNone(R.exclusion_reason(title, cond), title)
        self.assertIsNone(R.exclusion_reason("MSI RTX 3060 Ventus 2X 12G OC tested working", "Used"))
        # live titles seen 2026-10-28
        for title in ["Dell Nvidia RTX 3060 12GB Graphics Card GPU PC (LHR) (RIGHT FAN FAILED)",
                      "* Fan Only No Processor ) AMD Ryzen 7 3700X Heat Sink Fan (FAN ONLY)",
                      "ASUS TUF GAMING B550-PLUS (WIFI) (USED, SLIGHT DAMAGE, NO IO SHIELD)"]:
            self.assertIsNotNone(R.exclusion_reason(title, "Used"), title)
        self.assertIsNone(R.exclusion_reason("AMD Ryzen 5 5600 with Wraith Stealth cooler", "Used"))

    def test_picks_recheck_titles_and_skip_unknown_shipping(self):
        def card(i, title, price, ship=0.0):
            return {"id": i, "title": title, "price": price, "shipping": ship, "condition": "Pre-Owned",
                    "feedback_pct": 99.9, "feedback_count": 5000, "excluded": None}
        res = {"bin": [{"items": [card("1", "Dell RTX 3060 12GB (RIGHT FAN FAILED)", 290),
                                  card("2", "MSI RTX 3060 Ventus 2X 12G OC", 300),
                                  card("3", "Gigabyte RTX 3060 Eagle OC 12G local pickup", 200, None)]}]}
        self.assertEqual(J.pick_to_read(JOB, "gpu", res, 4), ["2"])

    def test_motherboard_search_drops_ram_listing(self):
        self.assertTrue(J.card_check(JOB["components"]["motherboard"],
                                     "8GB (2X4GB) Mem Ram For MSI X470 GAMING PRO, X470 GAMING PRO CARBON"))

    def test_seller_stats_and_money(self):
        self.assertEqual(R.seller_stats("uventure 99.8% positive (8.9K)"), {"feedback_pct": 99.8, "feedback_count": 8900})
        self.assertEqual(R.seller_stats("L Theory Inc (8956) 99.8% positive")["feedback_count"], 8956)
        self.assertEqual(R.shipping_cost("Shipping: US $12.50 USPS"), 12.5)
        self.assertEqual(R.shipping_cost("Shipping: Free USPS Ground"), 0.0)
        pickup = "Shipping, returns, and payments\nPickup:\nFree local pickup over 60 mi from 33101. See map\nShipping:\nUS $25.43 FedEx Ground"
        self.assertEqual(R.shipping_cost(pickup), 25.43)  # free pickup is not free shipping
        self.assertEqual(R.shipping_cost(pickup.replace("US $25.43", "Free")), 0.0)
        self.assertEqual(R.landed_cost(100, 10, 0.07), 117.7)
        self.assertIsNone(R.landed_cost(100, None))

    def test_description_boilerplate_is_review_not_exclusion(self):
        flags = R.description_flags("Tested and working. Returns accepted if the item is not working as described.")
        self.assertTrue(flags)  # flagged with context for a human...
        self.assertIsNone(R.exclusion_reason("RTX 3060 12GB", "Used"))  # ...but the title/condition keep it


class SearchParseTest(unittest.TestCase):
    def test_cards(self):
        bad, good = E.parse_card(PARTS_CARD), E.parse_card(GOOD_CARD)
        self.assertTrue(bad["excluded"])
        self.assertIsNone(good["excluded"])
        self.assertEqual((good["price"], good["shipping"], good["feedback_count"]), (299.97, 10.0, 8900))
        self.assertFalse(good["auction"])

    def test_search_url_excludes_parts_condition(self):
        url = E.search_url("rtx 3060 12gb", 150, 450)
        self.assertIn("LH_BIN=1", url)
        self.assertIn("_stpos=33101", url)
        self.assertNotIn("7000", url)
        self.assertIn("LH_Sold=1", E.search_url("x", sold=True))


class FakeSession:
    """Two result pages: page 1 has a next link; page 2 reaches the 'fewer words' filler."""
    def __init__(self):
        self.urls = []

    async def feedback(self, msg):
        pass

    async def goto(self, url, keys):
        self.urls.append(url)
        return {}

    async def js(self, script, retries=2):
        if "scrollHeight" in script:
            return 5
        page = 2 if "_pgn=2" in self.urls[-1] else 1
        if page == 1:
            cards = [{"id": "123456", "fewer": False, "lines": ["Shop on eBay"]}, dict(GOOD_CARD, fewer=False),
                     dict(PARTS_CARD, fewer=False)]
            return {"url": self.urls[-1], "count": "3 results", "next": self.urls[-1] + "&_pgn=2", "cards": cards}
        card = dict(GOOD_CARD, id="222222222222", fewer=False)
        filler = dict(GOOD_CARD, id="333333333333", fewer=True)
        return {"url": self.urls[-1], "count": "3 results", "next": None, "cards": [card, filler]}


class FakeFormSession(FakeSession):
    """Searching like a person: the typed search lands on eBay's own URL, then the filters, Next is clicked."""
    def __init__(self):
        super().__init__()
        self.s, self.steps = object(), []

    async def search_form(self, query):
        self.steps.append(("typed+clicked Search", query))
        self.urls.append("https://www.ebay.com/sch/i.html?_nkw=rtx+3060+12gb&_sacat=0&_from=R40&_trksid=p4624852.m570.l1313")
        return {"url": self.urls[-1]}

    async def read_down(self, bursts=None):
        self.steps.append(("read", self.urls[-1]))
        return {"bursts": 6}

    async def click_next(self, page):
        self.steps.append(("clicked Next", page))
        self.urls.append(self.urls[-1] + f"&_pgn={page}")
        return "click"


class SearchFlowTest(unittest.TestCase):
    def test_pages_until_filler_and_drops_parts(self):
        sess = FakeSession()
        with mock.patch.dict(E.S, {"search_via": "url"}):
            res = asyncio.run(E.search_products(sess, "rtx 3060 12gb", 150, 450, max_pages=5))
        self.assertEqual(len(sess.urls), 2)
        self.assertEqual([i["id"] for i in res["items"]], ["128147436297", "222222222222"])
        self.assertEqual([i["id"] for i in res["excluded"]], ["111111111111"])

    def test_search_like_a_person(self):
        sess = FakeFormSession()
        with mock.patch.dict(E.S, {"search_via": "form"}), \
                mock.patch.object(E.module_kit, "bring_to_front", new=mock.AsyncMock()):
            res = asyncio.run(E.search_products(sess, "rtx 3060 12gb", 150, 450, max_pages=5))
        self.assertEqual(sess.steps[0], ("typed+clicked Search", "rtx 3060 12gb"))
        filtered = sess.urls[1]  # the typed search's own page, with our filters applied
        for part in ("_nkw=rtx+3060+12gb", "LH_BIN=1", "LH_PrefLoc=3", "_stpos=33101", "_udlo=150", "_udhi=450",
                     "_sop=15", "_from=R40", "_trksid="):
            self.assertIn(part, filtered)
        self.assertIn(("clicked Next", 2), sess.steps)
        self.assertEqual([s[0] for s in sess.steps].count("read"), 2)  # each page is read before parsing
        self.assertEqual([p["via"] for p in res["pages"]], ["search box + Search button, then filters", "click"])
        self.assertEqual([i["id"] for i in res["items"]], ["128147436297", "222222222222"])

    def test_merge_url_keeps_ebay_extras_and_our_filters(self):
        typed = "https://www.ebay.com/sch/i.html?_nkw=rtx+3060&_sacat=0&_from=R40&_trksid=abc&_pgn=3"
        merged = E.merge_url(typed, E.search_url("rtx 3060", 150, 450))
        self.assertIn("_from=R40", merged)
        self.assertIn("_trksid=abc", merged)
        self.assertNotIn("_pgn", merged)
        self.assertIn("_udhi=450", merged)


class ListingTest(unittest.TestCase):
    def test_good_listing(self):
        rec = E.assess_listing("128147436297", raw_item(), {"text": "Fully tested with FurMark.", "url": "d"})
        self.assertEqual(rec["condition"], "Used")
        self.assertEqual(rec["landed_cost"], round(299.97 * 1.07, 2))
        self.assertTrue(rec["tested"])
        self.assertFalse(rec["trust"]["reject"])
        self.assertTrue(rec["available"])

    def test_auction_bid_is_never_a_price(self):
        rec = E.assess_listing("128147436297", raw_item(bin_text=None, bin_button=False, bid_button=True, bid_text="US $120.00"), {})
        self.assertIsNone(rec["price"])
        self.assertIsNone(rec["landed_cost"])
        self.assertEqual(rec["current_bid"], 120.0)
        self.assertTrue(any("auction" in r for r in rec["trust"]["reject"]))

    def test_out_of_stock_page_without_buy_button(self):
        """Item 800020842561 (2026-10-30): 'This item is out of stock' - price shown, no quantity line, no buttons."""
        rec = E.assess_listing("800020842561", raw_item(item_number="800020842561", quantity=None, bin_button=False,
                                                        bid_button=False), {})
        self.assertFalse(rec["available"])
        self.assertEqual(rec["price"], 299.97)  # the price is still read, but the item can't be bought
        self.assertTrue(E.assess_listing("128147436297", raw_item(bin_button=None, bid_button=None), {})["available"])

    def test_item_number_mismatch(self):
        rec = E.assess_listing("999999999999", raw_item(), {})
        self.assertTrue(rec["mismatches"])


def trap(comp, title, specs=None, **raw):
    rec = E.assess_listing("128147436297", raw_item(title=title, specs=specs or {}, **raw), {"text": raw.pop("desc", "")})
    return J.assess(JOB, comp, rec, None, 55)


class TrapTest(unittest.TestCase):
    """Planted traps: each must be rejected by the job rules."""
    def test_traps_rejected(self):
        traps = [("ram", "Crucial 64GB (2x32GB) DDR4 3200 SODIMM Laptop Memory", {}),
                 ("ram", "Kingston 64GB (2x32GB) DDR5 5600 Desktop", {}),
                 ("ram", "Samsung 64GB (2x32GB) DDR4 3200 ECC Registered RDIMM", {}),
                 ("ram", "Corsair Vengeance 64GB (2x32GB) DDR4 3200", {"Error Correction": "Yes"}),
                 ("gpu", "EVGA GeForce RTX 3060 Ti 8GB", {}),
                 ("gpu", "MSI GeForce RTX 3060 12GB Ventus", {"Memory Size": "8 GB"}),
                 ("cpu", "AMD Ryzen 5 5600G 6-Core AM4", {}),
                 ("psu", "Corsair RM850x 850W Gold PSU only - no cables", {}),
                 ("motherboard", "ASUS Prime X570-Pro bent pins", {})]
        for comp, title, specs in traps:
            self.assertEqual(trap(comp, title, specs)["status"], "reject", title)

    def test_live_deceptions_rejected(self):
        """Listings seen live on 2026-10-28."""
        fake = trap("cooler", "Deep Cool AK400 CPU Cooler Compatibilities In Description", {"Brand": "Vetroo", "Model": "V5"})
        self.assertEqual(fake["status"], "reject")
        no_cables = trap("psu", "Thermaltake ToughPower GF1 80 Plus Gold 850W Fully Modular",
                         {"Seller Notes": "Comes as pictured, only has power cable for board."})
        self.assertEqual(no_cables["status"], "reject")
        paymore = trap("psu", "Corsair RM850 75-001938 80 Plus Gold 850W Fully Modular Power Supply", {},
                       desc="Items included in this sale: Corsair RM850 *Please note, if it is not listed it is not "
                            "included - such as power cables or other accessories.")
        self.assertEqual(paymore["status"], "reject")
        partial = trap("psu", "Corsair RM850x 850W Fully Modular PSU (RPS0110) - Partial Cable Set", {})
        self.assertEqual(partial["status"], "reject")

    def test_psu_with_cables_passes(self):
        ok = trap("psu", "Corsair RM850x 850W 80+ Gold Fully Modular ATX Power Supply w/ Cables",
                  {"Maximum Wattage": "850W"}, desc="Tested, includes all original modular cables.")
        self.assertEqual(ok["status"], "pass", ok["reject_reasons"] + ok["review_notes"])

    def test_market_check_only_for_same_model(self):
        mkt = {"n": 50, "p25": 146.0, "median": 171.5, "p75": 190.0}
        rec = E.assess_listing("168813752651", raw_item(title="MSI B550-A PRO AMD Socket AM4 DDR4 ATX Motherboard",
                                                        bin_text="US $84.99", specs={}), {"text": "Tested working"})
        r = J.assess(JOB, "motherboard", rec, mkt, 55)
        self.assertFalse(any("sold median" in n for n in r["review_notes"]), r["review_notes"])

    def test_title_spec_contradiction_goes_to_review(self):
        r = trap("gpu", "MSI GeForce RTX 3060 Ventus 2X 8G OC 12GB", {"Memory Size": "12 GB"})
        self.assertEqual(r["status"], "review")

    def test_genuine_listing_passes(self):
        r = trap("ram", "Corsair Vengeance LPX 64GB (2x32GB) DDR4 3200 Non-ECC Desktop",
                 {"Number of Modules": "2", "Capacity per Module": "32 GB", "Error Correction": "No", "Type": "DDR4"})
        self.assertEqual(r["status"], "pass", r["reject_reasons"] + r["review_notes"])


class BuildTest(unittest.TestCase):
    def test_incomplete_build_reports_missing_and_no_budget_claim(self):
        ram = trap("ram", "Corsair Vengeance LPX 64GB (2x32GB) DDR4 3200 Non-ECC Desktop", {"Number of Modules": "2"})
        builds = J.make_builds(JOB, [ram])
        self.assertFalse(builds["cheapest"]["complete"])
        self.assertIn("gpu", builds["cheapest"]["missing"])
        self.assertIsNone(builds["cheapest"]["budget_gap"])

    def test_market_quantiles(self):
        sold = [{"price": p, "shipping": 0} for p in (200, 220, 240, 260, 280)]
        self.assertEqual(J.market(sold)["median"], 240)
        self.assertIsNone(J.market(sold[:2]))


class KnowledgeTest(unittest.TestCase):
    def test_module_check_clean(self):
        errors, _ = module_kit.check(module_kit.load(MOD))
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()


import builds as BLD  # noqa: E402
import report_v2 as RV2  # noqa: E402

JOB15 = json.loads((MOD / "jobs" / "llm-workstation-1500.json").read_text(encoding="utf-8"))


def _rec(comp, item_id, title, landed, catalog=None, specs=None, status="pass", trust=80):
    return {"component": comp, "item_id": item_id, "url": f"https://www.ebay.com/itm/{item_id}", "title": title, "landed_cost": landed,
            "price": landed, "shipping_to_zip": 0.0, "status": status, "trust": {"score": trust, "why": [], "reject": []},
            "catalog": catalog, "specs": specs or {}, "review_notes": [], "reject_reasons": [], "description_excerpt": "",
            "condition": "Used", "return_days": 30, "evidence": {"fetched_at": "2026-10-28T00:00:00"}}


class BuildComposerTest(unittest.TestCase):
    def setUp(self):
        cat = JOB15["catalog"]
        self.board = _rec("motherboard", "b1", "MSI B550-A PRO", 90.94, {"name": "MSI B550-A PRO", **cat["boards"]["MSI B550-A PRO"]})
        self.parts = {"gpu": _rec("gpu", "g1", "Gigabyte RTX 3060 Gaming OC 12GB", 369.15, {"name": "x", **cat["gpus"]["Gigabyte RTX 3060 GAMING OC 12G"]}),
                      "cpu": _rec("cpu", "c1", "AMD Ryzen 7 3700X", 90.94), "motherboard": self.board,
                      "ram": _rec("ram", "r1", "64GB DDR4", 393.36), "psu": _rec("psu", "p1", "Corsair RM850x 850W", 83.37),
                      "case": _rec("case", "k1", "Lancool 216", 61.31, {"name": "x", **cat["cases"]["Lian Li LANCOOL 216"]}),
                      "cooler": _rec("cooler", "o1", "AK400", 21.22, {"name": "x", **cat["coolers"]["DeepCool AK400"]}),
                      "ssd": _rec("ssd", "s1", "1TB NVMe", 121.79)}

    def rows(self, parts):
        return {r["check"]: r for r in BLD.compat(JOB15, parts)}

    def test_b550a_pro_second_slot_needs_nvme_in_m2_1(self):
        r = self.rows(self.parts)["Second GPU slot works with the NVMe installed"]
        self.assertEqual(r["result"], BLD.PASS)
        self.assertIn("M2_1", r["detail"])
        self.assertIn("disables PCI_E3", r["detail"])

    def test_two_slot_card_fits_7_slot_case_with_gap(self):
        rows = self.rows(self.parts)
        self.assertEqual(rows["Second GPU physically fits"]["result"], BLD.PASS)
        self.assertEqual(rows["Air gap between the two GPUs"]["result"], BLD.PASS)

    def test_thick_card_touches(self):
        p = dict(self.parts)
        p["gpu"] = dict(p["gpu"], catalog={"name": "x", "length_mm": 300, "slots": 2.7})
        self.assertEqual(self.rows(p)["Air gap between the two GPUs"]["result"], BLD.FAIL)

    def test_unknown_board_is_unverified_not_pass(self):
        p = dict(self.parts)
        p["motherboard"] = _rec("motherboard", "b2", "Some B550M board", 80.0, None)
        rows = self.rows(p)
        self.assertEqual(rows["Second GPU slot works with the NVMe installed"]["result"], BLD.UNVERIFIED)
        self.assertEqual(rows["Second GPU physically fits"]["result"], BLD.UNVERIFIED)
        self.assertNotEqual(BLD.summarize(BLD.compat(JOB15, p))["dual_ready"], "YES")

    def test_totals_are_checked(self):
        res = {"budget": 1500, "listings": [{"item_id": "a", "landed_cost": 100.0}, {"item_id": "b", "landed_cost": 50.0}],
               "selected": {"x": {"parts": {"gpu": "a", "ssd": "b"}, "total": 150.0, "budget_remaining": 1350.0}}}
        RV2.check_totals(res)
        res["selected"]["x"]["total"] = 151.0
        with self.assertRaises(AssertionError):
            RV2.check_totals(res)


class WorkstationCompatTest(unittest.TestCase):
    def build(self, ws_title, gpu_comp="gpu", desc="", catalog_key=None):
        cat = JOB15["catalog"]
        name, entry = J.catalog_match(cat["workstations"], ws_title)
        ws = _rec("workstation", "w1", ws_title, 300.0, {"name": name, **entry} if name else None)
        ws["description_excerpt"] = desc
        g = _rec(gpu_comp, "g1", "card", 400.0, None)
        return {r["check"]: r for r in BLD.compat(JOB15, {gpu_comp: g, "workstation": ws, "ssd": _rec("ssd", "s1", "ssd", 100.0)})}

    def test_5820_950w_powers_one_and_two_3060(self):
        r = self.build("Dell Precision T5820 Workstation W-2125 64GB 950W")
        self.assertEqual(r["Workstation PSU powers the GPU"]["result"], BLD.PASS)
        self.assertEqual(r["PSU powers two GPUs"]["result"], BLD.PASS)

    def test_5820_425w_has_no_gpu_power(self):
        self.assertEqual(self.build("Dell Precision 5820 W-2123 64GB 425W")["Workstation PSU powers the GPU"]["result"], BLD.FAIL)

    def test_unstated_psu_is_unverified(self):
        self.assertEqual(self.build("Dell Precision 5820 W-2123 64GB 1TB")["Workstation PSU powers the GPU"]["result"], BLD.UNVERIFIED)

    def test_psu_from_description(self):
        r = self.build("Dell Precision 5820 W-2123 64GB", desc="Power supply: 950W 80 Plus Gold")
        self.assertEqual(r["Workstation PSU powers the GPU"]["result"], BLD.PASS)

    def test_z4_750w_six_pin_only_is_unverified_for_8pin_card(self):
        self.assertEqual(self.build("HP Z4 G4 W-2125 64GB 750W")["Workstation PSU powers the GPU"]["result"], BLD.UNVERIFIED)

    def test_p520_690w_two_5060ti_ok_two_3090_not(self):
        self.assertEqual(self.build("Lenovo ThinkStation P520 W-2135 64GB 690W", "gpu_5060ti16")["PSU powers two GPUs"]["result"], BLD.PASS)
        self.assertEqual(self.build("Lenovo ThinkStation P520 W-2135 64GB 690W", "gpu_3090")["PSU powers two GPUs"]["result"], BLD.FAIL)

    def test_dual_cpu_7820_limits_cards_to_150w(self):
        r = self.build("Dell Precision T7820 2x Gold 6130 64GB RAM P4000 950W")
        self.assertEqual(r["PSU powers two GPUs"]["result"], BLD.FAIL)

    def test_two_dimms_only_halves_bandwidth(self):
        r = self.build("Dell Precision 5820 W-2125 64GB (2x32GB) 950W")
        self.assertEqual(r["RAM fills 4 channels (CPU offload bandwidth)"]["result"], BLD.FAIL)


class VariationPricingTest(unittest.TestCase):
    """Multi-option listings are priced at the cheapest in-stock option that meets the requirement (live case 2026-10-28)."""
    def setUp(self):
        E.set_variation_require(r"\b(64|96|128|192|256)\s?GB(?!\s*(SSD|NVMe|HDD|M\.2|storage))")

    def tearDown(self):
        E.set_variation_require(None)

    def msku(self, variations):
        return raw_item(title="Dell Precision 5820 i7-7800X 64GB RAM 1TB SSD", bin_text="US $279.88", msku={"variations": variations})

    def test_ssd_menu_is_not_ram(self):
        v = [{"variation_id": "1", "price": 279.88, "out_of_stock": False, "options": ["i7-7800X", "8 GB", "128 GB"],
              "labeled": [{"menu": "CPU", "value": "i7-7800X"}, {"menu": "RAM Size", "value": "8 GB"}, {"menu": "SSD Capacity", "value": "128 GB"}]},
             {"variation_id": "2", "price": 449.88, "out_of_stock": False, "options": ["i7-7800X", "64 GB", "1TB"],
              "labeled": [{"menu": "CPU", "value": "i7-7800X"}, {"menu": "RAM Size", "value": "64 GB"}, {"menu": "SSD Capacity", "value": "1TB"}]}]
        rec = E.assess_listing("128147436297", self.msku(v), {})
        self.assertEqual(rec["price"], 449.88)
        self.assertEqual(rec["chosen_option"]["variation_id"], "2")

    def test_out_of_stock_title_config_has_no_price(self):
        v = [{"variation_id": "1", "price": 208.88, "out_of_stock": False, "options": ["W-2123, NO RAM , NO SSD, NO GPU"], "labeled": []},
             {"variation_id": "2", "price": 549.88, "out_of_stock": True, "options": ["W-2235, 64GB RAM, 1TB SSD, K2200 GPU"], "labeled": []}]
        rec = E.assess_listing("128147436297", self.msku(v), {})
        self.assertIsNone(rec["price"])
        self.assertTrue(any("out of stock" in m for m in rec["mismatches"]))


import ebay_watch as W  # noqa: E402


class WatchTest(unittest.TestCase):
    Q = {"query": "rtx 3060 12gb", "max_price": 330, "require": ["3060", r"12\s?GB"], "reject": [r"\bTi\b"], "min_feedback": 25}

    def item(self, iid, title="MSI RTX 3060 12GB Ventus", price=300.0, ship=0.0, **kw):
        return {"id": iid, "url": f"https://www.ebay.com/itm/{iid}", "title": title, "price": price, "shipping": ship,
                "condition": "Pre-Owned", "feedback_count": 500, "feedback_pct": 99.9, "seller": "s", "excluded": None, **kw}

    def test_baseline_then_new_and_price_drop(self):
        st = {"items": {}, "cycles": 0}
        self.assertEqual(W.diff(st, "q", self.Q, [self.item("1")], baseline=True), [])   # first cycle: record only
        ev = W.diff(st, "q", self.Q, [self.item("1", price=290.0), self.item("2", price=320.0)], baseline=False)
        self.assertEqual(sorted(e["type"] for e in ev), ["NEW", "PRICE DROP"])
        drop = next(e for e in ev if e["type"] == "PRICE DROP")
        self.assertEqual((drop["was"], drop["total"]), (300.0, 290.0))

    def test_filters(self):
        self.assertIsNotNone(W.matches(self.Q, self.item("3", price=400.0)))                   # over max price
        self.assertIsNotNone(W.matches(self.Q, self.item("4", title="RTX 3060 Ti 8GB")))       # wrong model
        self.assertIsNotNone(W.matches(self.Q, self.item("5", excluded="title says 'for parts'")))
        self.assertIsNotNone(W.matches(self.Q, self.item("6", feedback_count=3)))
        self.assertIsNone(W.matches(self.Q, self.item("7")))

    def test_gone_items_are_marked(self):
        st = {"items": {}, "cycles": 0}
        W.diff(st, "q", self.Q, [self.item("1")], baseline=True)
        W.diff(st, "q", self.Q, [], baseline=False)
        self.assertIn("gone_since", st["items"]["1"])

    def test_watch_file_loads_with_minimum_interval(self):
        w = W.load_watch(MOD / "watches" / "gpus.json")
        self.assertGreaterEqual(w["interval_minutes"], W.MIN_INTERVAL_MIN)
        self.assertTrue(w["queries"])
