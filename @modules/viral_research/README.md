# Viral research module

Finds the videos in a niche that clearly beat **their own channel's normal**, measures what they do that the
**same channels' ordinary videos don't**, and produces an evidence pack from which Claude writes a
point-by-point production brief. Free, local tools only: yt-dlp (with Node as its JavaScript runtime),
ffmpeg / ffprobe from `Seamless-app\bin`, faster-whisper from on-device's whisper environment, and
on-device's llama-server with MiniCPM-V 4.7 1B to label thumbnails and opening frames (text, face, style, subject).

Design, measurements and thresholds: [PLAN.md](PLAN.md). Rules: [knowledge/rules.md](knowledge/rules.md).
What was learned about YouTube while building it: [knowledge/lessons.md](knowledge/lessons.md).

## Run

```
venv\Scripts\python @modules\viral_research\viral_research.py run --niche ai-music --langs en,es
```

- `--stage discover|baseline|score|shortlist|evidence|vision|analyze|check|pack` stops after that stage.
- YouTube bot check ("Sign in to confirm you're not a bot"): every request goes through one gate that pauses
  5/10/20/30 min and retries, gives up after 90 min, and saves the pause in `gate.json` so a rerun waits it out.
  Optional `cookies_from_browser` / `cookies_file` in `module.json` (use a secondary Google account).
- Budget: media is downloaded only for the top `measure_per_bucket` (8) outliers and controls per ranking.
- Every run opens a live status window (Chrome/Edge app window; `--no-dashboard` to skip it). It explains what
  the run is about, each stage with what it does and its live numbers, what is happening now, YouTube access
  (bot-check pause), the processes working on this computer, the rankings, the outlier gallery with vision
  labels, a filterable log, and Stop (safe: a rerun continues). It is its own process, one per run folder
  (`dashboard.json`): it outlives the run, a rerun reuses it, and it exits 15 minutes after the run ended and
  its window was closed. Attach to any run with `viral_research.py dashboard [--niche X]`.
- One folder per niche: `@downloads/<niche>/runs/<niche>-<date>/` caches each run (rerunning continues where it
  stopped); `@downloads/<niche>/playbook/index.html` is the page to work from.
- Then ask Claude to write the report from `pack.md` (it reads the numbers, the opening lines, and looks at
  the thumbnails and frames listed there), and the playbook (`viral_research.py playbook --niche <niche>`).

## The playbook (`@downloads/<niche>/playbook/`)

| File | What |
|---|---|
| `index.html` | the recipe by format (arrangement lane + copy-ready formula cards with outliers-vs-controls meters), the channel set-up, the 30 scored ideas, the evidence gallery, risks |
| `scorecard.csv` | the ideas' predicted bands; fill in links and views at 7 / 28 days — kept across rebuilds |
| `playbook_stats.json` | every mined slot (title, description, tags, settings, channels), outliers vs controls |
| `assets/` | the top outliers' thumbnails |

Built from a run plus `niches/<niche>.playbook.json` (formulas and ideas; evidence points at stats, never typed numbers).

## Outputs (run folder)

| File | What |
|---|---|
| `discovery.json` | every video found, with the searches that found it |
| `channels.json` | each channel's baseline (median views, sample size, window) |
| `scored.json` | every scored video: bucket, multiplier, tier, views/day, age |
| `shortlist.json` | per bucket: outliers and controls (the same channels' ordinary videos) |
| `meta/<id>.json` | full metadata |
| `evidence/<id>/` | thumbnail, captions or `transcript.json`, `comments.json`, `media.mp4`, `measures.json`, frames, contact sheet, `vision_thumbnail.json` / `vision_opening.json` (local vision labels) |
| `analysis.json` | outliers vs controls per feature (pattern flag), back-test |
| `spot_check.json` | view counts re-read from the watch page |
| `pack.md` | the evidence the report is written from (with viewer requests, most-replayed moments, creator-declared styles, a wider outlier list) |
| `concepts.json` | `concepts` command: each concept of `niches/<niche>.concepts.json` with proof, lift, crowding, holdout and search autocomplete |
| `report.md`, `report.html`, `predictions.csv` | the report (HTML via `analysis/report_html.py`); the 30 ideas' predicted results, filled in with real views after publishing |
| `log.txt`, `events.jsonl`, `run.pid`, `dashboard.json` | progress log and events (read by the dashboard); the running pipeline's process ID; the dashboard's process and port |
| `gate.json`, `failed.json` | the current bot-check pause; videos YouTube refused (not retried in the same run) |

## Pre-launch mode (`prelaunch.py`)

Should a new channel launch, for whom, with what, and is it cleared to publish? This mode measures every listed upload of a
stratified channel sample, failures included (search alone only surfaces survivors). Each number keeps its age next to it, and a
channel-relative score is refused when the channel's own baseline is too small. It also audits the owner's tracks and classifies
every asset's rights.

```
venv\Scripts\python @modules\viral_research\viral_research.py prelaunch --niche black-samurai-prelaunch            # all but previews
venv\Scripts\python @modules\viral_research\viral_research.py prelaunch --niche black-samurai-prelaunch --stage build
venv\Scripts\python @modules\viral_research\viral_research.py prelaunch --niche black-samurai-prelaunch --stage previews  # renders 3 x 40 s
```

**Stages:**

| Stage | What it does |
|---|---|
| `snapshot` | copies the source runs' raw files |
| `assets` | hashes and probes the owner's files; never changes them |
| `audio` | `analysis/audio_audit.py` |
| `sample` | reads the upload lists of new channels seen in this month's searches |
| `previews` | three visual variants from a copied excerpt |
| `build` | offline and deterministic |

**Files:**
- **Raw evidence:** `raw/`. Written once and never overwritten.
- **Normalized tables:** `normalized/`.
- **Interpretation:** the templates and facts in `niches/<niche>/`. These are `rights.json`, `claims.json`, `economics.json`, `values.json` and one `.md` per report. `build` refuses ledger entries that break the evidence rules and template placeholders without data.

**Outputs:** in `@downloads/<niche>/runs/<timestamp>/`.
- `decision_report.md`
- `evidence_ledger.csv`
- `rights_matrix.csv`
- `competitor_dataset.csv`
- `opportunity_comparison.md`
- `music_quality_report.md`
- `visual_experiment.md`
- `economics.md`
- `pilot_plan.md`
- `research_manifest.json`

## Add a niche

Create `niches/<name>.json` with query variants and hashtags per language (see `niches/ai-music.json`).

## Add a platform

Add `platforms/<platform>.py` with the same functions as `platforms/youtube.py` (search, channel tab,
metadata, captions, comments, thumbnail, download, an independent view check), and the platform's facts to
`knowledge/`. TikTok, Instagram and Facebook are planned; YouTube is the first.

## Tests

`venv\Scripts\python -m unittest tests.test_viral_research` (baselines, scores, features, caption parsing,
patterns, back-test).

`venv\Scripts\python -m unittest tests.test_viral_research_prelaunch` covers the pre-launch mode:
- dedupe;
- missing metadata and ages;
- small baselines;
- deterministic statistics;
- evidence and rights rules;
- failed fetches;
- report regeneration.
