# Amazon module

Searches Amazon US in your own Chrome session through the ai-web-auto platform. It uses your account's delivery
address, shown in the header and recorded as `deliver_to`. It reads the result cards: price, list price, stock
("Only 7 left", "Temporarily out of stock"), delivery cost and date, rating, reviews, Renewed, and the "used &
new offers from $X" price. Read-only: never adds to cart, buys, or changes the address.

```
cd @modules\amazon
..\..\venv\Scripts\python amazon_search.py search "rtx 3060 12gb" --min 200 --max 400 [--pages 2]
```

The search opens its own tab and closes it at the end. Output: `@downloads/<date>-search-<query>/search.json`.

Used by `@modules/price_watch` for cross-store tracking.

## Knowledge
- `knowledge/locators.json`: card selectors (`data-component-type`, `data-asin`, `data-cy` sections), verified
  live on 2026-10-28.
- `knowledge/rules.md`: price / shipping / availability rules and safety.
- `knowledge/lessons.md`: what was learned, including why product pages are not read (another extension's frame
  blocks the debugger on Amazon product pages in this Chrome).

## Maintain
```
python @modules\module_kit.py check amazon
venv\Scripts\python -m unittest tests.test_price_watch
```
