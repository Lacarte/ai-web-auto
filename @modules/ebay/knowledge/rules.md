# eBay — decision rules

What may be searched and read, what is excluded, how a price is counted, and when a listing is
trusted. `ebay_rules.py` (and `jobs/job_runner.py` for job rules) enforce every rule here; change
both together.

## Read-only, always

The module never clicks Buy It Now, Place bid, Add to cart, Watch or Make offer. `buy_it_now_button`
and `place_bid_button` are registered only so their **presence** can be read. Every purchase is the
human's decision.

## Identity

A listing is its **eBay item number** (`/itm/<id>`). After opening a listing the page's
"eBay item number:" must equal the requested id, or the listing is flagged.

## Excluded outright

| Rule | Where |
|---|---|
| Condition "For parts or not working" | URL filter `LH_ItemCondition` = the user's list (below), **and** card/page condition text |
| Title says: for parts, parts only, not working, broken, defective, as-is, for repair, box only, no display/video/post, artifacts, read description, untested, missing, cracked | `ebay_rules.EXCLUDE_TITLE` |
| Auction only (no Buy It Now) | a current bid is never a purchase price: never an offer, never "cheapest" (auctions are reported apart, below) |
| Seller with < 25 ratings or < 97% positive | `trust()` reject |
| Listing ended / 0 available / does not ship to the ZIP | `assess()` |
| eBay's "Results matching fewer words" filler and the `/itm/123456` placeholder card | search parser |

### Search filters (the user's instruction, 2026-10-28)

- **Item location: North America** - `LH_PrefLoc=3` (`settings.item_location`). Values read from the live filter
  links: 1 US only, 3 North America, 2 Worldwide, 98 Default. A card's "Located in Canada" is shown as a note.
- **Conditions: exactly** `LH_ItemCondition=1500|1000|2000|2010|3000|2020|2030` (Open box, New, Certified /
  Excellent / Very good / Good refurbished, Used; `settings.condition_ids`).
- **7000 (For parts or not working) is added only when the query itself asks for parts** ("for parts", "parts
  only", "not working"; `wants_parts`). Then parts listings are not excluded either. Otherwise never.

Rules read **everything the seller wrote**: title, item specifics (including *Seller Notes*), the
condition note, and the description frame. Live examples that only the specifics/notes exposed:
"Deep Cool AK400" with *Brand: Vetroo* (bait listing) and a PSU whose seller note says "only has power
cable for board".

- **Title brand ≠ item-specifics brand** → review (a job may make it a reject, e.g. coolers).
- **PSU without its full modular cable set** → reject: "cables not included", "unit only", "item only",
  "partial cable set", PayMore's "if it is not listed it is not included – such as power cables" (unless the
  listing positively says all cables are included). A PSU whose listing never confirms the cables → review.

Risky words in the **description** (not working, no returns, mining, no cables, stock photo, damage…)
do not exclude by themselves — descriptions are full of boilerplate — they put the listing in
**review** with the quoted context.

## Price

`landed = (Buy It Now price + shipping to settings.ship_to_zip) × (1 + settings.sales_tax_rate)`.
7% = Florida 6% + Miami-Dade 1% surtax, collected by eBay; it is an estimate (eBay shows the exact
tax at checkout). A listing whose shipping to the ZIP is not shown has no landed cost and cannot be
in a build.

Sold comps (`LH_Sold=1&LH_Complete=1`) give the market median and middle range. A listing more than
20% under the 25th percentile is put in review ("verify it is genuine"), not celebrated as a deal.
The comparison applies only to listings of the **same model** as the sold search (every word of the sold
query in the title) - comparing a B550-A Pro to X570-Pro sales flagged good listings as suspicious.

## Trust score (0–100, start 50)

+10 ≥ 1000 ratings · −10 < 100 ratings · +10 ≥ 99.5% · −10 < 98.5% · +10 ≥ 30-day returns ·
−20 no returns · +5 Top Rated Plus · −10 fewer than 3 photos · +10 testing described ·
−5 per description risk phrase (max 3) · −15 listing inconsistencies · −10 ships from outside the US.
A listing below `settings.min_trust` (55) goes to review, never into a build.

## Status of a read listing

- **pass** — no reject reason, no review note, trust ≥ min_trust → may be used in a build.
- **review** — needs a human or a seller answer (the notes say what to ask).
- **reject** — breaks a rule; the reason is listed.

## Jobs

A job (`jobs/<name>.json`) adds per-component `require` / `reject` title rules, `spec_checks`
against item specifics, `mismatch` rules (title vs specs contradictions → review), and a
`catalog` of compatibility facts. Catalog facts carry a manufacturer `source`; facts without one
are reported as **unconfirmed**, never as passed.

## Pacing and blocks

Searches behave like a person (the user asked, 2026-10-28; `settings.search_via = "form"`, shared helpers in
`module_kit`):
- **Search:** the tab comes to the front; type the query into `search_box` (click, clear, varied speed 45-140
  ms/char); check the box holds exactly the query and that `search_submit` (#gh-search-btn) is visible and
  uncovered; then a trusted click. Never the first button in the form: it is "Clear search".
- **Filters:** applied to the results page the typed search produced (`merge_url`). These are BIN, US only, ZIP,
  conditions, price range, sort and 120 per page; eBay's own `_from`/`_trksid` are kept.
- **Reading:** each page is read with mouse-wheel flicks (`read_bursts`, pauses, sometimes a notch back up)
  before the cards are parsed. Every card is in the page before scrolling, so this is pacing, not loading.
- **More pages:** read on to the Next link, pause, then a trusted click (evidence `_pgn=N`). If the click
  doesn't move the page, open the link's URL.
- **Pauses:** 3-9 s random between page loads, 12% of the time a 15-40 s read (`pause_seconds`, `long_pause`).
- A slow page is reloaded once; at most `settings.max_pages` pages per search.
- `search_via = "url"` opens the search URL directly and scrolls by script. It works in a background tab.

A CAPTCHA, bot check or sign-in redirect stops the run with a message. Never bypassed: the pacing is politeness,
not evasion (no CAPTCHA solving, fingerprint spoofing or proxies).

## Multi-part listings (jobs with `covers`)

A bundle (CPU + board + RAM) or a complete workstation covers several parts; a build is valid only if its
listings cover every part exactly once on one platform (AM4 DDR4, AM5 DDR5, Xeon W/SP RDIMM). Every
cross-part check is PASS (evidence), FAIL (evidence of a problem) or UNVERIFIED (no evidence) - an
unverified check is never counted as a pass. Stage "now" = 1 GPU + NVMe + 64GB; stage "upgrade" = 2 GPUs
with the NVMe in the slot the manual says keeps the second GPU slot alive (e.g. MSI B550-A PRO: M2_1).

Decisions: REJECT (hard failure) · INVESTIGATE (missing evidence, review notes, multi-option price) ·
WAIT (verified but > 110% of the sold 75th percentile, or over budget) · BUY CANDIDATE (verified, priced
within the sold range). A BUY CANDIDATE is not permission to buy.

## Multi-option listings (CPU / RAM / GPU / storage menus)

The card and default page price belong to the default option, not to the configuration in the title.
1. Read every combination from the page's embedded MSKU data (price, stock, variation id, menu labels).
2. The job's requirement picks the option - for the LLM workstation **RAM = 64GB is the target** (only the
   RAM menu's value counts; an SSD menu's "128 GB" is not RAM). Among in-stock combinations with 64GB, take
   the cheapest; larger RAM only if no 64GB option is in stock.
3. Walk the menus in page order like a person: open menu 1, record its options (enabled/disabled) as they
   are after the previous choices, choose, wait for the button to show the value, record the price; then
   menu 2, and so on (`walk_options`).
4. The price counts as confirmed only if the page price AND the URL's `?var=` id equal the embedded
   combination. Screenshots capture the visible Chrome tab, so the working tab's DOM state is the evidence.
5. No in-stock option meets the requirement -> REJECT ("no 64GB option in stock"). Selecting options never
   touches Buy It Now, Add to cart, Watch or Make offer.

## Auctions (the user's instruction, 2026-10-28)

The report shows auctions next to the Buy It Now offers: **time left vs current bid vs bid count vs watchers**.
- Search: `auction=True` -> `LH_Auction=1&_sop=1` (ending soonest), same location, conditions and title rules, and
  only an upper bound (the current bid may be far below the final price).
- Card (live 2026-10-28): `$1,375.00 | 28 bids · | Time left | 1d 19h left (Fri, 05:55 PM)`. This gives
  `current_bid`, `bids`, `time_left`, `seconds_left` and `ends`. Cards have no watcher count.
- Listing page (`read_auction`): `x-bid-price`, `x-bid-count`, `x-end-time`, and the demand signal `x-wtb-signals`
  ("108 people are watching this."). With no signal, watchers are **unknown**, never 0.
- A current bid is never a price. Auctions never count as matches, never go into a build, and are never the
  cheapest. An auction whose current bid + shipping is already over the product's ceiling is hidden: it cannot
  end under it.
- Place bid and Watch are read for presence only and never clicked.

## Watching (ebay_watch.py)

- Alert only: a watch never buys, bids, watches, offers or adds to cart.
- Interval >= 5 minutes (MIN_INTERVAL_MIN), one results page per query, newest first, normal page pacing.
- The first cycle of a new watch is a baseline: everything already listed is recorded, nothing alerts.
- NEW = not seen before + passes the query's require/reject, exclusion rules (parts / not working), max_price
  (item + shipping to the ZIP), conditions and min_feedback. PRICE DROP = a matching item's price + shipping fell.
- A CAPTCHA stops the watch with an alert; it is never bypassed.
