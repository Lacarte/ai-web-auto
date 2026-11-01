# viral_research — lessons

- **yt-dlp needs a JavaScript runtime for YouTube** (2026): without one it warns and some formats go missing.
  Node works with `--js-runtimes node`; `pip install -U "yt-dlp[default]"` brings the `yt-dlp-ejs` solver.
  yt-dlp also warns that Python 3.10 is deprecated (still works).
- **Flat search has no dates**; it has views, duration, channel ID and a description snippet (~5 s / 25).
- **A channel's `/videos` tab gives a full baseline in one request** with `--extractor-args
  youtubetab:approximate_date`: last 30-40 uploads with views (rounded like "41K") and day-accurate dates.
- **The `/shorts` tab has views but no dates**, with or without approximate_date.
- **There is no working Shorts search filter** (`sp=EgIQCQ==` returned nothing); `EgIYAQ==` (under 4 min)
  mixes Shorts and short videos. A Short is <= 180 s and vertical in full metadata.
- **Hashtag Shorts pages work** (`/hashtag/aimusic/shorts`): IDs and views, no channel or title.
- **The "most replayed" heatmap only exists on videos with enough views**; it was empty on a 40K-view video.
- **Tiny channels produce the strongest signals**: a Short with 35K views on a 393-subscriber channel whose
  Shorts usually get ~1,300 is a 27x outlier.
- **The "xx-orig" caption tracks are not the original language:** a video with auto-dubbed audio lists ~20
  of them (ar-orig, bn-orig, en-orig…). The declared audio language is metadata `language` (e.g. `en-US`).
- **Hashtag pages are noisy:** `#aimusic` returned a Bengali comedy Short; filter by declared audio language,
  then by script / common words.
- **Every yt-dlp call pays YouTube's JS challenge (~20-40 s with Node):** fetch media and captions in one call.
- **YouTube rate-limits caption downloads (HTTP 429)** under parallel load, and a caption error aborts the
  whole call unless `--ignore-errors`; keep captions optional and fall back to Whisper.
- **`--download-sections` failed with ffmpeg error -138 (timeout)** with the 2023 ffmpeg build: download a
  small file with yt-dlp's own downloader and cut locally instead.
- **Liveness from log freshness lies:** stages that fetch hundreds of videos log nothing for minutes, so the
  first dashboard showed "Stopped" while the pipeline was working. Runs now emit `events.jsonl` (progress
  counters, heartbeat) and `run.pid`; older runs are checked against the process table.
- **yt-dlp prints the Python 3.10 deprecation notice first on stderr**, so a short error snippet showed only
  that notice; strip it before classifying the error.
- **Channel names are a weak niche signal:** "IA para crear música" channels also post football. A video is
  on-niche only with the AI signal **and** the topic (YouTube category "Music" or music words in its own
  title, description or tags).
- **faster-whisper lives in on-device's own environment** (`C:\Users\Admin\.venvs\whisper`), called as a child
  process like on-device does.
- **YouTube bot-checks an anonymous IP after ~1,700 requests in a day** ("Sign in to confirm you're not a
  bot", HTTP 429). Every call goes through one gate: 1.5 s pacing, a shared pause with growing backoff
  (5/10/20/30 min), the same request retried, give up after 90 min. The pause is saved in `gate.json`, so a
  restarted run does not ask again before it ends. A run with under half its videos measured stops as
  `incomplete` instead of computing patterns on nothing.
- **Spend requests where they decide something** (video-agent-skills does ~30 per run): channel tabs and
  search are one request each; full metadata only for the shortlist; media only for the top 8 outliers and
  8 controls per ranking. The dashboard shows the request count per stage.
- **The local vision model (MiniCPM-V 4.7 1B via on-device's llama-server) needs short, single questions.**
  One long form made it drop the text line and miss faces. Four questions at 448 px (text / face yes-no /
  style in one word / subject in 8 words) labelled 3 test thumbnails correctly, 12 of 12 labels. It answers
  "painted" instead of "yes" for painted faces, so the parser accepts the kind of face as yes. About 25-30 s
  per image on CPU: `cache_prompt` did not reuse the image between questions.
- **A shared CPU makes the local vision model 4x slower** (about 2 min per image while another llama-server,
  the user's WeVisOne-VL-4B, was busy). The vision stage is time-boxed (`vision.max_minutes`, 20), labels every
  thumbnail before any opening frame, stops when its server dies, and leaves the rest for the next run. Never
  stop the user's other model servers: only the one the pipeline started (check its parent process).
- **Controls must pass the same niche check as outliers.** In the 2026-10-25 run only outliers were checked, so
  some controls were the channels' off-topic videos (a skateboard clip, an Amazon Prime how-to), and "the title
  names the AI" showed a fake 75% vs 17% gap. Since then off-niche controls are excluded too; any feature tied
  to the niche filter itself is not a finding.
- **Read only the channels that matter** (samurai-zen, 2026-10-25): searches touched 461 channels; reading the
  upload lists of only those with an on-niche video or a seed, capped at `max_channel_tabs` (200), and the Shorts
  lists of the 80 busiest niche channels kept the run far below the ~1,700 requests that triggered a bot check.
  The user's own examples go in as `seed_channels` / `seed_videos` in the niche file.
- **Evergreen niches fill the top of the list with old videos.** Search results carry no dates, so 19 of the 24
  samurai-zen candidates were over a year old (old videos had years to collect views, so they are excluded as not
  comparable). The shortlist now confirms candidates in rounds until each ranking has 12 that pass every check
  (`max_shortlist_candidates`, 60).
- **Hours-long videos: fetch only the opening.** Ambient niches post 1-10 hour videos; downloading the whole
  360p file to measure 90 s pulled 146 MB for one video in minutes (~10 GB for a run). Now one yt-dlp call
  resolves the stream URLs, range requests fetch the first few MB of video and audio, ffmpeg cuts the opening:
  3.6 MB in 22 s for a 2-hour video. The full-file fallback is allowed only up to 20 minutes. The 2023 ffmpeg
  decodes the AV1 streams YouTube serves (dav1d).
- **A detached process is still its parent's child for taskkill /T.** The dashboard started from the run died
  when the run was stopped (and the dashboard's own Stop button would have killed it). It is now started through
  `cmd /c start`, so its parent is a process that has already exited.
- **Ideas come from demand, not brainstorming.** Raw demand is free: viewers' requests in the outliers'
  comments ("haz una versión salsa", "part 2"), the "most replayed" heatmap (in about 1 in 5 videos' metadata),
  and YouTube's search autocomplete (`suggestqueries-clients6.youtube.com`, a light JSONP endpoint, not a page:
  "canción con ia de bad bunny", "ai song cover michael jackson"). Concepts are counted from a codebook
  (`niches/<niche>.concepts.json`) so the numbers can be reproduced, with a holdout on the newest 30 days.
- **The dashboard is its own detached process** (one per run folder, `dashboard.json`), so it outlives the run
  and shows how it ended. A detached process that starts a console program (PowerShell for the process table,
  taskkill) pops up a console window unless it passes `CREATE_NO_WINDOW`. Another app (Omnitase) serves the
  same MiniCPM model with its own llama.cpp: match our vision server by its executable path and the model.
- **Spot-check of 9 more thumbnails (2026-10-25):** text present 9/9, face 9/9, subject 9/9, style 6/9. All
  three style misses were "text" for a photo or painting with a big title, so "text" is no longer a style
  choice. Transcripts are approximate (3 of 9 had garbled words: "AI MUSIC" read as "AT MUSIC"): count words,
  don't quote them. Each label file stores its questions, so rewording one re-asks only that question.
