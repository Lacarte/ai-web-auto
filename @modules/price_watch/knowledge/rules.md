# Price watch - rules

## What it is
A timer over saved searches on several stores. Each product in `watches/<name>.json` has queries (shared, or
per store), a search price range (`min`/`max`), an alert ceiling `max_price` (price + shipping), title rules
(`require`, `reject`, named `reject_sets`), and per-store overrides (`"ebay": {...}`, `"amazon": {...}`).
Store specifics (how to search, read cards, detect blocks) live in the store modules (`ebay`, `amazon`).

## Safety
- Alert only. Never buys, bids, makes offers, adds to cart, watches an item, or messages a seller.
- A CAPTCHA / robot check / sign-in stops the watch and alerts the human (`Blocked`). Never bypassed.
- Human pace (the user asked, 2026-10-28). Polite pacing, never a way around a block:
  - The cycle interval varies by +/-20% (`interval_jitter`) and is never under 5 minutes (`MIN_INTERVAL_MIN`).
  - Products, stores and queries run in a new random order each cycle (`"shuffle": false` turns it off).
  - Each store module paces its page loads (random pauses, occasional long reads; see their rules).
- One tab per store, opened once in front so preflight binds to it.
- Both stores search like a person (`search_via: "form"` in each store module): type in the search box, click
  Search, read with the mouse wheel, click Next (shared helpers `module_kit.type_and_submit`, `read_down`,
  `click_link`). Trusted input needs the tab in front, so each store's tab comes forward during its searches.
- Page loads otherwise use `activate: false` and `dismiss_banners: false`. `search_via: "url"` keeps a store fully
  in the background. The watcher closes its own tabs on exit (`close_tab` closes only the bound
  tab). A tab the user closes is reopened at the next search.

## Searches (the user's report rules, 2026-10-28)
- eBay: items located in **North America**, conditions **1500|1000|2000|2010|3000|2020|2030**. "For parts" (7000) and
  the parts reject patterns apply only to a product whose queries ask for parts (`PARTS_QUERY` -> `parts_ok`).
- eBay: each query is searched twice, as **Buy It Now** (the price range, cheapest first) and as **auctions**
  (ending soonest, current bid up to `max_price`). `"ebay": {"auctions": false}` turns the auction search off. All
  of a product's searches run in a shuffled order.
- Amazon: each query's cards, then the **product pages** of the card-level matches (below).

## Workstations: always 64GB RAM or more (the user's rule, 2026-10-29)
- Every workstation product (P520, the alternatives, P620) requires a RAM size of 64GB or more in the title, and its
  eBay/Amazon queries include "64gb".
- A size followed by SSD / HDD / NVMe / M.2 / SATA / drive is storage, not RAM. "16GB RAM 512GB SSD" does not pass.
- Barebones ("No RAM") and 16/32GB systems are not matches. The report may mention them as a base to add RAM to,
  never as a match.
- `price_report.py` re-checks titles against the watch's current rules, so a rule tightened after a cycle still
  applies to the report.

## Matching (an offer counts only if all hold)
1. Not excluded by the store module (parts / not working / ...), and the global reject list (box only, empty box,
   read description; plus the parts words unless the queries ask for parts).
2. Not an auction: a bid is never a price. Auctions are reported apart (below).
3. Title has every `require` pattern and none of the `reject` / `reject_sets` patterns.
4. Available:
   - eBay results are live listings.
   - Amazon must be **confirmed on its product page**: a buy button AND an availability line that doesn't say
     unavailable. The card alone is not enough: some results show but can't be bought.
5. A single known price and known shipping: unknown shipping or a price range is not a match. Amazon's price is the
   **selected option's real price** on the product page, not the card's.
6. `price + shipping <= max_price` (and `>= min_price` when set).
7. Seller trust: eBay feedback count >= `min_feedback` (default 25) and >= `min_feedback_pct` (default 97%);
   Amazon `min_reviews` / `min_rating` when set; `require_prime: true` keeps only Prime offers.

## Amazon product-page checks
- The card-level matches without a fresh check are checked cheapest first, at most `verify_per_cycle` per product
  per cycle (default 5). The rest wait for the next cycle, and the dashboard counts them as waiting.
- A check stays valid for `verify_max_age_hours` (default 6) while the card price is unchanged.
- A failed read leaves the offer unconfirmed (not a match): it is never guessed.
- An offer first confirmed after the baseline alerts as NEW. One already listed at the baseline doesn't.
- Offers the page shows as not buyable are listed under the product ("shown in Amazon search but NOT buyable").
- `"amazon": {"verify_pages": false}` goes back to cards only.

## Auctions (eBay, shown, never matched)
- Shown when the title, exclusion, condition and seller rules pass and the current bid + shipping is still under
  `max_price`. Ending soonest first.
- Shown with time left, end time, current bid, shipping, bid + shipping, bids, watchers, seller, location.
- Watchers: read from the listing page of the soonest-ending shown auctions, at most `auction_details` per product
  per cycle (default 3), refreshed after `auction_details_max_age_minutes` (default 60). If eBay shows no
  watching signal, the count is "not shown", never 0.
- Bid history per auction (bid, bids, watchers over time) is kept in `state.json` -> `auctions`.
- Auctions never alert, never count as a match, and are never "cheapest".

## Alerts (from the second cycle on; the first cycle is a baseline)
- **NEW**: a matching offer seen for the first time.
- **PRICE DROP**: a matching offer's total fell.
- **BACK IN STOCK**: an Amazon item matched again after being out of stock / without an offer.
- **NOW MATCHES**: an offer seen before now passes the rules (e.g. the seller fixed the shipping).
- An alert is marked **CHEAPEST NOW** when it is the lowest matching total across all stores.
- `suspicious_below`: a match under that total gets a CHECK note (far below the market). It is still shown and
  alerted: it may be a real deal or a scam. The human checks seller, photos and returns.

## Comparison
- Per product: current matching offers (seen this cycle), cheapest total first, and the best per store.
- Landed estimate = total x (1 + tax_rate). eBay totals are for ZIP 33101. Amazon totals are for the account's
  delivery address (`deliver_to`, recorded).
- Amazon offers show **Prime** (yes / no), condition (New / Renewed / Used - grade), the option name and the seller
  from the product page.
- Amazon's "used & new offers from $X" is shown as a note, never as a price (condition and shipping unknown).
- An offer missing from this cycle's results gets `gone_since` (sold, ended, out of range, or pushed past the
  pages read). It drops out of the comparison but keeps its history.
- **Deals (2026-10-30):** an offer at or under a product's `deal_price` (price + shipping) gets a check mark and goes in
  the opportunity banner; one alert per deal (again only if the same listing gets cheaper), with its own chime
  (`sounds/opportunity.wav`), even on the baseline cycle.
  - eBay auctions count only when bid + shipping is under the deal price and they end within 24 h, and are always
    labelled "bid, not a price".
  - A selectable eBay listing counts only at the price its page showed after `config_check.py` selected the required
    options (RAM >= 64GB; PSU >= the watch's `psu_min_w` when the listing has a PSU menu), never at its "from" price.
- **build-deals schedule:** 06:00, 12:00, 18:00 only (never midnight-6 AM), signed in, no catch-up runs. The Claude
  step may only read, write and search files (`dontAsk`); it never browses or buys.
