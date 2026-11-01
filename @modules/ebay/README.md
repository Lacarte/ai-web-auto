# eBay product search module

Searches eBay US through the ai-web-auto platform (your own Chrome tab, your own session), reads the
listings that matter, and ranks what you could buy by **landed cost to your ZIP** and **seller trust**.
Read-only: it never bids, buys, watches, offers or adds to cart.

## Use

Stop `runner.bat` first (the module starts the backend on `:8765`); keep an eBay tab open in Chrome.

```
cd @modules\ebay
..\..\venv\Scripts\python ebay_search.py search "rtx 3060 12gb" --min 150 --max 450     # Buy It Now, ships to the ZIP
..\..\venv\Scripts\python ebay_search.py search "rtx 3060 12gb" --sold                  # sold comps (market price)
..\..\venv\Scripts\python ebay_search.py read 128147436297 406123456789                 # full listing check
..\..\venv\Scripts\python ebay_search.py job jobs\llm-workstation.json                  # a whole shopping goal
..\..\venv\Scripts\python ebay_search.py job jobs\llm-workstation.json --no-search --no-read   # re-rank from cache
```

Outputs go to `@downloads/<date>-<name>/`: `results.json`, `report.md`, `dashboard.html`, and
`evidence/` (page JSON + screenshot per listing, timestamp, sha256).

## What it enforces

See `knowledge/rules.md`. In short: parts / not working / untested / box-only listings are excluded
(URL condition filter + title rules); auction bids are never prices; landed = price + shipping +
7% FL tax; sellers under 25 ratings or 97% are rejected; description risk phrases and title/spec
contradictions go to **review**; a CAPTCHA or sign-in stops the run for you.

## Jobs

`jobs/<name>.json` describes a goal: components with searches, title `require`/`reject` rules,
`spec_checks` on item specifics, `mismatch` rules, and a `catalog` of compatibility facts with
manufacturer sources. `jobs/job_runner.py` searches, reads the cheapest candidates, applies the
rules, assembles builds (cheapest / best value / dual-GPU ready) and writes the reports.

## Maintain

```
python @modules\module_kit.py check ebay            # offline
python @modules\module_kit.py verify ebay --write   # on an open item or search page
venv\Scripts\python -m unittest tests.test_ebay_module
```

## Watch for new listings

To track products on eBay **and Amazon** with a price comparison, use `@modules/price_watch` (it drives this
module). The eBay-only watcher below still works.

```
..\..\venv\Scripts\python ebay_watch.py watches\gpus.json --test-alert   # check sound + notification
..\..\venv\Scripts\python ebay_watch.py watches\gpus.json --once         # one cycle (the first is a baseline)
..\..\venv\Scripts\python ebay_watch.py watches\gpus.json                # every interval_minutes until Ctrl+C
```

Edit `watches/gpus.json` (queries, max_price, require/reject, conditions, min_feedback, interval). State and
alerts: `@downloads/watch-<name>/state.json`, `alerts.jsonl`. Alert only - it never buys.
