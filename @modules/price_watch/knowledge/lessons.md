# Price watch - lessons

## 2026-10-28 - built from the eBay watcher
- `@modules/ebay/ebay_watch.py` came first (eBay only, newest-first). A watch that looks for a *cheap* offer in a
  range is better served by a cheapest-first search with the range filter: everything in range fits on
  page 1-3, and a new cheap listing surfaces at the top whatever its age.
- Every `navigate` used to bring its tab to the front, so a watcher on a 10-minute timer kept switching the
  user's visible tab. Fix in the extension: `navigate` takes `activate: false`. Reading works in a background
  tab (Runtime.evaluate). CDP *input* doesn't, but a watcher only reads.
- Probes that open `new_tab` on every run left a dozen tabs behind, and the platform couldn't close them.
  Added `close_tab` (bound tab only). Tools open one tab and reuse it.
- Amazon product pages can't be read in this Chrome (another extension's frame blocks the debugger). The watch
  works from search cards. See `@modules/amazon/knowledge/lessons.md`.
- Market reality when the watch was set up (eBay research of the same day): RTX 3090 sold median ~$1,599, RTX
  3060 12GB ~$338. Amazon new RTX 3060 12GB $449-$472. A 24GB card at $450-$600 is far below market, so matches
  carry a CHECK note.
- 2026-10-30: a selectable eBay listing's search card ("$269.99 to ...") is its cheapest option; two HP Z4 G4 listings
  titled "64GB RAM" show $269.99 for a smaller configuration. Price the 64GB option on the page (config_check.py).
- 2026-10-30: when the Chrome window is behind other windows, `activate_tab` makes the eBay tab active but Chrome still
  reports it hidden and drops typing. Scripts now poll `document.visibilityState` and wait instead of failing a search.
- 2026-10-30: `claude -p` refuses to start inside another Claude Code session (CLAUDECODE is set); the scheduled run
  is outside any session, so test it from Task Scheduler or by double-clicking run-build-deals.bat.
- 2026-10-30: a blocked store (robot / human check not completed) stopped the whole cycle. Now only that store is
  skipped for the cycle, one alert ("<store> needs you"), shown on the dashboard; the other stores continue.
- 2026-10-30: an eBay search card showed MSI RTX 5060 Ti 800020842561 at $617.99 while its page said "This item is out
  of stock" (no Buy It Now button). The dashboard made it the best offer. Each cycle now reads the listing pages of the
  cheapest matching eBay offers (verify_per_cycle 3, reused 6 h); out of stock drops the offer.
