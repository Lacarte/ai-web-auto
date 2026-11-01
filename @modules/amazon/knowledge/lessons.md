# Amazon - lessons

## 2026-10-28 - first live session (search pages, the user's Chrome, delivering to Miami 33182)
- Search cards are stable to read: `[data-component-type="s-search-result"]` with `data-asin`, and `data-cy`
  sections (`title-recipe`, `price-recipe`, `delivery-recipe`, `secondary-offer-recipe`, `reviews-block`).
  No robot check at a few searches per minute.
- The price range filter: `low-price`/`high-price` URL params redirect to `rh=p_36:<min cents>-<max cents>`.
  Use the rh form directly. Nothing in range -> "No results for your search query." (zero cards).
- `s=price-asc-rank` sorts cheapest first. `s=date-desc-rank` was ignored for a P520 query (still "Featured").
- Read cards only after `document.readyState === 'complete'` and a stable card count. The first poll once
  saw just 2 sponsored cards out of 18.
- For sponsored and some brand cards, `h2` holds only the brand ("Lenovo", "Amazon Renewed"). The full title is
  the whole `title-recipe` text.
- **Product pages couldn't be read at first.** `Runtime.evaluate` needs `chrome.debugger.attach`, and Chrome
  refuses it ("Cannot access a chrome-extension:// URL of different extension") because another installed
  extension injects a `chrome-extension://` frame into Amazon product pages, not into search pages. **Fixed
  later the same day:** `execute_script` now falls back to `chrome.scripting` in the MAIN world (indirect eval;
  extension capability `script_fallback`). Product pages read fine that way. Trusted input (wheel, CDP clicks)
  still can't reach them, so scrolling there falls back to `scrollBy`.
- The platform had no close-tab command: every `navigate new_tab` left a tab behind. `close_tab` was added; probes
  and watchers reuse one bound tab.
- **Search via the box (the user's instruction).** The user's absolute XPath
  `/html/body/div[1]/header/div[1]/div[1]/div[2]/div/form/div[3]/div/span/input` is the same element as
  `#nav-search-submit-button` (checked live: `<input type="submit" value="Go">`). Registered by id. `type_cdp`
  into `//input[@id='twotabsearchtextbox']` + a CDP click on the button opens `…/s?k=…&crid=…&sprefix=…&ref=nb_sb_noss_2`.
  The sort and price range are then added to that URL.
- **The first Next click missed.** The link's rect was measured right after `scrollIntoView` while Amazon was still
  smooth-scrolling. Scroll (instant) in one call, wait, measure in the next: the click then lands
  (`"via": "click"`).
- Sponsored card titles start with Amazon's disclaimer run together:
  `SponsoredSponsored You’re seeing this ad … search query.Leave ad feedback  <title>`. It is stripped and the
  card marked `sponsored`. Sponsored cards are often unrelated (a camera filter in a GPU search).
- **Scrolling like a person (the user asked).** `scroll` in the platform was `window.scrollBy`. Added a `wheel`
  command: trusted CDP `mouseWheel` notches at a pointer position. `module_kit.human_scroll` flicks 3-8 notches of
  ~100 px, pauses 0.4-1.9 s, sometimes goes 1 notch back up, and rests the pointer over the middle of the page.
  Live: 18 flicks down a results page, then the Next click landed. Wheel input needs the tab in front.
- Human-paced search (random 4-11 s between loads, sometimes 15-40 s, random think time, typing 45-140 ms/char,
  reading down before Next): 2 pages in ~41 s, no robot check.
- **Product pages, read live** (`scratch/amazon/probe_product.py`, `probe_buybox.py`):
  - B0BK36KLDD: PNY 3060 12GB Renewed, $399.97, "Only 1 left", third-party seller (Respec Store), **no Prime**.
  - B0CTK1SFVD: MSI 3050, $259.97, sold by Amazon.com, **Prime**. It also has a hidden `#usedOfferListingID`,
    so that input is no used signal.
  - B07QC8RSZB: ZOTAC 2060. Its **search card had no price**; the page offers only a **used** copy ("Buy Used :
    $198.22 · Used: Very Good · Sold by Atomic Cellular") through `#add-to-cart-button-ubb`, with no
    `#add-to-cart-button`.
  - B0FFGCKW2T: the user's variation-menu example. The selected option (`li[data-initiallyselected=true]`,
    `input[aria-checked=true]`) is "10*2.5*0.35inch F62G0" at $25.99. Its `.a-offscreen` is **empty**; the price
    is in the `aria-hidden` span and the `apex-pricetopay-accessibility-label`. Options without Prime simply lack
    the `i.a-icon-prime` slot.
  - `.priceToPay` text is "$399 . 97" (dollars, dot, cents on separate lines): join the text without whitespace
    before parsing.
  - One product page timed out once ("none of ['product_title'] appeared") and loaded fine on the next run. A
    failed read leaves the offer unconfirmed; it is not guessed.
- Amazon searches for "lenovo thinkstation p520" return mostly new P3/P5/P8 machines. Used P520s are rare on
  Amazon (one "Amazon Renewed" $663.93, sponsored). Title rules must require "P520".
- 2026-10-30: Amazon showed an "I am human" checkbox page (`#tmms-challenge`, `form#checkpointForm`, "Check the box to
  continue") that the old robot-check signals missed. It is registered as `human_check`. The script never ticks it:
  `wait_for_person` alerts, brings the tab forward and waits (human_wait_s, default 300) until the person has done it,
  then continues; otherwise Blocked, and the price watch skips Amazon for that cycle.
