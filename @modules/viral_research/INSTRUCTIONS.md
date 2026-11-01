# Viral research — how to trigger and run it

**Trigger** (English or Spanish), for example:
- "find the viral / top videos in the *X* niche", "what makes *X* videos go viral", "research *X* on YouTube"
- "run viral research on *X*", "analyse the outliers in *X*", "give me a report on how to go viral with *X*"
- "investiga el nicho *X*", "videos virales de *X*", "qué hace viral a los videos de *X*"

Platforms: **YouTube** is built. TikTok, Instagram and Facebook are planned (`platforms/<name>.py`); if asked for
one of them, say it isn't built yet and offer YouTube or building that submodule.

## 1. Set up the niche

1. Agree the niche, languages (default `en,es`) and formats (default: Shorts and long-form, ranked separately).
2. Slug: lowercase with dashes (`ai-music`, `dog-training`). If `niches/<slug>.json` doesn't exist, create it
   like `niches/ai-music.json`: per language, **8 query variants** written the way viewers search (generic
   term, tool/brand names, "made with …", "how to …", results/reactions, the format: "video", "song", "cover")
   and **5 hashtags** without `#`. Show the user the list in one short message, then start.

## 2. Run (long: 30-90 minutes; resumable)

```
@modules\viral_research\run.bat <slug> [langs]
```

or `venv\Scripts\python -u @modules\viral_research\viral_research.py run --niche <slug> --langs en,es`.

- Run it **in the background**; progress is written to `@downloads/<slug>/runs/<slug>-<date>/log.txt`.
- **Live status window — always on.** Every `run` opens the dashboard (like Seamless-app's) in a Chrome/Edge
  app window; never pass `--no-dashboard` unless the user asks. It runs as its own process, one per run folder
  (`dashboard.json`), so it still shows how a run ended; a rerun reuses it and reopens the window if it was
  closed; it exits 15 minutes after the run ends and its window is closed. It explains the whole process: what
  the run is about (niche, searches, outlier and control definitions, limits, tools), each of the 9 stages with
  what it does and live numbers, what is happening now, YouTube access (pause, last try), the processes working
  on this computer (yt-dlp, ffmpeg, Whisper, the local vision model), the four rankings, the outlier gallery with
  vision labels, a filterable log, links to `pack.md` / the report, and a Stop button. For a run already going:
  `venv\Scripts\python @modules\viral_research\viral_research.py dashboard --niche <slug>`.
- YouTube answered 429 / "confirm you're not a bot": the run pauses by itself (5/10/20/30 min) and retries;
  after 90 min it stops as `blocked`. Run the same command again later — every stage is cached, and the saved
  pause (`gate.json`) is respected. A run with under half its videos measured stops as `incomplete`: don't
  write a report from it.
- `--stage <name>` stops after a stage (discover, baseline, score, shortlist, evidence, vision, analyze, check, pack).
- The vision stage runs the user's local vision model (on-device's llama-server + MiniCPM-V, ~30 s per image on
  CPU). Its labels are a first pass: spot-check about 10 against the images before relying on them.
- Don't run other YouTube-heavy work at the same time (rate limits).

## 3. Write the report (Claude, in the session)

Inputs, all in the run folder: `pack.md` (read it fully), and for the top outliers of every bucket **look at**
the thumbnail, the contact sheet and the opening frames (paths are in `pack.md`), and read their comments.

Write `report.md` in the run folder, then the HTML version with
`venv\Scripts\python @modules\viral_research\analysis\report_html.py <run folder>` (the dashboard links both); offer
to publish it as a page. Structure:

1. **The answer in five lines** — what wins in this niche, per format and language.
2. **Top outliers** per bucket (`en-shorts`, `en-long`, `es-shorts`, `es-long`): multiplier, views, channel
   median, views/day, age, length, link.
3. **Recipes** — only patterns flagged in `analysis.json` (≥ 20 points more common than in the same channels'
   ordinary videos, ≥ 3 outliers, ≥ 2 channels), each with its numbers ("7 of 12 outliers vs 20% of controls,
   5 channels") and 2-3 example links. Visual observations (thumbnails, frames) are labelled as observations
   with how many outliers showed them.
4. **The brief, point by point** — exactly what to do:
   packaging (title formulas as templates, thumbnail rules), the first 3 seconds (what is seen, heard, said),
   structure (beats with timings), editing pace (cuts/min target from measurements), audio (start loud? level),
   length, posting time, description / tags.
5. **What not to copy** — wording, footage, audio, faces; analysis only, no re-uploads. Name the risks plainly:
   AI covers of copyrighted songs get Content ID claims (the money goes to the rights holder); imitating a real
   singer's voice or likeness breaks platform rules and can break the law.
6. **How sure we are** — sample sizes, the back-test result (say plainly if it doesn't hold), the spot-check,
   limitations (Shorts baselines have no dates; hashtag noise; captions missing → Whisper on the opening only;
   vision labels are a first pass, spot-checked).
7. **Test plan** — publish N videos following the brief and compare with the user's own median after 7 days.
8. **The viral formula and 30 ideas** (always the last part). Ideas are derived, not brainstormed: each one
   sits where a **proven concept**, **viewer demand** and an **open gap** meet.

   First, the concepts. After reading `pack.md`, write the codebook `niches/<niche>.concepts.json`:
   8-15 concepts, each `{"name", "match": regex on title + description, "seeds": [2-3 autocomplete seeds],
   "buckets": [optional]}`. A concept is the topic shape outliers share, not their words (e.g. "a famous song
   in an unexpected genre"). Then run
   `venv\Scripts\python @modules\viral_research\viral_research.py concepts --niche <slug> --autocomplete`.
   It writes `concepts.json`: hits, outliers, outlier rate, lift over the ranking's overall rate, channels,
   median multiplier, recent supply (last 90 days), a holdout (rate on videos older than 30 days vs the
   newest 30 days), and what people type into YouTube search. The codebook stays with the niche, so a monthly
   refresh counts the same way.

   1. **The formula** — one fill-in formula per format (and per language where they differ):
      `concept × title template × thumbnail rule × first 3 s × structure and pace × length × audio × posting`.
      Every slot names its evidence: a recipe with its numbers, a measured target, the most-replayed moment,
      or "observation in N of M outliers".
   2. **Concepts table** from `concepts.json`: hits, outliers, rate, lift, channels, recent supply, holdout,
      demand (autocomplete suggestions and the viewer requests in `pack.md`), and **proven** (3+ outliers on
      2+ channels) or **bet**, plus a rights flag. Say plainly when a holdout does not hold.
   3. **30 ideas, scored** — a table: #, concept (proven or bet), ranking (format + language), working title
      (new wording), thumbnail (what is in it, its text, its style), hook (the first 3 s: seen, heard, said),
      beats with timings, spice lever, and the score with its parts: evidence (proven, lift, holdout) × demand
      (viewer requests, autocomplete) × gap (outliers against recent supply), minus risk (copyright, likeness)
      and cost. Ranked by score; spread over the four rankings in proportion to their evidence; at most 5 bets,
      labelled. Prefer original songs or public-domain melodies; flag every idea with a rights risk.
   4. **Predictions, written down first** — save `predictions.csv` in the run folder: idea, ranking, title,
      concept, predicted multiplier band against the user's own channel median (e.g. 1-2x, 2-5x, 5x+), and
      empty columns for the published URL and the views at 7 and 28 days. This is how the formula gets judged
      by results, not by us.
   5. **Test the packaging before the song** — for the top 5 ideas: two titles and two thumbnail concepts each,
      to run in YouTube Studio's free Test & Compare.
   6. **How to spice them** — the levers that turn a proven concept into a new video (genre swap, era swap,
      language or culture swap, a story or character, a series and part 2, a seasonal or trend tie-in, a
      contrast or surprise in the first 3 s), each with the outliers and viewer requests that show it works.
   7. **How to replicate** — a production checklist per concept, with free tools: the song (a style-prompt
      template built from the creator-declared styles in `pack.md`: genre, era, instruments, vocal type, mood,
      tempo; their structure, never their text), the visuals (a thumbnail and visual-prompt template from the
      vision labels and the thumbnails themselves), the edit (cuts per minute, first cut, loud start, where the
      most-replayed moment falls), and the upload (title template, description lines, tags, posting time).

Every number must come from a file in the run folder. Never invent views or patterns. Ideas and templates are
new work built from the evidence; they quote no title, lyric or description.

## 4. Build the niche playbook (the page the user works from)

One folder per niche: `@downloads/<niche>/runs/<niche>-<date>/` (raw data, one per run) and
`@downloads/<niche>/playbook/` (what to do). The playbook is the copy-ready recipe: channel name, handle and
About, title, thumbnail, content structure (an arrangement lane), sound and picture, description, tags, upload
settings, rhythm, series, pinned comment, Shorts, the 30 ideas, the evidence gallery, and the scorecard.

1. Write `niches/<niche>.playbook.json` (see `niches/ai-music.playbook.json`): `tracks` (one per format, each
   with an `arrangement` and formula `cards`), `channel` cards, `ideas` (with `e`, `d`, `g`, `cost` from the
   concept table), `risks`, `confidence`. Every card's evidence **points at a stat instead of typing a number**:
   `{"slot": "es-long/tags/count"}` (title / description / tags / settings slots mined from the raw metadata),
   `{"measure": "en-long/lufs"}` (analysis.json), `{"concept": "<name>"}`, `{"channels": "one_sound" | "sound_in_name"
   | "rhythm" | "small"}`; only practice, policy and observations are text (`{"kind": "policy", "text": ...}`).
   The mined slots are in `playbook/playbook_stats.json` after a first build.
2. Check proposed channel handles are free (`https://www.youtube.com/@Handle` answers 404) and say the date.
3. Build: `venv\Scripts\python @modules\viral_research\viral_research.py playbook --niche <slug>`. It reuses the
   newest run, copies the top outliers' thumbnails, and rewrites `index.html`; `scorecard.csv` keeps whatever
   the user already filled in.

## Rules

- Free, local tools only (yt-dlp + Node, ffmpeg in `Seamless-app\bin`, faster-whisper in on-device's whisper
  environment, on-device's llama-server for vision). No paid APIs.
- Media and captions stay in the run folder (gitignored); never re-upload or reuse wording, footage or audio.
- Design and thresholds: [PLAN.md](PLAN.md); rules: [knowledge/rules.md](knowledge/rules.md); what was learned:
  [knowledge/lessons.md](knowledge/lessons.md).
