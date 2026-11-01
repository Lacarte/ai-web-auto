# Amazon - rules

## Safety
- Read-only. Never click Add to cart, Buy Now, See options, the delivery address, or anything in the cart flyout
  (the user's cart has items; it is theirs).
- Navigate with `dismiss_banners: false`: the platform's banner dismissal clicks "Close" buttons, which a reader
  has no reason to do on a store.
- A robot check (`captcha_form`, "Enter the characters you see below") or a sign-in page stops the run with
  `Blocked`. Never solved, never retried around. The human solves it in Chrome.
- Human pace (the user asked, 2026-10-28):
  - Random 4-11 s between page loads, and 12% of the time a longer 15-40 s read (`pause_seconds`, `long_pause`;
    `module_kit.human_delay`).
  - A random beat before typing and before each click; typing speed varies per search (`type_ms_per_char`).
  - Scroll down the results with the mouse wheel (`human_scroll`: trusted wheel flicks with reading pauses,
    sometimes a notch back up) until Next is in view, then click it. An extension without `wheel` falls back to a
    script scroll.
  - A watcher polls no faster than every 5 minutes.
  - This is polite pacing of the user's own low-volume session. It is not used to get past a block: no CAPTCHA
    solving, no fingerprint/user-agent spoofing, no proxies. A robot check stops the run for the human.

## How a search is made (the user's instruction, 2026-10-28)
- Type the query into the header search box and click the search button (`search_submit`, the element at the
  user's XPath `/html/body/div[1]/header/.../form/div[3]/div/span/input`, registered by its id).
- Trusted input only (CDP click + insertText). Never `el.click()` or setting `value`.
- Before clicking, check that the box holds exactly the query and the button is visible and uncovered; otherwise
  stop. Never click blind.
- Trusted input is dropped in a hidden tab, so the bound Amazon tab comes to the front for the typing and the
  click. A watcher that must stay fully in the background sets `search_via: "url"`.
- Sort and price range are then applied to the results URL Amazon produced (the same page its sort menu and price
  filter lead to). More pages: click Next.

## Prices
- `price` is the featured offer on the card. A struck-through price is `list_price`, never the price.
- No price on the card ("See options", no featured offer) -> `availability: no_offer`; nothing is tracked as a price.
- Shipping: "FREE delivery" -> 0 (flag `prime_only_free` when it says "for Prime members"); "$X delivery" -> X;
  anything else -> unknown (None), and a total isn't computed.
- Landed estimate = (price + shipping) x (1 + sales_tax_rate). Amazon charges FL sales tax at checkout. 7% is an
  estimate for Miami-Dade.
- "More Buying Choices $X (N used & new offers)" is `offers_from`: the lowest other offer, condition and
  shipping unknown. It is shown, never treated as a buyable price, and never alerted on.
- Prices and dates are for the delivery address in the header (`deliver_to`, read and recorded, never changed).

## Condition and availability (search card)
- "Renewed" in the title -> condition Renewed; otherwise the card's offer is taken as New until the product page
  says otherwise.
- "Temporarily out of stock" / "Currently unavailable" -> `out_of_stock`. "Only N left in stock" -> `stock_left`.
- Title says for parts / not working / broken / defective / as-is -> excluded.
- Prime: the card's `card_prime` badge -> `prime: true`; no badge -> false. "FREE delivery for Prime members" is
  `prime_only_free`, a different thing.

## The product page is the truth (the user's instruction, 2026-10-28)
Some search results show a price but can't be bought, and a card's price can belong to another option. So an
offer counts only after `check_product` (`AmazonSession.product`) has read its product page:
- **Real price = the selected option's price** in the variation menu (`selected_variant` -> `variant_price`, the
  option whose `data-asin` is the page ASIN; the user pointed at it with an absolute XPath). With no menu, the buy
  box price (`core_price`). If the option and buy box disagree, or the card said something else, it is noted.
- **Available = a buy button AND an availability line that doesn't say unavailable.** The buy button is
  `add_to_cart`, `add_to_cart_used` or `buy_now`. Otherwise the offer is:
  - `out_of_stock` ("Currently unavailable", "Temporarily out of stock", or the option itself unavailable);
  - `no_featured_offer` ("See All Buying Options");
  - `not_buyable` (no button);
  - `no_price`.
  A page can say "In stock" without a buy button.
- **New or used:** a buy box that sells used ("Buy Used", only `add_to_cart_used`) -> condition "Used - <grade>".
  `#usedOfferListingID` alone means nothing: it is also on new-offer pages.
- **Prime:** a visible `buybox_prime` badge or the selected option's `variant_prime`.
- **Shipping:** from `delivery_block` ("FREE delivery" -> 0; "$X delivery" -> X). "FREE ... for Prime members" is
  not assumed free.
- **Seller:** from `merchant_info` / the buy box ("Shipper / Seller", "Sold by").
- Add to cart and Buy Now are read for **presence only and never clicked**.

## Relevance
- A price-ascending search returns accessories (replacement fans), smaller cards (6GB, RTX 2060, RTX 3050) and
  whole PCs. Amazon's ranking is not a match: the caller's title rules (require / reject) decide.
