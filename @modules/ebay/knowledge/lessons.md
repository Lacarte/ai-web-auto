# eBay — lessons learned

- **Item pages carry stable data-testid hooks** (verified 2026-10-28): `x-item-title`, `x-price-primary`,
  `x-bin-price`, `x-item-condition`, `x-quantity`, `x-sellercard-atf`, `d-shipping-minview`,
  `x-returns-minview`, `x-about-this-item` with `ux-labels-values` pairs, `x-photos`,
  `x-store-information`, `x-feedback-detail-seller-rating`. Use them, not the generated classes.
- **Search result cards are `li[data-listingid]`**; the item id comes from the `/itm/<id>` link. Reading
  the card's `innerText` lines (title, condition, price, delivery, "Located in", "seller 99.8% positive (26K)")
  is more robust than per-field class selectors.
- **"N results" counts only exact matches**; eBay then pads the page with "Results matching fewer
  words". With `_ipg=120` a 11-result query still returns 117 cards — stop at the divider.
- **A placeholder card links `/itm/123456`** ("Shop on eBay"); skip it.
- **The seller's description is an iframe from itm.ebaydesc.com** (`iframe#desc_ifr`). The extension reads
  only the main frame, so open the iframe's `src` in the same tab and read its body.
- **Ship-to ZIP via URL**: `_stpos=33101&_fcid=1` makes cards and item pages show shipping "to 33101";
  the item page's delivery line ends with "to 33101", which proves the ZIP was applied.
- **Sponsored cards end with a reversed 'derosnopS' text** — it is an anti-scrape trick, not data.
- **Titles contradict themselves** ("RTX 3060 Ventus 2X 8G OC 12GB"): check item specifics
  (`Memory Size`) and put contradictions in review instead of trusting the title.
- **Cheapest-first sorting surfaces junk**: in Oct 2026 the cheapest "RTX 3060 12GB" BIN listings were
  all "Parts Only"; the condition URL filter (all ids except 7000) plus title rules removes them.
- **Item specifics are a semantic `<dl>`: read `dt` and its next `dd`.** The `ux-labels-values` testid
  exists only in the shipping section; using it for specifics silently returned 0 fields.
- **The description iframe is lazy**: `iframe#desc_ifr` appears only after its section scrolls into view
  (as do the lower seller badges). Scroll to the bottom, wait for the frame (bounded), and fall back to
  `https://itm.ebaydesc.com/itmdesc/<id>`.
- **Never use timers in page scripts**: Chrome throttles `setTimeout` hard in a background window, so an
  async scroll loop stalled for minutes. Scroll synchronously.
- **eBay's load event can take > 30 s** (trackers); cap the navigate wait (20 s) and decide readiness by the
  page's own ready locators.
- **Preview the read list before a long run**: cheapest-first picks surfaced "FAN FAILED", "Fan Only No
  Processor", "SLIGHT DAMAGE" and a RAM listing titled "for MSI X470" in the motherboard search. Exclusion
  flags stored at search time go stale when rules improve - re-check titles when picking.
- **Descriptions contradict "tested" titles**: "Stress tested … NOTE: the right fan doesn't work";
  "missing the original fan shroud, aftermarket fan at 100%". Always read the description frame.
- **Long jobs exceed one 10-minute command**: cache each phase in the run folder (`searches.json`,
  `listings_raw.json`) and re-run with `--no-search` / `--no-read`.
- **Search price caps hide markets**: with `_udhi` below the market, eBay returns "0 results" and the sold
  comps bunch at the cap (RTX 3090 at $1,100, Oct 2026: real median $1,599). If sold prices sit at the cap,
  re-search uncapped before concluding anything.
- **Sold cards start with "Sold <date>"**: take the title from the first line that is not the sold date,
  or sold comps can't be filtered by model and broad queries pull in bridges, PCs and laptop daughterboards.
- **eBay pads "64GB combo" searches with 8-32GB bundles** and its loose matching returns complete PCs for
  card searches; title rules (RAM amount not followed by SSD/NVMe, "not a card" patterns) keep them out.
- **Multi-option listings show the cheapest option on the card** ("$208.88 to ..."). Read them, record the
  option pickers in the buy box, and treat the price as unverified for the configuration in the title.
- **Workstation value depends on the PSU option**: Dell 5820 425 W has no GPU power connector, HP Z4 G4
  750 W has only 6-pin, a dual-CPU 7820 limits cards to 150 W. The listing must state the PSU.
- **Quad-channel only with 4+ DIMMs**: 64GB as 2x32GB in a Xeon W runs half the memory bandwidth.

## 2026-10-28 - searching like a person (the user asked)
- The header search form `form#gh-f` (GET /sch/i.html) holds the box `#gh-ac` (`name=_nkw`) and three buttons:
  "Clear search", "Camera icon", and the real **Search `#gh-search-btn`**. A probe that took the first visible button
  clicked Clear, and nothing was searched. Register the submit by id and check what is under the click point. Under
  Search's centre is its label `span.gh-search-button__label`, inside the button, so it counts as the button.
- A typed search opens `/sch/i.html?_nkw=<q>&_sacat=0&_from=R40&_trksid=...`. Our filters (BIN, US only, ZIP,
  conditions, range, sort, 120/page) are merged into that URL; `_from`/`_trksid` are kept.
- Every result card (`li[data-listingid]`, 62 on a 60-per-page search) is in the DOM before any scrolling. The old
  SCROLL_JS "lazy cards" step only loaded images. Mouse-wheel reading is pacing, so it is capped at a random
  number of flicks rather than scrolling 40,000 px of 120 results.
- Reading a page in the middle of a navigation fails ("Uncaught"). Polls after a click must tolerate a failed read.

## 2026-10-28 - price report: North America, conditions, auctions (the user asked)
- The Item location filter links carry `LH_PrefLoc`: 1 US only, 3 North America, 2 Worldwide, 98 Default,
  99 local pickup (read live, `scratch/ebay/probe_auction.py`).
- The user's condition list `1500|1000|2000|2010|3000|2020|2030` is also what eBay's own filter links echo for GPUs.
  The old list (1750, 2500, 2750, 4000-6000) held ids this category doesn't use.
- Auction cards: `$1,475.00 | 8 bids · | Time left | 19h 28m left (Thu, 06:10 PM)`. The time sits in
  `.s-card__time-left`, the end in `.s-card__time-end`, "HOT AUCTION" in `.signal--time-sensitive`. Cards have only
  a watch *heart*, no watcher count.
- The listing page has the watcher count in its demand signal `[data-testid="x-wtb-signals"]`: "People want this.
  108 people are watching this." Bids are in `x-bid-count`, the timer in `x-end-time` ("Ends in 1d 19hFriday,
  05:55 PM": the timer and the date run together).
- **The second search of a run typed into nothing.** The previous results page had been read down with the
  wheel, so the header search box was out of view. `type_cdp` clicked the box's off-screen position and the
  box kept the old query ("holds 'rtx 2060 12gb', not 'rtx 3060 12gb'"). Once the search button was off screen
  too, the check reported "covered (None)": there was nothing at that point. The value check refused to submit
  every time (correct), but those searches were lost. `module_kit.type_and_submit` now wheels back up to the box
  first (`scroll_to`) and retries the typing once.
- **eBay's header form carries the previous search's filters** into the typed search's URL. A Buy It Now search
  typed after an auction search came back with `LH_Auction=1` beside our `LH_BIN=1`, and found 0-16 cards instead
  of ~120. `merge_url` now keeps only `_from`/`_trksid` from the typed URL; every filter is ours.
- A navigate can time out (20 s) on a slow eBay page, and its reply then arrives late. The ready poll must
  treat a failed mid-load read as "not ready yet" (`_present`), not as an error.
- Live market check: RTX 3090 auctions ending within 2 days stood at $1,355-$1,500 current bid. A "$450-$600 24GB"
  card is far below what these sell for.
- A listing page's shipping box can start with "Pickup: Free local pickup…" above the real "Shipping: US $25.43
  FedEx" line. `shipping_cost` read the pickup line as free shipping (item 158164238777, 2026-10-29). It now
  reads only the "Shipping:" line.
- Typing into a hidden tab (another Chrome window in front) hangs `type_cdp` until it times out, and every reply
  after it comes late. Check `document.visibilityState` before blaming the extension.
- A title can promise an option the menus don't offer: item 226422183878 says "64GB RAM" ($285 on the card), but
  its RAM menu offers only 16GB or 32GB (2026-10-30). `read_listing` with a variation regex then returns
  `price: None` and no mismatch. Treat a None price after a variation requirement as "option not offered",
  and confirm in `raw.msku.variations`.
- A sold-out listing still shows its price: item 307174416566 read "Out of Stock, 18 sold" with "US $479.99".
  Check `available` before carrying an old pick into a new report.
