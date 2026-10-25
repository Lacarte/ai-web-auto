# youtube — decision rules

## Identity
- A video is its 11-character ID from a link (`watch?v=ID`, `/shorts/ID`) or `ytd-watch-flexy[video-id]`.
  Never a tile position: YouTube reuses tile elements for other videos as you scroll and navigate.
- A playlist is its ID (`WL` for Watch later, `PL…` otherwise); titles are only for display.
- A playlist entry is its `setVideoId` — the only safe handle for removing one copy.

## Preconditions and safety gates
- Signed in (`ytcfg.LOGGED_IN`) and on the account the default playlist belongs to (`ytcfg.DATASYNC_ID`).
- Add only when the video is not in the playlist: YouTube does not deduplicate.
- Never decide on the "contains" flag alone; it lags behind changes.
- Never retry an add automatically. If an add's outcome is unknown, read; don't repeat.
- Removing is allowed only to repair a duplicate the extension created, by that entry's `setVideoId`.
  Never `ACTION_REMOVE_VIDEO_BY_VIDEO_ID` (it removes every copy).
- Duplicates that already exist in a playlist are the user's; never touch them.

## When an action counts as done
- Add: `STATUS_SUCCEEDED` plus `playlistEditVideoAddedResultData` for that video.
- Independent check: the playlist page (`/playlist?list=ID`) shows exactly one copy.
