# PC Server and Parts (PCSP) - rules

## Safety
- Read-only. Never click Add to cart or Configure, and never open the cart.
- A robot check / access-denied page stops the run (`Blocked`). Never bypassed.
- Human pace: random 4-11 s between page loads (sometimes 15-40 s), mouse-wheel reading down each listing page.

## Reading a listing
- A query is a pcserverandparts.com URL (category with facets, e.g. the user's 16GB link) or search words
  (`search.php?search_query=`).
- Price = the bare `$` amount that is not a "Was:" price. "Was" is kept as `was`, a reference, never the price.
- Stock: "N in stock" / "10+ in stock". "Out of stock" / "Sold out" -> `out_of_stock`.
- A build-your-own row ("FROM $X | Custom build | Configure") has a **base price**: chassis + CPU only, add RAM,
  storage and GPU. It is `configure`, never counted as a buyable complete system.
- "Add to cart" present + a price -> `in_stock`.
- **Shipping is not shown on listings** -> `shipping: None`. The tracker then has no total and won't count the
  offer as a match; the report lists PCSP offers apart as "price only".
- Condition comes only from the title (Used / Refurbished / New / Open Box); otherwise it is unknown, not assumed.
