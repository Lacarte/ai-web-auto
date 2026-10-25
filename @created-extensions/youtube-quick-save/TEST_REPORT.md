# YouTube Quick Save — Test Report

Date: 2026-10-25 · Windows 11 · Node 24.14 · Playwright 1.64.1 (Chromium) · Python 3.10

| Suite | Result | Evidence |
|---|---|---|
| Live discovery and measurements on YouTube (before building) | done | `PLAN.md` "What the live tests proved"; `@modules/youtube/knowledge/lessons.md` |
| Unit tests (`tests/unit`, recorded YouTube replies, anonymised) | **20/20** | `node --test "tests/unit/*.test.mjs"` |
| Browser tests against the local fake YouTube (`tests/e2e`) | **17/17 in 5 consecutive runs** | `test-results/e2e-results.json`, `test-results/screenshots/` |
| Planted-bug check (`tests/mutation`) | **19/19 caught** (14 unit, 5 browser) | `test-results/mutations.json` |
| Live use on YouTube with the installed extension | **works** (confirmed by the user, 2026-10-25) | hands-on use; the scripted live check (`@modules/youtube/youtube_probe.py live-check`) has not been run |

## What was measured on YouTube first

These facts shaped the design; each has a test that would fail if the code ignored it.

| Measured | Test that holds the code to it |
|---|---|
| Each add creates another copy (3 copies of one video in the first live test) | Unit: "two quick saves leave one copy"; browser: "pressing again adds no second copy", "two tabs, same video" |
| The "contains" flag lagged 5–10 s after changes | Fake YouTube lags 3 s; browser: "two tabs, same video … the lagging flag is not trusted" |
| A playlist page carries a decoy continuation next to the video-list one | Unit: "continuation at the end of the video list, not the section one" (recorded reply); browser: local copy of a 3-page playlist ignores the decoy |
| The add reply names the new entry (`setVideoId`) | Unit: "an add counts only when YouTube confirms this video" |
| Remove by `setVideoId` removes exactly one entry | Browser: "added elsewhere moments before: only the copy this extension made is removed" |
| The hover preview covers the thumbnail | Browser: the test first proves `elementFromPoint` is inside the preview, then checks the highlight |
| Full read of 1,612 videos: 15–21 s | Saves never wait for a full read (local copy + one flag request) |

## Browser tests (fake YouTube, real Chromium, extension loaded)

Every check reads the fake server's real playlist state or the page, not the extension's own report.

| # | Scenario | Checked |
|---|---|---|
| 1 | Popup lists the playlists and keeps the chosen default | 4 playlists; default survives reopening |
| 2 | Local copy reads every page and ignores the decoy continuation | 7 videos over 3 pages; no decoy request in the server log |
| 3 | Home: highlight on the thumbnail under the cursor | frame within 2 px of the thumbnail (x, y, width, height); badge names the playlist |
| 4 | Home: between tiles the nearest one | 7 px right of tile 1 → tile 1 |
| 5 | Home: hover preview seen through | preview proven on top; highlight still the right video (`kind: preview`) |
| 6 | Ctrl, Ctrl saves once | exactly one copy; toast "Saved to zz quick-save test" |
| 7 | Pressing again adds no second copy | still one copy; one add request in total |
| 8 | Ctrl shortcuts, slow taps, typing never save | slow taps, Ctrl tap + Ctrl+C, Ctrl, Ctrl in the search box → zero add requests |
| 9 | Watch page: main player and sidebar | player → playing video; sidebar tile saved once (reached by in-page navigation) |
| 10 | Channel page: a Short | highlighted and saved once |
| 11 | Two tabs, same video, one after the other | one copy (flag lagging) |
| 12 | Added elsewhere moments before | duplicate appears, background check removes **the extension's** copy; the other device's copy kept |
| 13 | A failed add | "Not saved" shown; exactly one add request (no retry); nothing added |
| 14 | Different account | refused; zero add requests |
| 15 | Save all open YouTube tabs | 2 videos in 3 tabs → each once; "2 video(s) saved, 0 already there, 1 tab(s) without a video" |
| 16 | Close saved tabs | closes the 2 tabs whose video is saved; the unsaved watch tab and Home stay |
| 17 | All requests signed; remove-by-video never used | server log |

Screenshots: `01-home-highlight`, `02-home-preview`, `03-saved`, `04-player`, `05-sidebar-saved`,
`06-popup-save-tabs`, `07-popup-closed`.

## Planted-bug check

One bug at a time, in a copy of the code; the suites must fail.

| Planted bug | Caught by |
|---|---|
| target choice prefers the outer box | unit |
| Ctrl, Ctrl fires while typing / ignores the time window / fires on the second press | unit (3) |
| follows the decoy continuation | unit |
| an unreadable reply is taken as an empty playlist | unit |
| an add counts without YouTube naming the video | unit |
| account not checked | unit |
| recent-adds memory / local copy / YouTube's flag ignored | unit (3) |
| a failed add is retried | unit |
| repair removes the other device's copy / copies the extension didn't create | unit (2) |
| Ctrl, Ctrl counts while typing in the page | browser |
| highlight covers the whole tile, not the thumbnail | browser |
| requests not signed | browser |
| duplicate repair never runs | browser |
| close saved tabs closes every video tab | browser |

## Bugs found by testing, and fixed

- **Ctrl tap followed by Ctrl+C saved the video**: the gesture completed on the second Ctrl *press*. It now
  completes on the second Ctrl *release* with no other key in between (unit + browser regression tests).
- **Choosing a default playlist didn't stick**: the popup redrew the dropdown before reading the choice.
- **Pagination followed the wrong token** (found during discovery): pages carry a decoy continuation.
- **An empty playlist read as an error**: the video-list container alone now counts.

## Not covered yet

- The scripted live check (every scenario on real YouTube, counted on the playlist page, then cleaned up)
  has not been run; the user confirmed by hand that the extension works on YouTube.
- Real YouTube was not used for the browser suite; the fake reproduces the measured behaviour.
