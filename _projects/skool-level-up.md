# AI Web-Auto Projects

## Project: Skool Community Level Unlock (ai-video-creators)
**Date:** 2026-04-04
**Status:** Completed
**Goal:** Scan a Skool community page, understand the leveling system, and automate engagement to unlock Level 2.

### What we did
1. **Scanned the community** — extracted `__NEXT_DATA__` and `groupLevels` from the leaderboard page to map the full level system (9 levels, point thresholds, locked courses per level).
2. **Identified your status** — Level 1, 0 points, premium tier, joined 2026-04-03.
3. **Discovered unlock requirements** — Level 2 = 5 points (likes from other members). Unlocks "AI Influencer challenge" and "// Resources vault" courses.
4. **Executed Plan 1** — Watched 60-sec intro video, liked the Welcome post, commented on it.
5. **Executed Plan 2** — Posted an introduction ("New here - excited to learn AI video creation!") in the community.

### Key technical challenges solved

**Problem: CDP clicks don't trigger React event handlers on Skool**
- CDP `Input.dispatchMouseEvent` and JS `.click()` both failed on the "Write something" post editor card.
- Root cause: React event delegation — React listens at the root, not on individual elements.
- Solution: Walk up DOM parents, find `__reactProps$xxx.onClick`, call it directly with a fake event object.

**Problem: React re-render breaks CDP execution context**
- After triggering the React onClick, ALL subsequent `execute_script` / `Runtime.evaluate` calls returned empty strings.
- Root cause: The state change triggers a full component re-render that invalidates the CDP execution context.
- Solution: Wrap everything in a single Promise inside one `Runtime.evaluate` call. Trigger action → poll with setTimeout for new elements → interact with them → resolve. CDP's `awaitPromise:true` keeps the script alive through the re-render.

**Problem: React ignores direct input value setting**
- `input.value = 'text'` doesn't update React's internal state.
- Solution: Use `Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, text)` then dispatch an `input` event with `{bubbles: true}`.
- For ProseMirror/TipTap contentEditable editors: set `innerHTML` and dispatch input event.

**Problem: Screenshots appeared stale/cached**
- `chrome.tabs.captureVisibleTab` consistently showed the old page state even after confirmed navigation.
- Workaround: Relied on script output verification instead of visual screenshots.

### Proven selectors (Skool community pages)
```
__NEXT_DATA__                                    — all page/group/member data
pp.currentGroup.metadata.levels                  — level config (JSON string)
pp.groupLevels                                   — levels with locked courses
pp.self.member.metadata.chatLvlAllowed           — current user level
pp.leaderboardsData / pp.allTime.users           — leaderboard entries
button.styled__LikeButton-sc-ne2b7k-1            — post like button
[contenteditable=true] (comment area)             — comment ProseMirror editor
input[placeholder="Title"]                        — post editor title input
div.tiptap.ProseMirror.skool-editor               — post editor body (contentEditable)
styled__CardWrapper (with __reactProps.onClick)    — "Write something" trigger
```

### Automation pattern (reusable for any Skool community)
```
1. Navigate to community page
2. Extract __NEXT_DATA__ for group levels, member status, leaderboard
3. Check groupLevels for unlock requirements and point thresholds
4. Like posts: find button.styled__LikeButton, CDP click
5. Comment: scroll to [contenteditable], CDP click to focus, CDP type
6. Create post: trigger __reactProps.onClick on CardWrapper via single-Promise pattern,
   fill title with native setter, fill body via innerHTML, find and click submit button
```

### Files modified
- `ai_web_auto_backend/automation_controller.py` — fixed json.loads bug on dict result in discover command
- `.claude/skills/web-auto.md` — added Framework Click Escalation, React Input Filling, Single-Promise Pattern rules
- `CLAUDE.md` — added Framework Click Escalation and React Input Filling to Critical Technical Rules

### 6 Rules discovered (saved to memory + skill)
1. Always test if clicks work first — click a nav link to confirm CDP works before debugging
2. Check what's actually under the cursor — elementFromPoint + devicePixelRatio
3. When framework ignores CDP clicks — use __reactProps / __vue__ / __ng__ internal handlers
4. When action breaks execution context — single-Promise pattern in one Runtime.evaluate call
5. Never set input.value directly on React — use native prototype setter + dispatch input event
6. Follow strict detection order — CDP click → selector click → framework props → Promise pattern
