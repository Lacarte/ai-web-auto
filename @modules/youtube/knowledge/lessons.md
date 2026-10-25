# youtube — lessons

- **YouTube allows duplicates in a playlist:** each `ACTION_ADD_VIDEO` creates another entry, even when the
  video is already there (3 copies in the first live test). Check before adding.
- **The "contains this video" flag lags:** `containsSelectedVideos` from `get_add_to_playlist` stayed `NONE`
  right after an add and `ALL` for 5-10 s after a removal. A check based on it let a quick second save
  create a duplicate.
- **Playlist contents are current immediately:** `browse` with `VL<playlistId>` reflected every add and
  remove at once, as does the playlist page.
- **Reading a big playlist is slow:** 1,612 videos took 15-21 s (16 pages of 100, 0.6-4.4 s per page).
  Keep a local copy of the video list; don't read it on every save.
- **Follow the right continuation:** a playlist page has two continuation tokens. Only the
  `continuationItemRenderer` at the end of the video list pages the videos; the other returns nothing useful.
- **The add reply names the new entry** (`playlistEditVideoAddedResultData {videoId, setVideoId}`), so a
  repair can remove exactly the entry that was just created.
- **Remove-by-video removes every copy;** remove-by-`setVideoId` removes exactly one (verified live).
- **The hover preview covers the thumbnail** after ~2 s: `elementFromPoint` returns the preview's `<video>`
  (`#inline-preview-player`); the preview links to the same video.
- **Tiles are `yt-lockup-view-model`** (Shorts: `ytm-shorts-lockup-view-model`) on Home, channel pages and
  the watch sidebar; the old `ytd-*-renderer` tiles were not present on 2026-10-25.
- **Requests need the session's SAPISID hash headers** (`SAPISIDHASH`, `SAPISID1PHASH`, `SAPISID3PHASH`
  from the matching cookies) plus `X-Goog-AuthUser`, `X-Origin`, `X-Youtube-Client-Name/Version`.
- **Opening a watch page adds to watch history;** listing playlists needs no watch page.
