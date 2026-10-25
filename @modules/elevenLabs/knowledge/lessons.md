# ElevenLabs Music — lessons learned

What was found to work on this app, and why. Add to it when something new is learned; remove a
lesson when the app changes and it no longer holds.

- **Identify tracks by project URL, not list index.** React rerenders and deletions reorder the list;
  the `/app/music/project/<id>` link stays stable.
- **A CDP click can be acknowledged but never delivered** (seen after navigations with some extension
  builds). Detect it by the missing result (menu/dialog didn't open), then call the element's live React
  handler (`__reactProps$…onPointerDown` for menu triggers, `onClick` for items) on the same, re-checked
  element. Remember the fallback for the rest of the run (`_cdp_input_broken`).
- **Radix menus belong to their trigger:** match `[role="menu"][data-state="open"]` whose
  `aria-labelledby` equals the button id you clicked — another row's menu can be open at the same time.
- **The model picker and lyrics switch respond to their React handlers**, not to synthetic clicks.
- **The prompt editor is Tiptap.** Set content through `element.editor.commands.setContent` with
  exactly one `promptRegion` and one `lyricsRegion`; typing would go to whichever region has focus.
- **After a reload the history shows skeleton rows for several seconds** while its API answers — wait
  for the specific card (and for the list count to recover), never a fixed sleep.
- **An empty list is not proof of "nothing left"** until the empty-state text is shown.
- **Downloads:** set the download folder over CDP before clicking; Chrome may still save to
  `~/Downloads` and add ` (1)` to repeated names — match by title prefix and normalise the name.
- **Verify duration with ffprobe** before deleting: a truncated MP3 can still have a valid header.
- **Generate spends credits:** never retry it automatically; stop and report instead.
