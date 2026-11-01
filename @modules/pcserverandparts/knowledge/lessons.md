# PC Server and Parts (PCSP) - lessons

## 2026-10-29 - first live session (the user's link: refurbished-graphics-cards/?_bc_fsnf=1&Graphics+Card+Memory=16GB)
- BigCommerce store. Listings carry `data-entity-id` and `data-sku`. Three layouts:
  - product cards `article.card` ('SAVE $268.97 | GDDR5X | <title> | Was: $461.99 | $193.02 | 10 in stock | Add to cart');
  - ready-system rows `.pcspRdy__row` (specs | price | stock | Add to cart; no title element);
  - build-your-own rows `.pcspByoL__row` (a link: 'P520 900W, 2nd Gen Xeon | $184.98 | Configure').
- The refurbished GPU memory facet went up to 16GB (2, 4, 1, 8, 6, 16, 12 GB). The 16GB filter had 2 cards:
  - Dell Quadro P5000 16GB GDDR5X, Used, $193.02 (was $461.99), 10 in stock. The P5000 is on Lenovo's P520
    graphics list (180W).
  - Apple Radeon Pro W5700X 16GB, $569.99: an MPX module for the Mac Pro, not a PC card.
- `workstations/lenovo-workstations/lenovo-p520-thinkstation/`:
  - 4 build-your-own bases: $159.98 (690W) and $184.98 (900W);
  - ready systems from $706.92 (W-2133, 32GB, K620) to $1,645.77 (W-2245, 128GB, P2000);
  - barebones "No RAM / No drive / No GPU" from $164.98 (W-2123), e.g. $204.98 (W-2135) and $281.98 (W-2145).
- `lenovo-p620-thinkstation/` returns "PCSP - Not Found". P620 listings come from `search.php?search_query=thinkstation+p620`:
  - build-your-own from $1,364.98 (3945WX);
  - many 3945WX / 128GB / RTX 3080 10GB systems, plus one with 256GB and an RTX 3090 24GB;
  - P620 parts (motherboard $649.99, fan duct), which the watch's `not_a_workstation` set rejects.
- The probe's wheel scroll fell back to scrollBy on these pages (the debugger may be refused here too); the reads
  went through fine.
