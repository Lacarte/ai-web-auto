# YouTube Quick Save

Save the YouTube video under your cursor to your default playlist with **Ctrl, Ctrl** — never twice, and
never removing anything.

- **Highlight**: on youtube.com the video under (or nearest) the cursor gets a blue frame — tiles on Home and
  channel pages, Shorts, the watch-page sidebar, the hover preview, and the main player.
- **Ctrl, Ctrl** (tap Ctrl twice) saves it. The frame and a message show the result:
  green *Saved*, yellow *Already in …*, red *Not saved: reason*.
- **Popup**: choose the default playlist (read from your YouTube account), **Save all open YouTube tabs**,
  **Close saved tabs**, and recent activity.

No YouTube API key, no Google Cloud project, no backend: it uses the requests YouTube's own Save dialog uses,
with your signed-in session.

## Install

1. `chrome://extensions` → turn on **Developer mode** → **Load unpacked** → select
   `youtube-quick-save-extension/`.
2. Open youtube.com (signed in).
3. Click the extension icon → **Refresh list** → pick your default playlist. The extension reads the
   playlist's video list in the background (about 20 s for 1,600 videos).

After updating the files, click the reload icon on the extension in `chrome://extensions` and reload your
YouTube tabs.

## Use

- Hover a video and tap **Ctrl** twice (the second tap within 0.4 s). Nothing happens while you type in the
  search box or a comment, while holding Shift/Alt, or for shortcuts like Ctrl+C.
- **Save all open YouTube tabs**: saves the video of every open watch / Shorts tab (each video once) and lists
  the result per tab.
- **Close saved tabs**: after a confirmation, closes the YouTube tabs whose video is confirmed in the default
  playlist. Other tabs stay open.

## How it keeps your playlist clean

YouTube allows the same video in a playlist several times, and its "already in this playlist" flag lags
5–30 s behind changes. So the extension:

1. checks it is signed in to the account the default playlist belongs to;
2. skips the video if it is in its local copy of the playlist, was saved moments ago, or YouTube already
   reports it;
3. otherwise adds it once and accepts the save only when YouTube's reply names the new entry;
4. never retries an add; never sends a remove — except one repair: if the same video was added elsewhere
   within the same ~30 s and now appears twice, the background check removes **only the copy this extension
   created**. Duplicates you made yourself are never touched.

## Limits

- It relies on YouTube's internal web requests. If YouTube changes them, saves fail with a visible
  message (they never remove anything); the fix then goes in `js/page/bridge.js` and `js/lib/core.js`.
- The popup's actions need at least one youtube.com tab open (that's where the requests are made).
- A playlist's video list is re-read every 30 minutes and on **Re-read**; until the first read finishes,
  saves rely on YouTube's flag plus the extension's own memory of recent saves.
- Signed out, or signed in to a different account than the default playlist's: nothing is saved
  (**Refresh list** after switching accounts).

## Troubleshooting

| Message | Fix |
|---|---|
| *Pick a default playlist in the extension* | Popup → Refresh list → choose one |
| *Not saved: the extension was updated - reload this page* | Reload the YouTube tab |
| *Not saved: signed in to a different account…* | Switch back, or Refresh list and pick a playlist of this account |
| *Local copy not current: …* | Popup → Re-read |
| *open youtube.com in a tab first* | Open youtube.com, then retry the popup action |

## Privacy

Nothing leaves your browser except YouTube's own requests to youtube.com. Settings, the playlist copy and
the activity log stay in `chrome.storage.local`. Permissions: youtube.com, `storage`, `tabs`, `scripting`,
`alarms`.

## Files

| Path | What |
|---|---|
| `youtube-quick-save-extension/js/lib/core.js` | Video IDs, choosing the target, Ctrl-Ctrl, reading YouTube's replies, the save rules |
| `youtube-quick-save-extension/js/page/content.js` | Highlight, Ctrl-Ctrl, messages (runs on youtube.com) |
| `youtube-quick-save-extension/js/page/bridge.js` | The only part that talks to YouTube (five allowed requests) |
| `youtube-quick-save-extension/js/background.js` | Save queue, local playlist copy, background check, tab actions |
| `tests/` | Unit tests, a local fake YouTube + browser tests, the planted-bug check |
| `PLAN.md`, `TEST_REPORT.md` | Design decisions with the measurements behind them; test evidence |
| `@modules/youtube/` (project root) | What was learned about YouTube, and `youtube_probe.py` (discovery, live check) |

## Tests

`run-tests.bat` runs the unit tests and the browser tests (Playwright Chromium against the local fake
YouTube); `run-tests.bat --mutations` also runs the planted-bug check. Results: `test-results/`,
summary: `TEST_REPORT.md`.
