# YouTube Quick Save — Plan

Plan agreed on 2026-10-25, after live discovery and a save test on YouTube. What was learned lives in the
`@modules/youtube` module (knowledge, lessons, and `youtube_probe.py`, which ran the tests); raw evidence in
`@modules/youtube/@downloads/discovery/` (local, gitignored); anonymised replies in `tests/fixtures/youtubei/`.

## What it does

1. On youtube.com it highlights the video under or nearest the cursor: tiles on Home, channel pages, the
   watch-page sidebar, Shorts, and the main player.
2. **Ctrl, Ctrl** (two quick taps) saves that video to your default playlist — never a duplicate, never a
   removal.
3. The popup lists your playlists (read from YouTube) and sets the default.
4. The popup can **save every open YouTube video tab** at once, and **close the tabs** whose video is
   confirmed in the playlist.

## Decisions

| Topic | Decision |
|---|---|
| How to save | YouTube's own request, with your session (no API key, no debugger banner). If YouTube changes it, saves fail visibly; the Save-dialog route becomes the fallback then. |
| Delete button | Closes saved tabs, after a confirm, only for tabs whose video is confirmed in the playlist |
| Storage | `chrome.storage.local` |
| Live testing | Only on the private `zz quick-save test` playlist |
| Backend | None — everything runs in the browser |
| Shorts | Included |

## What the live tests proved

| Fact | Consequence |
|---|---|
| Playlists can be listed without opening a video (`/youtubei/v1/playlist/get_add_to_playlist`: 103 playlists with ID, title, privacy) | Settings load with no side effects |
| YouTube **allows duplicates**: each add (`/youtubei/v1/browse/edit_playlist`, `ACTION_ADD_VIDEO`) created another copy | The presence check is essential |
| The "contains this video" flag in the playlist list **lags 5–10 s** after a change | Used only to list playlists; never to decide whether to add |
| The playlist's real contents (`/youtubei/v1/browse`, `VL<playlistId>` + continuations) are **current immediately** | Saving decides from the real contents |
| `ACTION_REMOVE_VIDEO_BY_VIDEO_ID` removes *all* copies at once | Never used when saving; duplicate repair removes one extra copy by its own `setVideoId` |
| Video tiles are `yt-lockup-view-model`, Shorts `ytm-shorts-lockup-view-model`, on Home, channel pages and the watch sidebar | One way of finding the video works everywhere |
| After ~2 s of hover, an inline preview (`ytd-video-preview`, `#inline-preview-player`) covers the thumbnail, carrying the same video link | Finding the video looks through the preview |
| Requests need the session's SAPISID hash headers (`SAPISIDHASH`, `SAPISID1PHASH`, `SAPISID3PHASH`); the cookies are readable by page scripts | The page-context bridge signs requests the way YouTube's web client does |
| The add reply names the new entry: `playlistEditResults[].playlistEditVideoAddedResultData {videoId, setVideoId}` | A save is confirmed by YouTube itself, and a repair can remove exactly the entry we created |
| `ACTION_REMOVE_VIDEO` with a `setVideoId` removes exactly that one entry (verified: the other video stayed) | The duplicate repair is safe |
| A playlist page has two continuation tokens; only the one at the end of the video list pages the videos | The reader follows that one |
| **A full read of a 1,612-video playlist takes 15–21 s** (16 pages of 100; unavailable videos are not returned: 1,589 entries) and that playlist already holds 3 duplicate entries | Reading the whole playlist per save is far too slow → keep a local copy of the playlist's video list (see "Saving one video"); never touch duplicates the extension didn't create |

## How it's built

`@created-extensions/youtube-quick-save/youtube-quick-save-extension/`

- **Page script** (isolated world on youtube.com): throttled cursor tracking; finding the video ID under or
  nearest the cursor (through the preview); a highlight drawn above the page that never blocks clicks;
  Ctrl, Ctrl detection (two taps within 400 ms, nothing in between, ignored while typing and during Ctrl+key
  shortcuts); the result shown as *Saved* / *Already there* / *Failed*.
- **Page-context bridge** (main world): the only part that talks to YouTube. It signs and sends three kinds of
  request only — list playlists, read a playlist, edit a playlist (add a video, or remove one entry by its
  own `setVideoId`) — and rejects anything else.
- **Core logic** (`js/lib/core.js`, shared and unit-tested): video IDs from links, nearest-tile choice, the
  hotkey state machine, response parsing, and the save steps.
- **Background coordinator** (service worker): settings; one save queue (the same video saved from two tabs
  can't race); a short memory of recent adds; the open-tabs batch and closing saved tabs.
- **Popup**: the signed-in account check, a playlist picker with Refresh, the two tab buttons with a
  per-tab result, and recent activity.
- Permissions: youtube.com, `storage`, `tabs`, `scripting` (to reach YouTube tabs opened before install).
  No debugger.

### Saving one video

The background keeps a **local copy of the default playlist's video list**: a full read (15–20 s for
~1,600 videos, in the background) when the default is chosen, at startup, when it is older than 30 minutes,
and on Refresh; the extension's own adds update it instantly.

1. Signed in, and to the account the default playlist belongs to — otherwise stop.
2. In the local copy, or saved by the extension moments ago → *Already there*, nothing sent.
3. One fast request for the "contains this video" flag (~0.6 s; catches changes made elsewhere more than
   ~30 s ago, and confirms the playlist still exists). `ALL` → *Already there*.
4. Otherwise send YouTube's add command; require `STATUS_SUCCEEDED` and the reply's
   `playlistEditVideoAddedResultData` for this video → *Saved*, remember its `setVideoId`.
5. Background check (one full read, shortly after the last save): a video the extension added that now
   appears twice (someone added it elsewhere in the same ~30 s) → remove **only the entry the extension
   created**, by its `setVideoId`, and log it. Duplicates the extension didn't create are never touched.

Open-tabs batch: the same checks per video, adds one at a time, then one background check.
Close saved tabs: closes a tab only if its video is in the local copy or its flag says `ALL`.

## Steps

- **A — Record what was learned**: `@modules/youtube` module (pages, element patterns, the requests,
  lessons); recorded YouTube responses as test fixtures, with names and IDs replaced.
- **B — Core logic + unit tests**: video IDs from `watch?v=` / `/shorts/` links, nearest tile, the Ctrl,
  Ctrl detector, the save steps against a fake YouTube that lags, duplicates and fails, response parsing.
- **C — The extension**: page script, bridge, coordinator, popup.
- **D — Test site + Playwright**: a local copy of the hard parts (tile grid, Shorts, hover preview, watch
  page with player and sidebar, YouTube-style navigation, a fake playlist service that lags and duplicates).
  Tests for: highlight on the right tile, Ctrl, Ctrl saving and being ignored in text boxes, double press
  never duplicating, an error writing nothing, the batch and closing tabs, a wrong account stopping the save.
  Run twice; then the planted-bug check.
- **E — Live check in your Chrome, test playlist only**: Home tile (also while its preview plays), channel
  tile and Short, sidebar tile, main player, double press, already-saved video, three open tabs, closing
  saved tabs — each confirmed on the playlist's real contents and on YouTube's playlist page; the test
  playlist cleaned afterwards.
- **F — Documentation**: README (install, use, settings, limits, troubleshooting) and `TEST_REPORT.md` with
  evidence. Then you switch the default to your real playlist.

## Done means

- No duplicates and no removals in any live scenario, confirmed on YouTube's playlist page.
- Correct highlight on the three example pages (including the preview and the player):
  `https://www.youtube.com/`, `https://www.youtube.com/@RayCodingCorner`,
  `https://www.youtube.com/watch?v=xxf6D9jynFw`.
- All tests pass twice; planted bugs are caught; failures are visible and change nothing.

## Risks

- **YouTube changes its request** → saves fail visibly; then add the Save-dialog route.
- **YouTube changes tile structure** → finding videos relies on links; tile types are one list in core.
- **Large playlists** → measured 15–21 s for a full read of 1,612 videos, so saves use the local copy plus
  one flag request (target: a save under 2 s). Remaining gap: the same video added elsewhere within ~30 s
  before Ctrl, Ctrl, while the local copy didn't know it → a duplicate for a short time, removed by the
  background check (only the extension's own entry).

## What the user does

Nothing until step E. Then: stay signed in, keep AI Web-Auto loaded, "Load unpacked" this extension, and
pick `zz quick-save test` as the default in the popup.
