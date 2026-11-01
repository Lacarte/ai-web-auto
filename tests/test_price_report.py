"""The user's 2026-10-28 rules for the price report: eBay North America + the user's condition list (parts only when the
query asks), auctions tracked apart (time left vs current bid vs bids vs watchers), Amazon Prime, the real price of the
selected option, and availability checked on the product page. Page texts are from live probes (scratch/ebay,
scratch/amazon)."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "@modules", ROOT / "@modules" / "ebay", ROOT / "@modules" / "amazon", ROOT / "@modules" / "price_watch", ROOT / "@modules" / "bestbuy"):
    sys.path.insert(0, str(p))

import amazon_search as AZ  # noqa: E402
import ebay_search as E  # noqa: E402
import price_watch as PW  # noqa: E402

WATCH = json.loads((ROOT / "@modules" / "price_watch" / "watches" / "llm-parts.json").read_text(encoding="utf-8"))
P520, G24, G12 = "Lenovo P520 (64GB+)", "RTX 24GB ($450-$600)", "RTX 12GB ($200-$400)"

# auction cards as CARDS_JS lines (live 2026-10-28, "rtx 3090" auctions ending soonest)
AUCTION_TI = ("ASUS TUF Gaming GeForce RTX 3090 Ti OC 24GB GDDR6X RGB HDMI/DP | Opens in a new window or tab | ASUS · NVIDIA GeForce "
              "RTX 3090 · 24 GB | Pre-Owned | $1,475.00 | 8 bids · | Time left | 19h 28m left (Thu, 06:10 PM) | +$87.30 delivery | "
              "Located in United States | chzap-80 100% positive (39)").split(" | ")
AUCTION_MSI = ("HOT AUCTION | MSI GeForce RTX 3090 GAMING X TRIO 24GB GDDR6X Graphics Card | Opens in a new window or tab | MSI · NVIDIA "
               "GeForce RTX 3090 · 24 GB | Pre-Owned | $1,375.00 | 28 bids · | Time left | 1d 19h left (Fri, 05:55 PM) | +$6.83 delivery | "
               "Located in United States | jasu-7353 100% positive (7)").split(" | ")


class EbaySearchRules(unittest.TestCase):
    def test_north_america_and_the_users_conditions(self):
        url = E.search_url("rtx 3060 12gb", 150, 420)
        self.assertIn("LH_PrefLoc=3", url)  # North America (live filter link: 1 US only, 3 North America, 2 Worldwide)
        self.assertIn("LH_ItemCondition=1500%7C1000%7C2000%7C2010%7C3000%7C2020%7C2030&", url)
        self.assertNotIn("7000", url)

    def test_for_parts_only_when_the_query_asks(self):
        self.assertTrue(E.wants_parts("rtx 3090 for parts"))
        self.assertFalse(E.wants_parts("rtx 3090 24gb"))
        self.assertIn("2030%7C7000", E.search_url("rtx 3090 for parts", 100, 300))
        card = {"id": "1", "lines": ["NVIDIA RTX 3090 24GB for parts no display", "For parts or not working", "$200.00",
                                     "Free delivery", "seller 99.5% positive (500)"]}
        self.assertIsNotNone(E.parse_card(card)["excluded"])
        self.assertIsNone(E.parse_card(card, parts_ok=True)["excluded"])

    def test_auction_search_url(self):
        url = E.search_url("rtx 3090", None, 600, auction=True)
        self.assertIn("LH_Auction=1", url)
        self.assertIn("_sop=1", url)  # time: ending soonest
        self.assertNotIn("LH_BIN", url)
        self.assertIn("_udhi=600", url)
        self.assertNotIn("_udlo", url)

    def test_auction_card_current_bid_bids_time_left(self):
        it = E.parse_card({"id": "840049637356", "lines": AUCTION_TI})
        self.assertTrue(it["auction"])
        self.assertEqual((it["current_bid"], it["bids"], it["time_left"], it["ends"], it["shipping"]),
                         (1475.0, 8, "19h 28m left", "Thu, 06:10 PM", 87.3))
        self.assertEqual(it["seconds_left"], 19 * 3600 + 28 * 60)
        self.assertEqual(it["located_in"], "United States")
        self.assertEqual(E.parse_card({"id": "9", "lines": AUCTION_MSI})["seconds_left"], 86400 + 19 * 3600)

    def test_listing_page_watchers(self):
        a = E.parse_auction_page({"bid_price": "US $1,375.00 28 bidsEnds in 1d 19hFriday, 05:55 PM", "bid_count": "28 bids",
                                  "end_time": "Ends in 1d 19hFriday, 05:55 PM",
                                  "watchers": "People want this. 108 people are watching this."})
        self.assertEqual((a["current_bid"], a["bids"], a["watchers"], a["time_left"], a["ends"]),
                         (1375.0, 28, 108, "1d 19h", "Friday, 05:55 PM"))
        quiet = E.parse_auction_page({"bid_price": "US $50.00", "bid_count": "0 bids", "end_time": "Ends in 19h 27mThursday, 06:10 PM"})
        self.assertIsNone(quiet["watchers"])  # no demand signal: unknown, never 0
        self.assertEqual(quiet["seconds_left"], 19 * 3600 + 27 * 60)

    def test_auction_script_renders(self):
        js = E.MODULE.js(E.AUCTION_JS)
        for sel in ('x-bid-count', 'x-end-time', 'x-wtb-signals'):
            self.assertIn(sel, js)
        self.assertNotIn("@@", js)


# product pages as PRODUCT_JS returns them (live 2026-10-28)
PNY_RENEWED = {"url": "https://www.amazon.com/dp/B0BK36KLDD", "ready": "complete", "asin": "B0BK36KLDD",
               "title": "PNY GeForce RTX 3060 12GB XLR8 Gaming Revel Epic-X RGB Single Fan Graphics Card (Renewed)",
               "availability": "Only 1 left in stock - order soon.", "add_to_cart": True, "add_to_cart_used": False, "buy_now": True,
               "see_all": False, "out_of_stock": False, "core_price": "$399.97", "variants": [], "prime": False,
               "delivery": "FREE delivery Wednesday, November 4. Details", "merchant": "Shipper / Seller | Respec Store", "buybox": ""}
MSI_AMAZON = {**PNY_RENEWED, "url": "https://www.amazon.com/dp/B0CTK1SFVD", "asin": "B0CTK1SFVD",
              "title": "msi Gaming RTX 3050 Ventus 2X 6G OC Graphics Card", "availability": "In Stock", "core_price": "$259.97",
              "prime": True, "delivery": "FREE delivery Friday, October 30", "merchant": "Shipper / Seller | Amazon.com"}
USED_ONLY = {**PNY_RENEWED, "url": "https://www.amazon.com/dp/B07QC8RSZB?th=1", "asin": "B07QC8RSZB", "title": "ZOTAC Gaming GeForce RTX 2060, Black",
             "availability": "In stock", "add_to_cart": False, "add_to_cart_used": True, "buy_now": False, "core_price": "$198.22",
             "merchant": None, "delivery": "FREE delivery Monday, November 16. Details",
             "buybox": "Buy Used : $198.22 | FREE delivery Monday, November 16. Details | Used: Very Good | Details | Sold by Atomic Cellular | In stock | Add to cart"}
BATTERY = {**PNY_RENEWED, "url": "https://www.amazon.com/dp/B0FFGCKW2T", "asin": "B0FFGCKW2T",
           "title": "38Wh F62G0 Battery for Dell Inspiron 13 7000", "availability": "In Stock", "core_price": "$25.99", "prime": True,
           "merchant": "Sold by ZAYAUPVL", "variants": [{"asin": "B0FFGCKW2T", "label": "10*2.5*0.35inch F62G0", "price": "$25.99",
                                                         "availability": "In Stock", "prime": True}]}


class CarriedFilters(unittest.TestCase):
    """Live 2026-10-28: eBay's search form carries the previous search's filters into the typed search's URL."""

    def test_only_ebay_tracking_params_survive(self):
        typed = ("https://www.ebay.com/sch/i.html?_nkw=rtx+3060+12gb&_sacat=0&LH_Auction=1&_sop=1&_udhi=400&LH_PrefLoc=1"
                 "&_from=R40&_trksid=p4624852.m570.l1313")
        merged = E.merge_url(typed, E.search_url("rtx 3060 12gb", 150, 420))
        self.assertNotIn("LH_Auction", merged)
        self.assertIn("LH_BIN=1", merged)
        self.assertIn("_sop=15", merged)
        self.assertIn("LH_PrefLoc=3", merged)
        self.assertIn("_from=R40", merged)
        self.assertIn("_trksid=", merged)
        back = E.merge_url(merged, E.search_url("rtx 3060 12gb", None, 400, auction=True))
        self.assertNotIn("LH_BIN", back)
        self.assertNotIn("_udlo", back)  # an auction search has no lower bound
        self.assertIn("LH_Auction=1", back)


class AmazonSearchAfterProductPage(unittest.TestCase):
    def test_goes_home_before_typing(self):
        with tempfile.TemporaryDirectory() as tmp:
            sess = AZ.AmazonSession(object(), Path(tmp))
            sess.on_product = True
            calls = []

            async def goto(url, keys, timeout=25.0):
                calls.append((url, keys))
                return {}
            sess.goto = goto
            with mock.patch.object(AZ.module_kit, "human_pause", mock.AsyncMock()), \
                    mock.patch.object(AZ.module_kit, "_any_present", mock.AsyncMock(return_value=["search_box"])), \
                    mock.patch.object(AZ.module_kit, "type_and_submit", mock.AsyncMock(return_value="before")), \
                    mock.patch.object(AZ.module_kit, "_poll", mock.AsyncMock(return_value=True)), \
                    mock.patch.object(sess, "feedback", mock.AsyncMock()), \
                    mock.patch.object(sess, "guard", mock.AsyncMock(return_value={"url": "u"})):
                asyncio.run(sess.search_form("lenovo thinkstation p520 64gb"))
            self.assertEqual(calls, [("https://www.amazon.com/", ["search_box"])])
            self.assertFalse(sess.on_product)


class HiddenTabFallback(unittest.TestCase):
    """Live 2026-10-28: while the user used Chrome the store tab was hidden, typing went nowhere and five eBay searches
    were lost. The search is now opened by its URL instead, and the page says so."""

    def test_both_stores_fall_back_to_the_search_url(self):
        for M, Session, url in ((E, E.EbaySession, "https://www.ebay.com/sch/i.html?_nkw=rtx+3060+12gb&_sacat=0"),
                                (AZ, AZ.AmazonSession, AZ.search_url("rtx 3060 12gb"))):
            with tempfile.TemporaryDirectory() as tmp:
                sess = Session(object(), Path(tmp))
                calls = []

                async def goto(u, keys, timeout=25.0):
                    calls.append(u)
                    return {}
                sess.goto = goto
                boom = RuntimeError("the search box holds 'rtx 2060 12gb', not 'rtx 3060 12gb' (page hidden) - not submitting")
                with mock.patch.object(M.module_kit, "human_pause", mock.AsyncMock()), \
                        mock.patch.object(M.module_kit, "_any_present", mock.AsyncMock(return_value=["search_box"])), \
                        mock.patch.object(M.module_kit, "type_and_submit", mock.AsyncMock(side_effect=boom)), \
                        mock.patch.object(sess, "feedback", mock.AsyncMock()), \
                        mock.patch.object(sess, "guard", mock.AsyncMock(return_value={"url": url})):
                    asyncio.run(sess.search_form("rtx 3060 12gb"))
                self.assertEqual(calls, [url])
                self.assertIn("page hidden", sess.last_via)
                with mock.patch.dict(M.S, {"form_fallback_url": False}), \
                        mock.patch.object(M.module_kit, "human_pause", mock.AsyncMock()), \
                        mock.patch.object(M.module_kit, "_any_present", mock.AsyncMock(return_value=["search_box"])), \
                        mock.patch.object(M.module_kit, "type_and_submit", mock.AsyncMock(side_effect=boom)), \
                        mock.patch.object(sess, "feedback", mock.AsyncMock()):
                    with self.assertRaises(RuntimeError):
                        asyncio.run(sess.search_form("rtx 3060 12gb"))


class SlowPage(unittest.TestCase):
    """Live 2026-10-28: an eBay navigate timed out (20 s), its reply came 6 s later, and the ready poll's first read hit
    the page mid-load ("Uncaught") - which ended the search. A failed read must just mean 'not ready yet'."""

    def test_goto_keeps_polling_through_a_mid_load_read(self):
        for M, Session in ((E, E.EbaySession), (AZ, AZ.AmazonSession)):
            with tempfile.TemporaryDirectory() as tmp, \
                    mock.patch.object(M.module_kit, "_command", mock.AsyncMock()), \
                    mock.patch.object(M.module_kit, "_any_present", mock.AsyncMock(side_effect=[RuntimeError("Uncaught"), ["item_title"]])), \
                    mock.patch.object(Session, "guard", mock.AsyncMock(return_value={"url": "u"})):
                sess = Session(object(), Path(tmp))
                self.assertEqual(asyncio.run(sess.goto("https://example.test/", ["item_title"])), {"url": "u"})


class TypingAfterReadingDown(unittest.TestCase):
    """Live 2026-10-28: after reading a results page down with the wheel, the next search's typing went nowhere - the
    header search box was out of view - and the box kept the previous query. Scroll back up to it first."""

    class Page:
        def __init__(self, top, works_after=0):
            self.top, self.works_after, self.value = top, works_after, "rtx 2060 12gb"
            self.wheels, self.typed, self.clicked = [], 0, []

        async def send_command(self, cmd):
            from types import SimpleNamespace as NS
            act = cmd.action if isinstance(cmd.action, str) else cmd.action.value
            ok = NS(status="success", error=None, data=None)
            if act == "activate_tab":
                return ok
            if act == "wheel":
                self.wheels.append(cmd.params)
                self.top = min(60, self.top - cmd.params["delta_y"] * cmd.params["notches"])
                return ok
            if act == "type_cdp":
                self.typed += 1
                if self.top >= 0 and self.typed > self.works_after:
                    self.value = cmd.params["text"]
                return ok
            script = cmd.params["script"]
            if "in_view" in script:
                res = {"in_view": self.top >= 0, "top": self.top, "w": 1400, "h": 800}
            elif "elementFromPoint" in script:
                res = {"value": self.value, "x": 1327, "y": 71, "size": 7392, "covered": False, "hit": "SPAN.x", "page": "visible"}
            else:
                res = "https://www.ebay.com/sch/i.html?_nkw=rtx+2060+12gb"
            return NS(status="success", data=NS(extracted_data={"result": json.dumps(res)}))

        async def click_cdp(self, x, y):
            self.clicked.append((x, y))

    def test_scrolls_back_up_to_the_box_then_types(self):
        with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
            page = self.Page(top=-9000)
            asyncio.run(E.module_kit.type_and_submit(E.MODULE, page, "rtx 3060 12gb"))
        self.assertGreater(len(page.wheels), 3)
        self.assertTrue(all(w["delta_y"] < 0 for w in page.wheels))  # wheel UP, in flicks
        self.assertEqual((page.value, page.clicked), ("rtx 3060 12gb", [(1327, 71)]))

    def test_one_retry_then_refuse(self):
        with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
            flaky = self.Page(top=10, works_after=1)  # the first typing is lost, the second lands
            asyncio.run(E.module_kit.type_and_submit(E.MODULE, flaky, "rtx 3060 12gb"))
            self.assertEqual((flaky.typed, flaky.clicked), (2, [(1327, 71)]))
            dead = self.Page(top=10, works_after=99)
            with self.assertRaisesRegex(RuntimeError, "holds 'rtx 2060 12gb'.*not submitting"):
                asyncio.run(E.module_kit.type_and_submit(E.MODULE, dead, "rtx 3060 12gb"))
            self.assertEqual((dead.typed, dead.clicked), (2, []))


import bestbuy_search as BB  # noqa: E402

# Best Buy cards as CARDS_JS returns them (live 2026-10-29, "rtx 24gb", store Rio Hondo Mall)
BB_3060 = {"sku": "6468931", "title": "GIGABYTE - NVIDIA GeForce RTX 3060 12GB GDDR6 PCI Express 4.0 Graphics Card - Black",
           "href": "https://www.bestbuy.com/product/gigabyte-nvidia-geforce-rtx-3060-12gb/JJGGLHJ2CR", "price": "$472.99$47299",
           "comp_value": "The comparable value is Comp. Value: $479.99", "shipping": "Shipping unavailable", "pickup": "Pickup unavailable",
           "unavailable": False, "rating": "Rating 4.8 out of 5 stars with 904 reviews",
           "buttons": ["Top Rated", "Save", "+ 2 offers for you", "Find a Store"],
           "lines": ["Top Rated", "GIGABYTE - NVIDIA GeForce RTX 3060 12GB", "$472.99", "Save $7", "Comp. Value: $479.99",
                     "+ 2 offers for you", "More options from $434.99", "Pickup unavailable", "Shipping unavailable", "Find a Store"]}
BB_QUADRO = {"sku": "13221938", "title": "PNY - NVIDIA Quadro RTX 4500 Graphic Card - 24 GB GDDR6 - DisplayPort", "href": None,
             "price": "$3,285.99$3,28599", "shipping": None, "pickup": None, "unavailable": True, "rating": None,
             "buttons": ["Save", "Unavailable [disabled]"], "lines": ["Not yet reviewed", "$3,285.99", "Unavailable"]}


class BestBuyCards(unittest.TestCase):
    def test_live_cards(self):
        it = BB.parse_card(BB_3060)
        self.assertEqual((it["price"], it["comp_value"], it["availability"], it["available"], it["rating"], it["reviews"]),
                         (472.99, 479.99, "unavailable_here", False, 4.8, 904))
        self.assertEqual(it["more_options_from"]["price"], 434.99)
        self.assertIsNone(it["shipping"])  # not shown as free: unknown, never assumed
        q = BB.parse_card(BB_QUADRO)
        self.assertEqual((q["price"], q["availability"]), (3285.99, "out_of_stock"))

    def test_buyable_cards(self):
        ok = BB.parse_card({**BB_3060, "shipping": "FREE Shipping Get it by Sat, Nov 1", "buttons": ["Add to cart"]})
        self.assertEqual((ok["availability"], ok["shipping"], ok["landed_estimate"]), ("in_stock", 0.0, round(472.99 * 1.07, 2)))
        pick = BB.parse_card({**BB_3060, "shipping": "Shipping unavailable", "pickup": "Pickup today at Miami Dadeland"})
        self.assertEqual(pick["availability"], "pickup_only")

    def test_badge_prefix_and_urls(self):
        self.assertEqual(BB.parse_card({**BB_QUADRO, "title": "New! - PNY - NVIDIA RTX PRO 4000 24GB"})["title"], "PNY - NVIDIA RTX PRO 4000 24GB")
        self.assertEqual(BB.query_of(BB.search_url("rtx 24gb")), "rtx 24gb")
        self.assertIn("intl=nosplash", BB.search_url("rtx 24gb"))

    def test_scripts_render(self):
        js = BB.MODULE.js(BB.CARDS_JS)
        self.assertNotIn("@@", js)
        self.assertIn("price-block-customer-price", js)

    def test_watch_counts_only_buyable_best_buy_offers(self):
        r = PW.rules_for(WATCH, WATCH["products"][G12], "bestbuy")
        self.assertEqual(PW.queries_for(WATCH["products"][G12], "bestbuy"), ["rtx 3060 12gb"])
        self.assertEqual(PW.queries_for(WATCH["products"][P520], "bestbuy"), [])
        it = PW.norm_bestbuy(BB.parse_card(BB_3060))
        self.assertEqual(PW.matches(r, it), "not available (unavailable_here)")
        cheap = PW.norm_bestbuy(BB.parse_card({**BB_3060, "price": "$349.99", "shipping": "FREE Shipping", "buttons": ["Add to cart"]}))
        self.assertIsNone(PW.matches(r, cheap))
        self.assertIn("more options (e.g. Open-Box) from $434.99", PW.notes_for(r, cheap))


sys.path.insert(0, str(ROOT / "@modules" / "pcserverandparts"))
import pcsp_search as PC  # noqa: E402
import price_report as PR  # noqa: E402


class PcspRows(unittest.TestCase):
    """pcserverandparts.com listings as ROWS_JS returns them (live 2026-10-29)."""

    def row(self, text, buttons=("Add to cart",), layout="card", title=None):
        return {"id": "1", "sku": "X", "layout": layout, "title": title, "href": "https://pcserverandparts.com/x/",
                "buttons": list(buttons), "lines": text.split(" | ")}

    def test_card_ready_and_build_your_own(self):
        p5000 = PC.parse_row(self.row("SAVE $268.97 | GDDR5X | Dell - NVIDIA Quadro P5000 (16GB GDDR5X) Graphics Card - Used (00MKC) | "
                                      "PCIe 3.0 x162560 CUDA | Was: $461.99 | $193.02 | 10 in stock | Add to cart",
                                      title="Dell - NVIDIA Quadro P5000 (16GB GDDR5X) Graphics Card - Used (00MKC)"))
        self.assertEqual((p5000["price"], p5000["was"], p5000["availability"], p5000["stock"], p5000["condition"], p5000["shipping"]),
                         (193.02, 461.99, "in_stock", "10", "Used", None))
        rdy = PC.parse_row(self.row("P520 | Windows 11 | Xeon W-2133 3.60GHz | 128GB DDR4 | 1TB NVMe | Quadro P2000 | $1,477.77 | "
                                    "10+ in stock | Add to cart", layout="pcspRdy__row"))
        self.assertEqual((rdy["price"], rdy["available"]), (1477.77, True))
        self.assertEqual(rdy["title"], "P520 | Windows 11 | Xeon W-2133 3.60GHz | 128GB DDR4 | 1TB NVMe | Quadro P2000")
        byo = PC.parse_row(self.row("P520 900W, 2nd Gen Xeon | $184.98 | Configure", buttons=(), layout="pcspByoL__row"))
        self.assertEqual((byo["price"], byo["availability"], byo["available"]), (184.98, "configure", False))
        self.assertEqual(PC.listing_url("thinkstation p620"), "https://pcserverandparts.com/search.php?search_query=thinkstation+p620")
        self.assertNotIn("@@", PC.MODULE.js(PC.ROWS_JS))

    def test_report_groups(self):
        base = {"product": "x", "store": "pcsp", "matches": False, "current": {"price": 193.02}}
        self.assertEqual(PR.classify({**base, "why_not": "price or shipping unknown"}), "price_only")
        self.assertEqual(PR.classify({**base, "why_not": "not available (configure)"}), "byo")
        self.assertEqual(PR.classify({**base, "store": "ebay", "why_not": "price or shipping unknown"}), "unknown")
        self.assertEqual(PR.classify({**base, "why_not": "$700.00 > max $650"}), "over")
        self.assertEqual(PR.classify({**base, "matches": True}), "match")

    def test_p620_parts_are_not_workstations(self):
        w = json.loads((ROOT / "@modules" / "price_watch" / "watches" / "llm-alternatives.json").read_text(encoding="utf-8"))
        r = PW.rules_for(w, w["products"]["Lenovo ThinkStation P620"], "pcsp")
        for t in ("Lenovo - 5B20X58762 - ThinkStation - P620 - sWRX8 - WRX80 - DDR4 - ECC (Motherboard)",
                  "Lenovo - ThinkStation P620 - Memory Fan Duct Assembly - DIMM Baffle - (5M10U50415)"):
            self.assertIsNotNone(PW.matches(r, {"store": "pcsp", "title": t, "available": True, "price": 649.99, "shipping": 0}))
        ok = "Lenovo ThinkStation P620 - AMD Ryzen Threadripper PRO 3945WX (4.00 GHz) 12C - 256GB DDR4 - NVIDIA GeForce RTX 3090 (24GB GDDR6X)"
        self.assertEqual(PW.matches(r, {"store": "pcsp", "title": ok, "available": True, "price": 3000, "shipping": None}),
                         "price or shipping unknown")


class AmazonProductPage(unittest.TestCase):
    def test_live_pages(self):
        r = AZ.parse_product(PNY_RENEWED, "B0BK36KLDD")
        self.assertEqual((r["price"], r["availability"], r["condition"], r["prime"], r["seller"], r["stock_left"], r["shipping"]),
                         (399.97, "in_stock", "Renewed", False, "Respec Store", 1, 0.0))
        r = AZ.parse_product(MSI_AMAZON, "B0CTK1SFVD")
        self.assertEqual((r["condition"], r["offer"], r["prime"], r["seller"]), ("New", "new", True, "Amazon.com"))
        r = AZ.parse_product(USED_ONLY, "B07QC8RSZB")
        self.assertEqual((r["price"], r["available"], r["offer"], r["condition"], r["seller"]),
                         (198.22, True, "used", "Used - Very Good", "Atomic Cellular"))
        r = AZ.parse_product(BATTERY, "B0FFGCKW2T")
        self.assertEqual((r["price"], r["variant"], r["prime"]), (25.99, "10*2.5*0.35inch F62G0", True))

    def test_real_price_is_the_selected_option(self):
        raw = {**BATTERY, "core_price": "$30.99", "variants": [{**BATTERY["variants"][0], "price": "$27.97"}]}
        r = AZ.parse_product(raw, "B0FFGCKW2T")
        self.assertEqual((r["price"], r["buybox_price"]), (27.97, 30.99))
        self.assertIn("selected option $27.97 vs buy box $30.99", r["notes"])

    def test_shown_but_not_available(self):
        for raw, want in (({**PNY_RENEWED, "availability": "Currently unavailable.", "add_to_cart": False, "buy_now": False}, "out_of_stock"),
                          ({**PNY_RENEWED, "availability": "Temporarily out of stock."}, "out_of_stock"),
                          ({**PNY_RENEWED, "availability": "In stock", "add_to_cart": False, "buy_now": False}, "not_buyable"),
                          ({**PNY_RENEWED, "availability": None, "add_to_cart": False, "buy_now": False, "see_all": True}, "no_featured_offer"),
                          ({**BATTERY, "variants": [{**BATTERY["variants"][0], "availability": "Currently unavailable"}]}, "out_of_stock")):
            r = AZ.parse_product(raw, raw["asin"])
            self.assertEqual((r["availability"], r["available"]), (want, False), raw.get("availability"))

    def test_spaced_price_and_prime_member_delivery(self):
        self.assertEqual(AZ.money("$399 . 97"), 399.97)
        r = AZ.parse_product({**PNY_RENEWED, "delivery": "FREE delivery Friday for Prime members"}, "B0BK36KLDD")
        self.assertIsNone(r["shipping"])  # free only for members: not assumed

    def test_card_prime_badge(self):
        card = {"asin": "B0CTK1SFVD", "title": "msi Gaming RTX 3050", "price": "$259.97", "delivery": "FREE delivery Fri, Oct 30",
                "prime": True, "lines": []}
        self.assertTrue(AZ.parse_card(card)["prime"])
        self.assertFalse(AZ.parse_card({**card, "prime": False})["prime"])

    def test_product_script_renders(self):
        js = AZ.MODULE.js(AZ.PRODUCT_JS)
        self.assertNotIn("@@", js)
        for sel in ("#availability", "#add-to-cart-button-ubb", "tp-inline-twister-dim-values-container", "#productTitle"):
            self.assertIn(sel, js)


def az(asin, title, price, **kw):
    card = {"asin": asin, "title": title, "price": f"${price:.2f}", "delivery": "FREE delivery Fri, Oct 30", "lines": [], **kw}
    return PW.norm_amazon(AZ.parse_card(card))


def eb_auction(id_, lines):
    return PW.norm_ebay(E.parse_card({"id": id_, "lines": lines}))


class Report(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.state = {"items": {}, "cycles": 0}
        self.w = {**WATCH, "interval_minutes": 10, "shuffle": False,
                  "products": {G12: WATCH["products"][G12], G24: WATCH["products"][G24]}}
        self.bin, self.auc, self.pages, self.checks, self.details = {}, {}, {}, [], {}
        self.alerts = mock.patch.object(PW, "alert").start()
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(self.tmp.cleanup)

    async def search(self, store, query, lo, hi, pages, kind="bin"):
        src = self.auc if kind == "auction" else self.bin
        return [dict(i) for i in src.get((store, query), [])]

    async def check(self, store, kind, item_id):
        self.checks.append((store, kind, item_id))
        if kind == "product":
            return AZ.parse_product(self.pages[item_id], item_id)
        return {"id": item_id, **E.parse_auction_page(self.details[item_id])}

    def run_cycle(self):
        return asyncio.run(PW.cycle(self.search, self.w, self.state, self.d, check=self.check))

    def test_amazon_counts_only_when_the_product_page_says_buyable(self):
        ok = az("B0AAAAAAA1", "ASUS Dual GeForce RTX 3060 V2 12GB OC", 289.99, prime=True)
        gone = az("B0AAAAAAA2", "MSI GeForce RTX 3060 Ventus 2X 12G", 279.99)
        dearer = az("B0AAAAAAA3", "Gigabyte GeForce RTX 3060 Gaming OC 12G", 299.99)
        self.bin = {("amazon", "rtx 3060 12gb"): [ok, gone, dearer]}
        self.pages = {"B0AAAAAAA1": {**MSI_AMAZON, "asin": "B0AAAAAAA1", "core_price": "$289.99"},
                      "B0AAAAAAA2": {**PNY_RENEWED, "asin": "B0AAAAAAA2", "availability": "Currently unavailable.",
                                     "add_to_cart": False, "buy_now": False},
                      # the selected option on the page costs more than the card showed: over the $400 ceiling
                      "B0AAAAAAA3": {**BATTERY, "asin": "B0AAAAAAA3", "core_price": "$429.99", "variants": [
                          {"asin": "B0AAAAAAA3", "label": "12GB OC", "price": "$429.99", "availability": "In Stock", "prime": False}]}}
        self.run_cycle()
        self.assertEqual(sorted(c[2] for c in self.checks), ["B0AAAAAAA1", "B0AAAAAAA2", "B0AAAAAAA3"])
        recs = {r["id"]: r for r in self.state["items"].values()}
        self.assertTrue(recs["B0AAAAAAA1"]["matches"])
        self.assertTrue(recs["B0AAAAAAA1"]["current"]["prime"])
        self.assertEqual(recs["B0AAAAAAA2"]["why_not"], "not available (out_of_stock)")
        self.assertIn("> max $400", recs["B0AAAAAAA3"]["why_not"])
        self.assertEqual(recs["B0AAAAAAA3"]["current"]["price"], 429.99)
        cmp_ = json.loads((self.d / "compare.json").read_text(encoding="utf-8"))
        g12 = next(p for p in cmp_["products"] if p["product"] == G12)
        self.assertEqual((g12["matches"], g12["best"]["prime"]), (1, True))
        self.assertEqual([u["url"] for u in g12["amazon_unavailable"]], ["https://www.amazon.com/dp/B0AAAAAAA2"])
        html = (self.d / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn("NOT buyable on the product page", html)
        self.assertIn("<th>Prime</th>", html)

        self.checks.clear()  # fresh reads are reused while the card price is unchanged
        self.run_cycle()
        self.assertEqual(self.checks, [])
        self.bin[("amazon", "rtx 3060 12gb")][0] = {**ok, "price": 279.99}
        self.run_cycle()
        self.assertEqual(self.checks, [("amazon", "product", "B0AAAAAAA1")])

    def test_pages_wait_their_turn_and_alert_new_only_after_the_baseline(self):
        cards = [az(f"B0BBBBBBB{i}", f"ASUS Dual GeForce RTX 3060 12GB OC v{i}", 250 + i) for i in range(7)]
        self.bin = {("amazon", "rtx 3060 12gb"): cards}
        self.pages = {c["id"]: {**MSI_AMAZON, "asin": c["id"], "core_price": f"${c['price']:.2f}"} for c in cards}
        self.run_cycle()  # baseline: the 5 cheapest are checked
        self.assertEqual(len(self.checks), 5)
        cmp_ = json.loads((self.d / "compare.json").read_text(encoding="utf-8"))
        self.assertEqual(next(p for p in cmp_["products"] if p["product"] == G12)["amazon_waiting_for_page_check"], 2)
        events = self.run_cycle()  # the other 2 get checked; they were listed at the baseline: no alert
        self.assertEqual(len(self.checks), 7)
        self.assertEqual(events, [])
        late = az("B0BBBBBBC9", "ASUS Dual GeForce RTX 3060 12GB OC late", 260)
        self.bin[("amazon", "rtx 3060 12gb")].append(late)
        self.pages[late["id"]] = {**MSI_AMAZON, "asin": late["id"], "core_price": "$260.00"}
        events = self.run_cycle()
        self.assertEqual([(e["type"], e["id"], e["prime"]) for e in events], [("NEW", "B0BBBBBBC9", True)])

    def test_auctions_time_left_bid_bids_watchers(self):
        cheap = ("EVGA GeForce RTX 3090 FTW3 Ultra 24GB | Pre-Owned | $420.00 | 12 bids · | Time left | 3h 10m left (Today, 09:15 PM) | "
                 "+$15.00 delivery | Located in United States | gpu-shop 99.8% positive (2,400)").split(" | ")
        later = ("NVIDIA GeForce RTX 3090 Founders Edition 24GB | Pre-Owned | $300.00 | 4 bids · | Time left | 1d 2h left (Fri, 11:00 PM) | "
                 "Free delivery | Located in Canada | maple 100% positive (310)").split(" | ")
        self.auc = {("ebay", "rtx 3090 24gb"): [eb_auction("111", cheap), eb_auction("222", later),
                                                eb_auction("840049637356", AUCTION_TI)]}  # $1,475 bid: already over $600
        self.details = {"111": {"bid_price": "US $430.00", "bid_count": "13 bids", "end_time": "Ends in 3h 2mToday, 09:15 PM",
                                "watchers": "People want this. 41 people are watching this."},
                        "222": {"bid_price": "US $300.00", "bid_count": "4 bids", "end_time": "Ends in 1d 1hFriday, 11:00 PM"}}
        self.run_cycle()
        self.assertEqual(sorted(c for c in self.checks if c[1] == "auction"), [("ebay", "auction", "111"), ("ebay", "auction", "222")])
        cmp_ = json.loads((self.d / "compare.json").read_text(encoding="utf-8"))
        g24 = next(p for p in cmp_["products"] if p["product"] == G24)
        self.assertEqual(g24["matches"], 0)  # a bid is never a price: auctions are not offers
        rows = g24["auctions"]
        self.assertEqual([a["url"][-3:] for a in rows], ["111", "222"])  # ending soonest first; the $1,475 one is hidden
        self.assertEqual((rows[0]["current_bid"], rows[0]["bids"], rows[0]["watchers"], rows[0]["time_left"], rows[0]["bid_plus_ship"]),
                         (430.0, 13, 41, "3h 2m", 445.0))
        self.assertIsNone(rows[1]["watchers"])
        self.assertEqual(rows[1]["located_in"], "Canada")
        html = (self.d / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn("a current bid is NOT a price", html)
        self.assertIn("not shown", html)
        # within the hour the listing pages aren't read again
        self.checks.clear()
        self.run_cycle()
        self.assertEqual([c for c in self.checks if c[1] == "auction"], [])

    def test_parts_rules_only_for_parts_queries(self):
        r = PW.rules_for(WATCH, WATCH["products"][G12], "ebay")
        self.assertFalse(r["parts_ok"])
        self.assertTrue(any("for parts" in p for p in r["reject"]))
        parts = {**WATCH["products"][G12], "queries": {"ebay": ["rtx 3060 12gb for parts"]}}
        r = PW.rules_for(WATCH, parts, "ebay")
        self.assertTrue(r["parts_ok"])
        self.assertFalse(any("for parts" in p for p in r["reject"]))


if __name__ == "__main__":
    unittest.main()
