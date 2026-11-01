# Best Buy - lessons

## 2026-10-29 - first live session (the user's link: searchpage.jsp?id=pcat17071&st=rtx%2024gb#&intl=nosplash)
- `intl=nosplash` skips the country-choice splash. Best Buy rewrites the search URL to add `id=pcat17071` and drops
  `intl`.
- The redesigned results page:
  - cards are `li.product-list-item[data-product-id]` (the SKU), about 16 per search, with `data-testid` hooks
    (`price-block-customer-price`, `plp-shipping-messaging-<sku>`, `plp-pickup-messaging-<sku>`,
    `plp-unavailable-<sku>`, `rnr-stats-link`);
  - cards past the first screen have EMPTY innerText until scrolled into view, so read down to the end first;
  - the first product link in a card is the image (no text); the title is the longest link text;
  - the price block reads "$472.99$47299" (the split dollars/cents follow): take the first $X.XX.
- The header search is `textarea#autocomplete-search-bar` with `#autocomplete-clear-button` then
  `#autocomplete-search-button` ("Submit search"). The header is sticky.
- Trusted input: the wheel worked on a search page, but on the home page the debugger was refused ("Cannot access
  a chrome-extension:// URL of different extension" - another extension's frame, as on Amazon product pages).
  Reads go through the `chrome.scripting` fallback; the search falls back to its URL.
- The store was "Rio Hondo Mall" (Best Buy's own pick, not the user's Miami address). For that store every GPU
  read "Shipping unavailable / Pickup unavailable / Find a Store"; some had a disabled "Unavailable".
- Prices seen 2026-10-29: the RTX 3060 12GB (GIGABYTE) was $472.99, with more options from $434.99. Nothing 24GB
  was under $3,159.99 (RTX PRO 4000); the Quadro RTX 4500 24GB was $3,285.99 and unavailable.
- No pagination appeared for these searches (one page, max_pages 1).
