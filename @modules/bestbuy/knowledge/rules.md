# Best Buy - rules

## Safety
- Read-only. Never click Add to cart, Save, "+ N offers for you", Find a Store, or anything that changes the store
  or location. Availability is read for the store Best Buy shows in the header ("Your store ..."), recorded, and
  never changed.
- A robot check / "Access Denied" stops the run (`Blocked`). Never bypassed: the human solves it in Chrome.
- Human pace, as in the other stores:
  - random 4-11 s between page loads, sometimes 15-40 s;
  - typing 45-140 ms/char into the header box, then a trusted click on Submit search;
  - reading down the list with mouse-wheel flicks.

## How a search is made (2026-10-29)
- The search uses the header box (`search_box`, a textarea) and Submit search (`search_submit`). Never the Clear
  button next to it.
- If typing can't reach the page, the same search is opened by URL (`/site/searchpage.jsp?st=<query>&intl=nosplash`)
  and the page records it. Typing fails in a hidden tab, and when another extension's frame blocks the debugger
  (seen on the home page).
- The whole list is read down to its end: a card renders its content only when scrolled into view.

## Prices
- `price` = `card_price` (price to pay). "Comp. Value" is `comp_value`, a reference, never the price.
- "More options from $X" is `more_options_from`: other offers such as Open-Box, condition not shown. It is a
  note, never a price.
- Shipping is 0 only when the card says FREE. Otherwise it is unknown (None), so the offer has no total and
  cannot match.

## Availability (from the card's own messages)
- Disabled "Unavailable" / "Sold Out" -> `out_of_stock`.
- "Add to cart", or a shipping message that isn't "unavailable" -> `in_stock`.
- Pickup available but shipping unavailable -> `pickup_only`.
- "Shipping unavailable" + "Pickup unavailable" (+ "Find a Store") -> `unavailable_here`: not buyable for the
  user's store/location.
- Only `in_stock` counts as available.
