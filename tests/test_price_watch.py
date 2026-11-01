"""Amazon card parsing (cards captured live 2026-10-28) and the cross-store price watcher (fake searches)."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "@modules", ROOT / "@modules" / "ebay", ROOT / "@modules" / "amazon", ROOT / "@modules" / "price_watch"):
    sys.path.insert(0, str(p))

import amazon_search as AZ  # noqa: E402
import price_watch as PW  # noqa: E402

WATCH = json.loads((ROOT / "@modules" / "price_watch" / "watches" / "llm-parts.json").read_text(encoding="utf-8"))
P520, G24, G12 = "Lenovo P520 (64GB+)", "RTX 24GB ($450-$600)", "RTX 12GB ($200-$400)"

# Cards as CARDS_JS returns them (fields from the live probe of "rtx 3060 12gb" and the $200-$400 range).
MSI = {"asin": "B0H4X2M3NY", "title": "msi Gaming GeForce RTX 3060 Ventus 2X 12G OC V1 Graphics Card - 15 Gbps GDRR6",
       "price": "$464.99", "list_price": None, "delivery": "Tomorrow FREE delivery Tomorrow, Oct 29",
       "secondary": "More Buying Choices $439.45(12+ used & new offers)", "rating": "4.6 out of 5 stars",
       "reviews": "29 ratings", "lines": ["Compare", "msi Gaming GeForce RTX 3060 Ventus 2X 12G OC V1", "300+ bought in past month", "$464.99"]}
ASUS = {"asin": "B0985X2YR1", "title": "ASUS Dual GeForce RTX 3060 V2 12GB GDDR6 OC Edition Gaming Graphics Card",
        "price": "$449.99", "list_price": "$499.99", "delivery": "FREE delivery Wed, Nov 4 Only 7 left in stock - order soon.",
        "secondary": None, "rating": "4.7 out of 5 stars", "reviews": "2,969 ratings", "lines": ["Only 7 left in stock - order soon."]}
RENEWED = {"asin": "B081QVXLF4", "title": "Amazon Renewed ASUS GeForce RTX 2060 6GB Phoenix Fan Edition VR Ready HDMI",
           "price": "$229.97", "delivery": "FREE delivery Wed, Nov 4 Or fastest delivery Oct 30 - Nov 3 Only 1 left in stock",
           "rating": "4.3 out of 5 stars", "reviews": "38 ratings", "lines": []}
NO_OFFER = {"asin": "B07TDN1SC5", "title": "GIGABYTE ZOTAC Gaming GeForce RTX 2060 6GB GDDR6", "price": None,
            "delivery": None, "rating": "4.6 out of 5 stars", "reviews": "679 ratings", "lines": ["See options"]}
PAID_SHIP = {"asin": "B0H2WTG32H", "title": "VBESTLIFE RTX 3060 Graphics Card, 6GB GDDR6 192Bit GPU, PCIe 3.0 x16",
             "price": "$334.61", "delivery": "$9.99 delivery Nov 24 - Dec 4", "lines": []}
OOS = {"asin": "B0D6W2NMJL", "title": "Lenovo Thinkstation P8, AMD Threadripper Pro 7975Wx", "price": "$10,389.00",
       "delivery": "FREE delivery for Prime members Temporarily out of stock.", "lines": []}


class AmazonCards(unittest.TestCase):
    def test_price_shipping_offers(self):
        it = AZ.parse_card(MSI, tax=0.07)
        self.assertEqual((it["price"], it["shipping"], it["availability"], it["condition"]), (464.99, 0.0, "in_stock", "New"))
        self.assertEqual(it["offers_from"]["price"], 439.45)
        self.assertEqual((it["rating"], it["reviews"], it["bought_past_month"]), (4.6, 29, "300+"))
        self.assertEqual(it["landed_estimate"], round(464.99 * 1.07, 2))
        self.assertEqual(it["url"], "https://www.amazon.com/dp/B0H4X2M3NY")

    def test_list_price_is_never_the_price(self):
        it = AZ.parse_card(ASUS)
        self.assertEqual((it["price"], it["list_price"], it["stock_left"], it["reviews"]), (449.99, 499.99, 7, 2969))

    def test_renewed_no_offer_paid_shipping_out_of_stock(self):
        self.assertEqual(AZ.parse_card(RENEWED)["condition"], "Renewed")
        no = AZ.parse_card(NO_OFFER)
        self.assertEqual((no["availability"], no["price"], no["landed_estimate"]), ("no_offer", None, None))
        self.assertEqual(AZ.parse_card(PAID_SHIP)["shipping"], 9.99)
        oos = AZ.parse_card(OOS)
        self.assertEqual((oos["availability"], oos["available"], oos["prime_only_free"]), ("out_of_stock", False, True))

    def test_unknown_shipping_stays_unknown(self):
        it = AZ.parse_card({**MSI, "delivery": "Delivery date unknown"})
        self.assertIsNone(it["shipping"])
        self.assertIsNone(it["landed_estimate"])

    def test_search_url_and_scripts_render(self):
        self.assertEqual(AZ.search_url("rtx 3060 12gb", 200, 400, 2),
                         "https://www.amazon.com/s?k=rtx+3060+12gb&s=price-asc-rank&rh=p_36%3A20000-40000&page=2")
        for script in (AZ.CARDS_JS, AZ.STATE_JS):
            self.assertNotIn("@@", AZ.MODULE.js(script))

    def test_sponsored_disclaimer_is_not_the_title(self):
        live = ("SponsoredSponsored You’re seeing this ad based on the product’s relevance to your search query."
                "Leave ad feedback PolarPro - Chroma VND/PL - McKinnon Series - 2-5 Stops - 82mm")
        it = AZ.parse_card({**MSI, "title": live})
        self.assertEqual((it["title"], it["sponsored"]), ("PolarPro - Chroma VND/PL - McKinnon Series - 2-5 Stops - 82mm", True))
        spaced = {**MSI, "title": "Sponsored You’re seeing this ad based on the product’s relevance to your search query. "
                                  "Leave ad feedback GIGABYTE GeForce RTX 3060 Gaming OC 12G"}
        self.assertEqual(AZ.parse_card(spaced)["title"], "GIGABYTE GeForce RTX 3060 Gaming OC 12G")
        self.assertFalse(AZ.parse_card(MSI)["sponsored"])

    def test_refine_the_url_the_search_box_produced(self):
        typed = "https://www.amazon.com/s?k=rtx+3060+12gb&crid=2X&sprefix=rtx+3060%2Caps%2C140&ref=nb_sb_noss_1&page=3"
        self.assertEqual(AZ.refine_url(typed, 200, 400),
                         "https://www.amazon.com/s?k=rtx+3060+12gb&crid=2X&sprefix=rtx+3060%2Caps%2C140&ref=nb_sb_noss_1"
                         "&s=price-asc-rank&rh=p_36%3A20000-40000")
        self.assertEqual(AZ.query_of(typed), "rtx 3060 12gb")
        self.assertIsNone(AZ.query_of("https://www.amazon.com/dp/B0H4X2M3NY"))
        self.assertEqual(AZ.MODULE.locators["search_submit"]["css"], "#nav-search-submit-button")
        self.assertFalse(AZ.MODULE.locators["search_box"]["xpath"].startswith("/html"))


def az(card, **kw):
    return PW.norm_amazon({**AZ.parse_card(card), **kw})


def eb(id_, title, price, ship=0.0, fb=(99.5, 1200), **kw):
    return PW.norm_ebay({"id": id_, "url": f"https://www.ebay.com/itm/{id_}", "title": title, "price": price, "shipping": ship,
                         "condition": "Pre-Owned", "feedback_pct": fb[0], "feedback_count": fb[1], **kw})


class AmazonPersonChecks(unittest.TestCase):
    """2026-10-30: Amazon's 'I am human' checkpoint. The script never ticks it: it tells the person, waits, continues."""

    def session(self, states):
        s = AZ.AmazonSession(None, Path(tempfile.mkdtemp()))
        seq = iter(states)

        async def state():
            return next(seq)

        async def quiet(msg):
            self.badges.append(msg)
        s._state, s.feedback = state, quiet
        s.on_person_needed = self.told.append
        return s

    def setUp(self):
        self.badges, self.told = [], []
        for p in (mock.patch.object(AZ.module_kit, "bring_to_front", mock.AsyncMock()),
                  mock.patch.object(AZ.asyncio, "sleep", mock.AsyncMock()),
                  mock.patch.dict(AZ.S, {"human_wait_s": 30})):
            p.start()
        self.addCleanup(mock.patch.stopall)

    def test_what_needs_the_person(self):
        self.assertEqual(AZ.needs_person({"human": True}), "an 'I am human' check")
        self.assertEqual(AZ.needs_person({"captcha": True}), "a robot check")
        self.assertEqual(AZ.needs_person({"signin": True}), "a sign-in page")
        self.assertIsNone(AZ.needs_person({"url": "https://www.amazon.com/s?k=x"}))

    def test_waits_for_the_person_then_continues(self):
        s = self.session([{"human": True, "url": "u"}, None, {"url": "https://www.amazon.com/s?k=rtx"}])
        st = asyncio.run(s.wait_for_person({"human": True, "url": "https://www.amazon.com/checkpoint"}))
        self.assertEqual(st["url"], "https://www.amazon.com/s?k=rtx")
        self.assertIn("I am human", self.told[0])
        self.assertIn("completed by you", self.badges[-1])

    def test_not_completed_in_time_is_blocked(self):
        s = self.session([{"human": True, "url": "u"}] * 100)
        with mock.patch.object(asyncio.get_event_loop_policy().__class__, "__init__", lambda *a: None):
            pass
        with self.assertRaises(AZ.Blocked):
            with mock.patch.dict(AZ.S, {"human_wait_s": 0}):
                asyncio.run(s.wait_for_person({"human": True, "url": "https://www.amazon.com/checkpoint"}))

    def test_state_script_and_locator_never_click(self):
        self.assertIn("human_check", AZ.STATE_JS)
        self.assertIn("never clicks", AZ.MODULE.locators["human_check"]["description"])
        src = Path(AZ.__file__).read_text(encoding="utf-8")
        self.assertNotIn("continueCheck", src)  # the checkbox is never targeted by the script


class Matching(unittest.TestCase):
    def rules(self, product, store):
        return PW.rules_for(WATCH, WATCH["products"][product], store)

    def test_p520_rules(self):
        r = self.rules(P520, "ebay")
        self.assertIsNone(PW.matches(r, eb("307174416566", "Lenovo ThinkStation P520 Workstation W-2135 64GB RAM 512GB K620 WIN 11 PRO", 479.99)))
        self.assertIsNotNone(PW.matches(r, eb("1", "Lenovo ThinkStation P520c W-2135 64GB", 400)))  # P520 and the P520c reject
        self.assertIn("lacks", PW.matches(r, eb("2", "Lenovo ThinkStation P520 W-2135 32GB RAM", 300)))
        self.assertIn("> max", PW.matches(r, eb("3", "Lenovo ThinkStation P520 W-2245 128GB", 640, 25)))
        self.assertIn("ratings", PW.matches(r, eb("4", "Lenovo ThinkStation P520 64GB", 400, fb=(100, 10))))
        self.assertIn("box only", PW.matches(r, eb("5", "Lenovo ThinkStation P520 64GB box only", 400)))
        self.assertIn("auction", PW.matches(r, eb("6", "Lenovo ThinkStation P520 64GB", 400, auction=True)))

    def test_12gb_rules_on_live_amazon_titles(self):
        r = self.rules(G12, "amazon")
        self.assertIn("> max", PW.matches(r, az(MSI)))  # a real 12GB card, but $464.99 is over the $400 ceiling
        self.assertIsNone(PW.matches(r, az({**MSI, "price": "$379.00"})))
        self.assertIn("lacks", PW.matches(r, az(RENEWED)))  # 2060 6GB
        self.assertIsNotNone(PW.matches(r, az(PAID_SHIP)))  # "RTX 3060 ... 6GB"
        self.assertIn("Ti", PW.matches(r, az({**MSI, "title": "MSI RTX 3060 Ti 12GB", "price": "$300.00"})))
        self.assertIn("not available", PW.matches(r, az({**MSI, "price": "$300.00", "delivery": "Temporarily out of stock."})))
        pc = az({**MSI, "title": "STGAubron Gaming PC Desktop,Core I7,RTX 3060 12G,32G RAM,1TB SSD", "price": "$399.00"})
        self.assertIn("title matches", PW.matches(r, pc))

    def test_live_cycle_mistakes(self):
        """Titles the first live cycle (2026-10-28) got wrong."""
        r = self.rules(P520, "amazon")
        for ram in ("A-Tech 64GB Kit (2 x 32GB) for Lenovo ThinkStation P520 - DDR4 PC4-21300 2666Mhz ECC",
                    "64GB Memory for Lenovo ThinkStation P520 DDR4 2666MHz ECC LRDIMM."):
            self.assertIn("title matches", PW.matches(r, az({**MSI, "title": ram, "price": "$500.00"})), ram)
        # a P520 sold without a GPU is fine here: the GPU is bought separately
        no_gpu = eb("12", "Lenovo ThinkStation P520 Xeon W-2135 64GB RAM No GPU", 380, excluded="title says 'No GPU'")
        rb = self.rules(P520, "ebay")
        self.assertIsNone(PW.matches(rb, no_gpu))
        self.assertTrue(any("allowed" in n for n in PW.notes_for(rb, no_gpu)))
        self.assertIsNotNone(PW.matches(self.rules(G12, "ebay"), eb("13", "RTX 3060 12GB No GPU", 300, excluded="title says 'No GPU'")))
        modded = eb("14", "ASUS Nvidia GeForce RTX 3060 12GB Graphics Card GPU PC (LHR) (MODDED FAN)", 299.97)
        r12 = self.rules(G12, "ebay")
        self.assertIsNone(PW.matches(r12, modded))
        self.assertIn("CHECK: title says 'MODDED'", PW.notes_for(r12, modded))

    def test_watch_regexes_have_no_control_characters(self):
        """A shell heredoc once turned \\b into backspace characters in a JSON file."""
        for f in (ROOT / "@modules" / "price_watch" / "watches").glob("*.json"):
            text = f.read_text(encoding="utf-8")
            self.assertFalse([c for c in text if ord(c) < 32 and c not in "\n\r\t"], f.name)
            self.assertNotIn("\\u0008", text, f.name)

    def test_24gb_rules(self):
        r = self.rules(G24, "ebay")
        self.assertIsNone(PW.matches(r, eb("7", "NVIDIA GeForce RTX 3090 Founders Edition 24GB GDDR6X", 560, 20)))
        fan = eb("8", "Fan Video Card for RTX 3090 24GB GamingPro OC Replacement Cooling Fan", 52.38)
        self.assertIn("title matches", PW.matches(r, fan))
        self.assertIsNotNone(PW.matches(r, eb("9", "NVIDIA RTX A6000 48GB", 590)))
        self.assertIn("title matches", PW.matches(r, eb("9b", "RTX 3090 / A6000 48GB lot", 590)))
        self.assertIn("title matches", PW.matches(r, eb("10", "RTX 3090 NVLink bridge 4-slot", 80)))
        self.assertIn("CHECK", " ".join(PW.notes_for(r, eb("7", "RTX 3090 24GB", 560, 20))))


class Cycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.state = {"items": {}, "cycles": 0}
        self.w = {**WATCH, "interval_minutes": 10}
        self.results, self.auctions, self.calls = {}, {}, []
        self.alerts = mock.patch.object(PW, "alert").start()
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(self.tmp.cleanup)

    async def search(self, store, query, lo, hi, pages, kind="bin"):
        self.calls.append((store, query, kind, lo, hi))
        src = self.auctions if kind == "auction" else self.results
        return [dict(i) for i in src.get((store, query), [])]

    def run_cycle(self):
        return asyncio.run(PW.cycle(self.search, self.w, self.state, self.d))

    def test_baseline_then_new_drop_back_in_stock_and_compare(self):
        p520_ebay = eb("307174416566", "Lenovo ThinkStation P520 W-2135 64GB RAM 512GB K620", 479.99)
        card_az = az({**ASUS, "price": "$389.00"})
        self.results = {("ebay", "lenovo thinkstation p520 64gb"): [p520_ebay],
                        ("amazon", "rtx 3060 12gb"): [card_az]}
        self.assertEqual(self.run_cycle(), [])  # baseline: records, alerts nothing
        self.alerts.assert_not_called()

        cheaper_ebay = eb("111", "Gigabyte RTX 3060 Gaming OC 12GB", 330, 10)
        self.results[("ebay", "rtx 3060 12gb")] = [cheaper_ebay]
        self.results[("ebay", "lenovo thinkstation p520 64gb")] = [{**p520_ebay, "price": 449.99}]
        events = self.run_cycle()
        kinds = {(e["type"], e["store"], e["id"]) for e in events}
        self.assertIn(("NEW", "ebay", "111"), kinds)
        self.assertIn(("PRICE DROP", "ebay", "307174416566"), kinds)
        drop = next(e for e in events if e["type"] == "PRICE DROP")
        self.assertEqual((drop["total"], drop["was"]), (449.99, 479.99))
        new = next(e for e in events if e["type"] == "NEW")
        self.assertTrue(new.get("cheapest"))  # $340 on eBay beats $389 on Amazon
        self.assertEqual(self.alerts.call_count, 2)

        cmp_ = json.loads((self.d / "compare.json").read_text(encoding="utf-8"))
        g12 = next(p for p in cmp_["products"] if p["product"] == G12)
        self.assertEqual((g12["best"]["store"], g12["best"]["total"]), ("ebay", 340.0))
        self.assertEqual(set(g12["by_store"]), {"ebay", "amazon"})
        self.assertIn("RTX 12GB", (self.d / "dashboard.html").read_text(encoding="utf-8"))

        # Amazon card goes out of stock, then comes back; eBay listing disappears (sold / ended)
        self.results[("amazon", "rtx 3060 12gb")] = [az({**ASUS, "price": "$389.00", "delivery": "Temporarily out of stock."})]
        self.results[("ebay", "rtx 3060 12gb")] = []
        self.assertEqual([e for e in self.run_cycle() if e["product"] == G12], [])
        gone = self.state["items"][f"{G12}|ebay:111"]
        self.assertIn("gone_since", gone)
        self.results[("amazon", "rtx 3060 12gb")] = [card_az]
        back = [e for e in self.run_cycle() if e["product"] == G12]
        self.assertEqual([(e["type"], e["store"]) for e in back], [("BACK IN STOCK", "amazon")])
        lines = (self.d / "alerts.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3)

    def test_failed_search_does_not_mark_offers_gone(self):
        self.results = {("ebay", "lenovo thinkstation p520 64gb"): [eb("307174416566", "Lenovo ThinkStation P520 64GB", 479.99)]}
        self.run_cycle()

        async def failing(store, query, lo, hi, pages, kind="bin"):
            raise RuntimeError("page script failed")
        asyncio.run(PW.cycle(failing, self.w, self.state, self.d))
        self.assertNotIn("gone_since", self.state["items"][f"{P520}|ebay:307174416566"])

    def test_blocked_store_is_skipped_and_the_others_continue(self):
        """2026-10-30: Amazon's "I am human" checkpoint the person didn't tick must not stop eBay / Best Buy."""
        class Blocked(RuntimeError):
            pass
        calls = []

        async def search(store, query, lo, hi, pages, kind="bin"):
            calls.append(store)
            if store == "amazon":
                raise Blocked("human check (I am human) at https://www.amazon.com/checkpoint - not cleared")
            return [eb("307174416566", "Lenovo ThinkStation P520 W-2135 64GB RAM 512GB K620", 479.99)] if query == "lenovo thinkstation p520 64gb" else []
        asyncio.run(PW.cycle(search, {**self.w, "shuffle": False}, self.state, self.d))
        self.assertEqual(calls.count("amazon"), 1)  # tried once, then skipped for the rest of the cycle
        self.assertIn("ebay", calls)
        self.assertIn("amazon", self.state["blocked"])
        self.assertTrue(any("amazon needs you" in str(c.args[0]) for c in self.alerts.call_args_list))
        self.assertIn("Skipped this cycle", (self.d / "dashboard.html").read_text(encoding="utf-8"))
        self.assertTrue(self.state["items"][f"{P520}|ebay:307174416566"]["matches"])

    def test_human_pacing(self):
        import random
        import module_kit
        rng = random.Random(7)
        s = {"pause_seconds": [4.0, 11.0], "long_pause": {"chance": 0.12, "seconds": [15, 40]}}
        waits = [module_kit.human_delay(s, rng) for _ in range(2000)]
        self.assertTrue(all(4.0 <= x <= 11.0 or 15 <= x <= 40 for x in waits))
        longs = sum(x >= 15 for x in waits) / len(waits)
        self.assertTrue(0.08 < longs < 0.16, longs)  # some long reads, not many
        self.assertGreater(len(set(waits)), 1990)  # no fixed beat
        self.assertGreater(max(waits[:50]) - min(waits[:50]), 5)
        gaps = [PW.next_wait({"interval_minutes": 10}, rng) for _ in range(500)]
        self.assertTrue(all(480 <= g <= 720 for g in gaps))
        self.assertEqual(PW.next_wait({"interval_minutes": 5, "interval_jitter": 0.5}, random.Random(1)) >= 300, True)

    def test_wheel_bursts_look_like_a_person(self):
        import random
        import module_kit
        rng = random.Random(3)
        bursts = [module_kit.wheel_burst(rng) for _ in range(1000)]
        down = [b for b in bursts if b["delta_y"] > 0]
        up = [b for b in bursts if b["delta_y"] < 0]
        self.assertTrue(all(3 <= b["notches"] <= 8 and 90 <= b["delta_y"] <= 125 for b in down))
        self.assertTrue(all(b["notches"] == 1 for b in up))
        self.assertTrue(0.07 < len(up) / len(bursts) < 0.17)  # sometimes back up a notch
        self.assertTrue(all(0.4 <= b["pause"] <= 1.9 for b in bursts))

    def test_human_scroll_uses_wheel_until_target_then_falls_back(self):
        import module_kit
        from types import SimpleNamespace as NS

        class FakeBrowser:
            def __init__(self, wheel=True):
                self.y, self.wheel, self.sent = 0, wheel, []

            async def send_command(self, cmd):
                self.sent.append(cmd.action if isinstance(cmd.action, str) else cmd.action.value)
                if self.sent[-1] == "wheel":
                    if not self.wheel:
                        return NS(status="failure", error="Unknown action: wheel", data=None)
                    self.y = max(0, self.y + cmd.params["notches"] * cmd.params["delta_y"])
                    return NS(status="success", data=NS(extracted_data={"scrollY": self.y}))
                script = cmd.params["script"]
                if "innerWidth" in script:
                    out = json.dumps({"w": 1400, "h": 900})
                elif script.startswith("(() => { scrollBy"):
                    self.y += int(script.split("scrollBy(0, ")[1].split(")")[0])
                    out = 1
                elif script == "TARGET":
                    out = self.y >= 3000
                else:
                    out = json.dumps(self.y)
                return NS(status="success", data=NS(extracted_data={"result": out}))

        with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
            b = FakeBrowser()
            r = asyncio.run(module_kit.human_scroll(b, "TARGET"))
            self.assertEqual((r["via"], r["end"]), ("wheel", "target in view"))
            self.assertGreaterEqual(b.y, 3000)
            self.assertGreater(b.sent.count("wheel"), 3)  # several flicks, not one jump
            old = FakeBrowser(wheel=False)
            r = asyncio.run(module_kit.human_scroll(old, "TARGET"))
            self.assertEqual((r["via"], r["end"]), ("script", "target in view"))
            self.assertEqual(old.sent.count("wheel"), 1)  # tried once, then the script fallback

    def test_type_and_submit_never_clicks_blind(self):
        import module_kit
        from types import SimpleNamespace as NS
        import ebay_search as EB

        class Page:
            def __init__(self, value, covered=False):
                self.value, self.covered, self.clicked, self.typed = value, covered, [], []

            async def send_command(self, cmd):
                act = cmd.action if isinstance(cmd.action, str) else cmd.action.value
                if act == "type_cdp":
                    self.typed.append(cmd.params)
                    return NS(status="success", error=None, data=None)
                if act in ("activate_tab", "wheel"):
                    return NS(status="success", error=None, data=None)
                script = cmd.params["script"]
                if "in_view" in script:
                    return NS(status="success", data=NS(extracted_data={"result": json.dumps({"in_view": True, "top": 10, "w": 1400, "h": 800})}))
                if "location.href" in script and "elementFromPoint" not in script:
                    return NS(status="success", data=NS(extracted_data={"result": json.dumps("https://www.ebay.com/")}))
                return NS(status="success", data=NS(extracted_data={"result": json.dumps(
                    {"value": self.value, "x": 1327, "y": 71, "size": 7392, "covered": self.covered, "hit": "SPAN.x"})}))

            async def click_cdp(self, x, y):
                self.clicked.append((x, y))

        with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
            ok = Page("rtx 3060 12gb")
            asyncio.run(module_kit.type_and_submit(EB.MODULE, ok, "rtx 3060 12gb"))
            self.assertEqual(ok.clicked, [(1327, 71)])
            self.assertEqual(ok.typed[0]["xpath"], "//input[@name='_nkw']")
            self.assertTrue(45 <= ok.typed[0]["delay_per_char"] <= 140)
            for page in (Page("rtx 3060"), Page("rtx 3060 12gb", covered=True)):
                with self.assertRaises(RuntimeError):
                    asyncio.run(module_kit.type_and_submit(EB.MODULE, page, "rtx 3060 12gb"))
                self.assertEqual(page.clicked, [])

    def test_order_is_shuffled_but_report_keeps_watch_order(self):
        seen = []

        async def record(store, query, lo, hi, pages, kind="bin"):
            seen.append((store, query))
            return []
        orders = set()
        for _ in range(6):
            seen.clear()
            asyncio.run(PW.cycle(record, self.w, self.state, self.d))
            orders.add(tuple(seen))
        self.assertGreater(len(orders), 1)
        cmp_ = json.loads((self.d / "compare.json").read_text(encoding="utf-8"))
        self.assertEqual([p["product"] for p in cmp_["products"]], list(WATCH["products"]))

    def test_interval_floor_and_queries_per_store(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({**WATCH, "interval_minutes": 1}, f)
        self.assertEqual(PW.load_watch(Path(f.name))["interval_minutes"], PW.MIN_INTERVAL_MIN)
        Path(f.name).unlink()
        self.assertEqual(PW.queries_for(WATCH["products"][P520], "amazon"), ["lenovo thinkstation p520 64gb"])
        self.assertNotIn("quadro rtx 6000 24gb", PW.queries_for(WATCH["products"][G24], "amazon"))


DEALS = json.loads((ROOT / "@modules" / "price_watch" / "watches" / "build-deals.json").read_text(encoding="utf-8"))
G16, Z4 = "RTX 5060 Ti 16GB", "HP Z4 G4 (64GB+)"


class Deals(unittest.TestCase):
    """The opportunity banner: offers at or under deal_price (build-deals watch, 2026-10-30)."""

    def comp(self, product, offers=(), auctions=()):
        return {"product": product, "offers": list(offers), "auctions": list(auctions)}

    def offer(self, url, total, store="ebay"):
        return {"store": store, "url": url, "title": f"offer {url}", "total": total, "current": {"condition": "New"}, "notes": []}

    def auction(self, url, bid, ship, ends_at):
        return {"url": url, "title": f"auction {url}", "ends_at": ends_at, "history": [],
                "current": {"current_bid": bid, "shipping": ship, "bids": 7, "time_left": "5h", "condition": "Used"}}

    def test_rules_bin_configured_and_auctions(self):
        from datetime import datetime
        at = datetime(2026, 10, 30, 12, 0)
        comps = [self.comp(G16, [self.offer("a", 449.99), self.offer("b", 450.01)],
                           [self.auction("soon", 300, 10, "2026-10-30T20:00"), self.auction("late", 300, 10, "2026-11-03T12:00"),
                            self.auction("high", 445, 10, "2026-10-30T15:00")]),
                 self.comp(Z4)]
        configured = [{"product": Z4, "url": "cfg", "title": "Z4 G4 selectable", "page_price": 400.0, "shipping": 0.0,
                       "options": ["W-2135", "64GB"]},
                      {"product": Z4, "url": "cfg2", "title": "Z4 G4 64GB+1TB", "page_price": 430.0, "shipping": 0.0}]
        d = PW.deals_for(DEALS, comps, configured, at=at)
        self.assertEqual([(x["kind"], x["url"]) for x in d], [("buy", "a"), ("configured", "cfg"), ("auction", "soon")])
        self.assertEqual(d[0]["landed_est"], round(449.99 * 1.07, 2))
        self.assertIn("NOT a price", d[2]["notes"][0])  # a current bid is never presented as a price
        self.assertIn("selected on the page", d[1]["notes"][0])

    def test_alert_once_per_deal_unless_cheaper(self):
        st = {}
        x = {"product": G16, "kind": "buy", "store": "ebay", "url": "https://www.ebay.com/itm/1", "title": "t", "total": 440.0,
             "landed_est": 470.8, "deal_price": 450}
        self.assertEqual([e["type"] for e in PW.deal_events(st, [x])], ["DEAL"])
        self.assertEqual(PW.deal_events(st, [x]), [])
        self.assertEqual(len(PW.deal_events(st, [{**x, "total": 430.0}])), 1)
        self.assertEqual(PW.deal_events(st, [{**x, "kind": "auction", "total": 435.0}]), [])

    def test_configured_prices_must_be_page_confirmed_and_fresh(self):
        from datetime import datetime, timedelta
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            items = [{"url": "ok", "confirmed": True, "checked_at": datetime.now().isoformat()},
                     {"url": "embedded-only", "confirmed": False, "checked_at": datetime.now().isoformat()},
                     {"url": "old", "confirmed": True, "checked_at": (datetime.now() - timedelta(hours=30)).isoformat()}]
            (d / "configured.json").write_text(json.dumps({"items": items}), encoding="utf-8")
            self.assertEqual([x["url"] for x in PW.load_configured(d)], ["ok"])

    def test_cycle_banner_and_baseline_alert(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            card = eb("555", "ZOTAC GAMING GeForce RTX 5060 Ti 16GB Twin Edge OC", 439.0, fb=(99.8, 3000))
            res = {("ebay", "rtx 5060 ti 16gb"): [card]}

            async def search(store, query, lo, hi, pages, kind="bin"):
                return [dict(i) for i in res.get((store, query), [])] if kind == "bin" else []
            with mock.patch.object(PW, "alert") as al:
                st = {"items": {}, "cycles": 0}
                events = asyncio.run(PW.cycle(search, {**DEALS, "shuffle": False}, st, d))
                self.assertEqual([(e["type"], e["id"]) for e in events], [("DEAL", "555")])  # even on the baseline cycle
                al.assert_called_once()
                html = (d / "dashboard.html").read_text(encoding="utf-8")
                self.assertIn("Opportunity: 1 offer(s)", html)
                self.assertIn("class=deal", html)
                self.assertEqual(json.loads((d / "compare.json").read_text(encoding="utf-8"))["deals"][0]["total"], 439.0)
                self.assertEqual(asyncio.run(PW.cycle(search, {**DEALS, "shuffle": False}, st, d)), [])  # not again
                res[("ebay", "rtx 5060 ti 16gb")] = [{**card, "price": 499.0}]
                asyncio.run(PW.cycle(search, {**DEALS, "shuffle": False}, st, d))
                self.assertIn("No opportunity this cycle", (d / "dashboard.html").read_text(encoding="utf-8"))

    def test_out_of_stock_listing_page_drops_the_card(self):
        """2026-10-30: the dashboard showed MSI Vanguard 800020842561 ($617.99) as best; its page says out of stock."""
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            oos = eb("800020842561", "MSI VANGUARD GeForce RTX 5060 Ti 16GB GDDR7", 617.99, fb=(99.6, 1085695))
            ok = eb("860075974010", "ZOTAC NVIDIA GeForce RTX 5060 Ti Twin Edge OC White 16GB", 749.99, fb=(99.1, 141))
            reads = []

            async def search(store, query, lo, hi, pages, kind="bin"):
                return [dict(oos), dict(ok)] if (store, query, kind) == ("ebay", "rtx 5060 ti 16gb", "bin") else []

            async def check(store, kind, item_id):
                reads.append((store, kind, item_id))
                if kind == "listing":
                    return {"available": item_id != "800020842561", "price": {"800020842561": 617.99}.get(item_id, 749.99),
                            "shipping": 0.0, "checked_at": PW.now()}
                return {}
            with mock.patch.object(PW, "alert"):
                st = {"items": {}, "cycles": 0}
                w = {**DEALS, "shuffle": False, "products": {G16: {**DEALS["products"][G16], "max_price": 800,
                                                                    "queries": {"ebay": ["rtx 5060 ti 16gb"]}}}}
                asyncio.run(PW.cycle(search, w, st, d, check=check))
                cmp_ = json.loads((d / "compare.json").read_text(encoding="utf-8"))["products"][0]
                self.assertEqual(cmp_["best"]["total"], 749.99)
                self.assertIn("out_of_stock", st["items"][f"{G16}|ebay:800020842561"]["why_not"])
                self.assertEqual([r[2] for r in reads if r[1] == "listing"], ["800020842561", "860075974010"])
                asyncio.run(PW.cycle(search, w, st, d, check=check))  # fresh page reads are reused
                self.assertEqual(len([r for r in reads if r[1] == "listing"]), 2)

    def test_build_deals_rules(self):
        r = PW.rules_for(DEALS, DEALS["products"][Z4], "ebay")
        self.assertIsNone(PW.matches(r, eb("1", "HP Z4 G4 Workstation Xeon W-2135 64GB RAM 1TB NVMe 1000W", 400)))
        self.assertIsNotNone(PW.matches(r, eb("2", "HP Z4 G4 Workstation W-2125 32GB RAM", 300)))
        self.assertIsNotNone(PW.matches(r, eb("3", "64GB DDR4 RDIMM kit for HP Z4 G4", 90)))
        self.assertIn("price is a range", PW.matches(r, eb("4", "HP Z4 G4 Workstation W-2125 64GB RAM", 269.99, price_is_range=True)))
        self.assertTrue(PW.notes_for(r, eb("5", "HP Z4 G4 W-2125 64GB 465W", 350)))  # 465W supply: no GPU cables
        g = PW.rules_for(DEALS, DEALS["products"][G16], "ebay")
        self.assertIsNone(PW.matches(g, eb("6", "MSI GeForce RTX 5060 Ti 16G VENTUS 2X OC PLUS", 449)))
        self.assertIsNotNone(PW.matches(g, eb("7", "ASUS Dual RTX 5060 Ti 8GB", 300)))
        p = PW.rules_for(DEALS, DEALS["products"]["Lenovo P520 (64GB+)"], "ebay")
        self.assertIsNotNone(PW.matches(p, eb("8", "Lenovo ThinkStation P520c W-2135 64GB", 380)))


import config_check as CC  # noqa: E402


def var(price, oos=False, vid="v", **menus):
    return {"price": price, "out_of_stock": oos, "variation_id": vid, "options": list(menus.values()),
            "labeled": [{"menu": k.replace("_", " "), "value": v} for k, v in menus.items()]}


class ConfigCheck(unittest.TestCase):
    """Selectable listings: the price of the REQUIRED options, never the 'from' price (live option data 2026-10-30)."""

    def test_choose_cheapest_64gb_and_nvme(self):
        # item 385449239010 (P520 built to order): "$589.82" on the search card is the 16/32GB option
        vs = [var(589.82, RAM_Configuration="32GB DDR4", Primary_Storage="256GB NVMe", Graphics_Card="Nvidia Quadro M2000 4GB"),
              var(827.82, vid="64a", RAM_Configuration="64GB DDR4", Primary_Storage="256GB NVMe", Graphics_Card="Nvidia Quadro M2000 4GB"),
              var(927.91, vid="64n", RAM_Configuration="64GB DDR4", Primary_Storage="1TB NVMe", Graphics_Card="Nvidia Quadro M2000 4GB"),
              var(800.00, oos=True, RAM_Configuration="64GB DDR4", Primary_Storage="1TB NVMe", Graphics_Card="None")]
        ch = CC.choose(vs)
        self.assertEqual((ch["any_storage"]["embedded_price"], ch["any_storage"]["variation_id"]), (827.82, "64a"))
        self.assertEqual(ch["with_nvme"]["embedded_price"], 927.91)  # the out-of-stock $800 combination never counts
        self.assertEqual(ch["menus"]["RAM Configuration"], "ram")

    def test_title_says_64gb_but_menu_does_not(self):
        # item 226422183878: title "64GB RAM", RAM menu only 16 / 32 GB
        vs = [var(285, RAM="16 GB", SSD="128GB", GPU_TYPE="NO GPU", CPU="W-2123"), var(350, RAM="32 GB", SSD="128GB", GPU_TYPE="NO GPU", CPU="W-2123")]
        ch = CC.choose(vs)
        self.assertIsNone(ch["any_storage"])
        self.assertEqual(ch["ram_values"], ["16 GB", "32 GB"])

    def test_one_combined_menu_is_read_by_content(self):
        # item 336423364751 (HP Z4 G4, live 2026-10-30): one "Specification" menu; "128GB SSD" is storage, not RAM
        vs = [var(332.88, Specification="Intel W-2123 8GB DDR4 128GB SSD R5-340X"),
              var(269.98, Specification="Intel W-2123 NO RAM No Drive/OS"),
              var(409.99, Specification="Intel W-2123 32GB RAM No Drive/OS"),
              var(549.99, vid="64", Specification="Intel W-2123 64GB RAM No Drive/OS"),
              var(729.99, oos=True, Specification="Intel W-2123 32GB DDR4 1TB SSD P4000")]
        ch = CC.choose(vs)
        self.assertEqual((ch["any_storage"]["embedded_price"], ch["any_storage"]["variation_id"]), (549.99, "64"))
        self.assertIsNone(ch["with_nvme"])
        self.assertIn("64GB RAM", ch["ram_values"])
        # item 236625790642: nothing above 32GB in its combined menu -> not a 64GB machine
        self.assertIsNone(CC.choose([var(617.99, HP_Z4_G4_Workstation="W-2123 32GB DDR4 1TB SSD M4000"),
                                     var(342.88, HP_Z4_G4_Workstation="W-2123 8GB DDR4 128GB SSD R5-340X")])["any_storage"])
        import re as _re
        self.assertIsNone(_re.search(CC.RAM_REQUIRE, "W-2123 8GB DDR4 128GB SSD R5-340X"))
        self.assertTrue(_re.search(CC.RAM_REQUIRE, "Intel W-2123 64GB RAM No Drive/OS"))

    def test_psu_menu_must_reach_the_dual_gpu_wattage(self):
        vs = [var(300, Memory="64GB (4x16GB)", Power_Supply="465W"), var(360, Memory="64GB (4x16GB)", Power_Supply="750W"),
              var(420, Memory="64GB (4x16GB)", Power_Supply="1000W")]
        self.assertEqual(CC.choose(vs, 64, 1000)["any_storage"]["embedded_price"], 420)
        short = CC.choose(vs[:2], 64, 1000)
        self.assertEqual(short["any_storage"]["embedded_price"], 300)
        self.assertIn("1000W", short["psu_short"])

    def test_storage_and_size_parsing(self):
        self.assertTrue(CC.nvme_tb("1TB NVMe M.2 PCIe Gen 3 SSD"))
        self.assertFalse(CC.nvme_tb("1TB SSD"))  # SATA or unknown: not the NVMe the user requires
        self.assertFalse(CC.nvme_tb("256GB NVMe + 1TB HDD"))
        self.assertEqual(CC.gb_of("64GB (4x 16GB) DDR4-2666 ECC RDIMMs"), 64)
        self.assertEqual(CC.gb_of("4x16GB DDR4"), 64)
        self.assertEqual(CC.gb_of("2TB NVMe"), 2000)

    def test_candidates_are_range_priced_listings_of_the_model(self):
        st = {"cycles": 3, "items": {}}

        def put(id_, title, price, rng=True, cyc=3, fb=(99.6, 22300), product=Z4):
            st["items"][f"{product}|ebay:{id_}"] = {"product": product, "store": "ebay", "id": id_, "url": f"https://www.ebay.com/itm/{id_}",
                                                     "title": title, "seen_cycle": cyc,
                                                     "current": {"price": price, "shipping": 0.0, "price_is_range": rng,
                                                                 "feedback_pct": fb[0], "feedback_count": fb[1]}}
        put("236625845642", "HP Z4 G4 Workstation Intel W-2125 64GB RAM No Drive/OS/GPU", 269.99)
        put("2", "HP Z4 G4 Workstation Choose CPU RAM SSD", 199.0)  # no RAM size in the title: still a candidate
        put("3", "HP Z4 G4 Workstation W-2135 64GB", 350.0, rng=False)  # fixed price: priced by the watcher itself
        put("4", "HP Z4 G4 Workstation 64GB", 150.0, cyc=2)  # not seen this cycle
        put("5", "64GB DDR4 RDIMM for HP Z4 G4", 90.0)  # a RAM kit, not a workstation
        put("6", "HP Z4 G4 Workstation 64GB", 210.0, fb=(90.0, 20))  # untrusted seller
        self.assertEqual([c["id"] for c in CC.candidates(DEALS, st)], ["2", "236625845642"])

    def test_wait_visible_reads_the_page_answer(self):
        """2026-10-30: 'document.visibilityState' came back as a bare string, json.loads failed, and the run waited
        forever with the window in front. The script must return JSON, the way _run_script parses it."""
        calls = []

        class FakeKit:
            async def bring_to_front(self, s):
                pass

            async def _run_script(self, s, script):
                calls.append(script)
                raw = {"JSON.stringify(document.visibilityState)": '"visible"'}.get(script, "visible")
                return json.loads(raw)  # what module_kit._run_script does with a string result

        asyncio.run(CC.wait_visible(None, FakeKit(), max_min=0.1))
        self.assertEqual(calls, [CC.VISIBLE_JS])

    def test_merge_keeps_newest_read(self):
        from datetime import datetime, timedelta
        old = [{"url": "a", "page_price": 500, "checked_at": (datetime.now() - timedelta(hours=5)).isoformat()},
               {"url": "b", "page_price": 400, "checked_at": (datetime.now() - timedelta(days=4)).isoformat()}]
        new = [{"url": "a", "page_price": 480, "checked_at": datetime.now().isoformat()}]
        self.assertEqual([(x["url"], x["page_price"]) for x in CC.merge(old, new)], [("a", 480)])


if __name__ == "__main__":
    unittest.main()
