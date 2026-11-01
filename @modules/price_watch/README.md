# Price watch (eBay + Amazon + Best Buy)

Watches products across stores on a timer. It plays a sound and shows a Windows notification when a matching
offer **appears**, **comes back in stock** or **drops in price**. Every cycle it writes a dashboard comparing
the best price per product on each store. Alert only: it never buys, bids, adds to cart or messages anyone.

## Run

Stop `runner.bat` (or anything else on port 8765) first. Chrome with the AI Web-Auto extension must be open,
and reloaded after 2026-10-28 so that it has background loads and `close_tab`.

```
cd @modules\price_watch
..\..\venv\Scripts\python price_watch.py watches\llm-parts.json --test-alert   # check sound + notification
..\..\venv\Scripts\python price_watch.py watches\llm-parts.json --once         # one cycle (the first = baseline)
..\..\venv\Scripts\python price_watch.py watches\llm-parts.json                # every interval_minutes, Ctrl+C to stop
run-watch.bat                                                                  # same, double-click
..\..\venv\Scripts\python price_watch.py watches\llm-parts.json --report       # rebuild the dashboard only
```

The watcher opens **one eBay tab and one Amazon tab** and closes them when it stops.
- Both stores are searched **like a person**: type the query into the site's search box, click its Search button,
  read the results with mouse-wheel flicks, click Next.
- That needs the tab in front during a search. **Drag the two tabs into their own Chrome window** so this doesn't
  interrupt your browsing; it keeps working there.
- If you close a tab, it is reopened at the next search.

It moves at a human pace:
- Random 3-11 s between pages, sometimes a 15-40 s pause.
- Random typing speed and a beat before clicks.
- Checks come about every 10 minutes (8-12), in a shuffled order.

A cycle of the default watch takes roughly 8-12 minutes. `"search_via": "url"` in a store's `module.json` goes back
to fast background loading for that store.

What it searches (your rules, 2026-10-28):
- **eBay:** items located in North America, conditions Open box / New / refurbished / Used
  (`LH_ItemCondition=1500|1000|2000|2010|3000|2020|2030`; "for parts" only when a query says so).
  - Each query runs twice: as Buy It Now and as **auctions** ending soonest.
- **Amazon:** search cards, then the **product page** of each card that would match. There it reads:
  - the real price of the selected option;
  - whether it can actually be bought (buy button + availability);
  - new or used, Prime, and the seller.

  A few pages per product per cycle, cheapest first.
- **Best Buy** (added 2026-10-29): it searches the header box, reads every card down to the end, and records:
  - price;
  - "Comp. Value" (a reference, never the price);
  - "more options from" (Open-Box etc., a note only);
  - availability from the card's shipping / pickup / unavailable messages, for the store Best Buy shows in the
    header.

  Only offers marked in stock count.

Output in `@downloads/watch-<name>/`:
- `dashboard.html` refreshes itself every 60 s. Keep it open. Per product it shows:
  - best per store, and every matching offer with total, landed estimate, condition, Prime and trust;
  - Amazon results that are not buyable;
  - an **auctions table**: time left, current bid, shipping, bid + shipping, bids, watchers;
  - recent alerts.
- `alerts.jsonl` (every alert), `compare.json` (the same report as data), `state.json` (every offer seen, with
  price history; auctions with their bid history).

## Watch file (`watches/<name>.json`)

```json
{
  "name": "llm-parts", "interval_minutes": 10, "tax_rate": 0.07, "stores": ["ebay", "amazon"],
  "reject_sets": {"accessory": ["replacement|cooling fan|..."]},
  "products": {
    "RTX 12GB ($200-$400)": {
      "queries": {"ebay": ["rtx 3060 12gb", "rtx 3080 12gb"], "amazon": ["rtx 3060 12gb"]},
      "min": 150, "max": 420,            // search price range (filters the store's results)
      "max_price": 400,                  // alert ceiling: price + shipping
      "require": ["\\b12\\s?GB\\b"], "reject": ["\\b3060\\s?Ti\\b"], "reject_sets": ["accessory"],
      "prefer": ["900\\s?W"],            // shown as a note, not required
      "suspicious_below": 900,           // matches under this get a CHECK note (far below market)
      "conditions": ["New", "Refurbished"],   // optional: only these conditions
      "ebay": {"min_feedback": 25, "min_feedback_pct": 97, "pages": 1,
               "auctions": true, "auction_details": 3},          // auction search + listing pages read per cycle
      "amazon": {"pages": 2, "min_reviews": 10, "min_rating": 4.0,
                 "verify_per_cycle": 5, "verify_max_age_hours": 6, // product-page checks
                 "require_prime": false},
      "enabled": true
    }
  }
}
```

The first cycle of a new watch is a baseline: it records what's already listed and alerts nothing. Set
`"alert_on_first_cycle": true` to be told about current matches too. `interval_minutes` is never below 5.

## What counts as a match

See `knowledge/rules.md`:
- Store-excluded listings (parts, not working, box only, untested) and auctions never match (auctions have their own table).
- The title rules must pass.
- The item must be in stock, with a known single price and known shipping, at or under `max_price`. On Amazon
  that is confirmed on the product page (real price of the selected option, buy button present).
- The eBay seller needs at least 25 ratings and 97% positive feedback.

Amazon's "used & new offers from $X" is shown, never alerted on (condition unknown).

## Deals: check mark + opportunity banner (`deal_price`)

A product with `"deal_price": N` (price + shipping) gets:
- a green **✓ Opportunity** banner at the top of `dashboard.html` (and `"deals"` in `compare.json`) listing every offer at
  or under N, cheapest first:
  - **Buy now**: a matching Buy It Now / Amazon (page-confirmed) / Best Buy offer;
  - **Buy now (options selected)**: a selectable eBay listing priced by `config_check.py` with the required options
    selected on its page (never its search-card "from" price);
  - **Auction**: current bid + shipping under N and ending within `deal_auction_hours` (default 24). A bid is not a price.
- a ✓ on its row in the product table;
- one alert per deal (again only if the same listing gets cheaper), even on the first (baseline) cycle.

`config_check.py watches/<name>.json` prices selectable eBay listings of products with a `"configure"` block
(`model` regex, `ram_gb_min`, `psu_min_w`): it picks the cheapest in-stock combination from the page's option data,
**selects it menu by menu on the page**, and keeps the price only if the page then shows that price ("confirmed").
Results: `configured.json` next to the dashboard (rows older than 24 h don't count).

### Scheduled build-deals check
`watches/build-deals.json` (RTX 5060 Ti 16GB, Lenovo P520 64GB+, HP Z4 G4 64GB+, 1TB NVMe) runs from Windows Task
Scheduler task **"AI Web-Auto build deals"** at 06:00, 12:00 and 18:00 (every 6 h, never midnight-6 AM; only while
you are signed in; a missed time is skipped, not caught up). `run-build-deals.bat`: one watch cycle -> `config_check.py`
-> `claude -p` with `build-deals-analysis.md` (tools limited to Read/Write/Glob/Grep, `dontAsk`) writes
`@downloads/build-deals-reports/report-<time>.html`, which then opens (the dashboard opens if no report was written).
Each run's log: `@downloads/build-deals-reports/run-<time>.log`.
- Keep Chrome open with AI Web-Auto, its window visible during the run (a hidden eBay tab drops typing; the scripts
  wait for it).
- Run it now: double-click `run-build-deals.bat`. Pause: `schtasks /Change /TN "AI Web-Auto build deals" /DISABLE`.
  Remove: `schtasks /Delete /TN "AI Web-Auto build deals" /F`.
- `--port auto` (default): 8765, or 8766 when another program holds 8765.

## Limits

- Amazon product pages are read through the extension's `chrome.scripting` fallback (another extension blocks the
  debugger there; reload AI Web-Auto after 2026-10-28). Each cycle checks a few pages per product, so a new watch
  needs a couple of cycles before every Amazon offer is confirmed.
- eBay watcher counts come from the listing page and only appear when eBay shows its "N people are watching" signal.
- "Gone" means not in this cycle's results: sold, ended, out of the range, or past the pages read.
- A robot check or sign-in stops the watch with an alert. Solve it in Chrome and start it again.

## Tests

```
venv\Scripts\python -m unittest tests.test_price_watch tests.test_price_report
python @modules\module_kit.py check price_watch
```
