# Viral research — Plan

Agreed 2026-10-25. First platform: YouTube. First niche: AI music. Four separate rankings:
English and Spanish × Shorts and long-form. Claude (in the session) writes the qualitative analysis and the
final report from the data this module saves. Free and local tools only.

## The question

In a niche, which videos clearly beat **their own channel's normal**, and what do they repeatedly do that the
**same channels' ordinary videos don't**? Turn the answer into a short, testable production brief.

Raw views are not the signal: big channels get big views on ordinary videos. A video is an **outlier** when
`views ÷ its channel's median views` (same format) is 3× or more; 1.5–3× is borderline.

## What was measured before building (2026-10-25)

| Fact | Consequence |
|---|---|
| yt-dlp full metadata: views, likes, comments, exact upload time, duration, width/height, subscribers, tags, chapters, description, captions in 176 languages, sometimes a "most replayed" heatmap | One request per shortlisted video gives everything except comments and media |
| Flat search (`ytsearchN:`): views, duration, channel ID, title — no date — ~5 s for 25 | Cheap discovery; the channel ID is enough to score |
| Flat channel `/videos` tab with `youtubetab:approximate_date`: last 30 videos with views (rounded like "41K") and a day-accurate date, ~2 s | **One request per channel** gives the long-form baseline (7–180 days, ≥10 videos) |
| Flat channel `/shorts` tab: views, **no dates** | Shorts baseline = 30 most recent after skipping the 3 newest (still growing); stated as a limitation |
| Hashtag Shorts pages (`/hashtag/<tag>/shorts`) return Shorts with views, no channel | Shorts discovery; one metadata request per Short for its channel |
| No working "Shorts" search filter (`sp=EgIQCQ` → 0 results); "under 4 min" mixes Shorts and short videos | A Short = ≤ 180 s **and** vertical (height > width) from full metadata |
| yt-dlp needs a JS runtime for YouTube; Node works (`--js-runtimes node`); yt-dlp updated to 2026.10.21 with `yt-dlp-ejs` | No Deno install |
| faster-whisper 1.2.1 in `C:\Users\Admin\.venvs\whisper` (on-device's setup) | Transcripts when captions are missing, via a worker process |
| ffmpeg / ffprobe in `Seamless-app\bin` | Cuts, frames, loudness |

## Pipeline (cached per run in `@downloads/<niche>/runs/<niche>-<date>/`, resumable)

1. **Discover** — query variants and hashtags per language (`niches/<niche>.json`): flat search, "under 4 min"
   search, hashtag Shorts pages.
2. **Baseline** — per channel: flat `/videos` (long-form) or `/shorts` tab → median views, sample size.
3. **Score** — provisional multiplier from flat views; tier; views/day; language check; sponsored excluded.
4. **Shortlist** — per bucket (language × format): top outliers + an equal number of **controls** (same
   channels, 0.5–1.5×). Full metadata confirms format (vertical ≤ 180 s), exact views, upload date.
5. **Evidence** — captions (json3, word timing) or a local transcript, top comments, thumbnail; media
   (≤ 480p; long-form: first 90 s only) for measurement.
6. **Measure** — cuts per minute, time to first cut, frames at 0/1/2/3 s + contact sheet, loudness, how loud
   the first 3 s are vs the rest, silence share, words in the first 3 / 15 s, words per minute, music-only.
7. **Patterns** — features clearly more common in outliers than controls (≥ 20 points) and present in ≥ 3
   outliers across ≥ 2 channels; packaging (title, thumbnail, length, timing) separate from content (hook,
   pacing, structure).
8. **Back-test** — learn patterns on the older half of the scored videos, check whether newer videos that
   match them out-perform those that don't; report the result either way.
9. **Spot-check** — view counts re-read from the watch page HTML (independent of yt-dlp's parser).
10. **Evidence pack** (`pack.md`) → Claude writes `report.md` / `report.html`: ranked outliers with numbers,
    recipes with evidence counts and links, the point-by-point brief, five video ideas, what not to copy.

## Layout

```
@modules/viral_research/
  module.json, PLAN.md, knowledge/   rules, lessons, the pipeline as actions
  viral_research.py                  CLI: run / collect / measure / analyze / pack / spot-check
  platforms/youtube.py               YouTube submodule (TikTok, Instagram, Facebook later, same interface)
  analysis/metrics.py                baselines, scores, title/transcript features (pure)
  analysis/media.py                  ffmpeg / ffprobe measurements
  analysis/patterns.py               outliers vs controls, back-test (pure)
  analysis/pack.py                   evidence pack for the report
  analysis/whisper_worker.py         faster-whisper child process (word timings)
  niches/ai-music.json               query variants and hashtags per language
  @downloads/<niche>/runs/...        raw data, media, transcripts, report (gitignored)
  @downloads/<niche>/playbook/       the copy-ready playbook page and scorecard (gitignored)
```

## Rules

- Analysis only: reuse structure, pacing and formulas — never wording, footage, or re-uploads. Media stays
  local.
- Polite collection: a few requests at a time, cached, resumable.
- Every number in the report traces back to a saved file.
