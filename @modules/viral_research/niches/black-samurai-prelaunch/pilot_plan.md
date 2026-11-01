# Pilot plan (preregistered before anything is published)

Nothing is published automatically. The owner publishes by hand after the gates below pass.

## The choices under test
- **Primary audience:** adults who put on calm music to quiet the mind — to meditate, read, or do deep work.
- **Promise:** *calm Japanese flute music to quiet the mind.* No lyrics, no drums that startle, no sudden starts.
- **Brand identity:** one recurring fictional masked samurai in meditation, always the same character, backdrop and palette. There are no historical claims, and he is not presented as Yasuke.
- **Production style:** the visual variant that wins the offline viewer test. The prior is V1 or V2: one quote card at the opening at most, and no rolling quotes.
- **Alternative hypothesis:** *soft flute lo-fi for focus* (the beat-clear set) earns more distribution for a new channel than the calm meditation set.

## Gates before the first upload (all must pass)
1. **Rights.** Nothing used is BLOCKED or NEEDS EVIDENCE in `rights_matrix.csv`.
   - Save the ElevenLabs generation history for every track (date, plan, ID, prompt) and the plan invoices.
   - Get Kling's written answer on rights after cancellation, or keep the membership while the loop is in use.
   - Record the image provenance (prompts and dates, or a written note of what is known).
   - Replace the quote set with the corrected one: authentic lines credited, the 12 blocked lines removed or rewritten, the rest presented as original reflections, and the Japanese checked by a native speaker.
2. **Music.** Complete the corrections in `music_quality_report.md`:
   - split by promise;
   - smooth the joins;
   - separate the similar pairs;
   - finish the listening check.
3. **Offline tests.** Run the blind music test and the V1/V2/V3 viewer test, and record n and the selection method.
4. **Channel setup.**
   - Turn on the altered/synthetic content disclosure for every video.
   - Add a description line saying the music is AI-assisted and the art is AI-generated.
   - Comments on; moderation filters on.
   - Same upload hour each time.

## Videos (5, all genuinely distinct)

| # | Arm | Music | Length |
|---|---|---|---|
| 1 | Primary: calm meditation | the 6 calm tracks (~22 min; *Morning Drift* and *Stillness* kept apart) + ~23 min new calm music | ~45 min |
| 2 | Alternative: flute lo-fi | the 6 beat-clear tracks (~21.5 min) + ~23 min new | ~45 min |
| 3 | Primary | all new calm music, different key/instrument emphasis (e.g. koto + flute) | ~45 min |
| 4 | Alternative | all new flute lo-fi | ~45 min |
| 5 | Primary | all new calm music (e.g. harp + shakuhachi, rain bed optional) | ~45 min |

- **New music needed:** about 180 minutes.
- **Production time:**
  - ElevenLabs Pro: about one month, at a 60% kept share.
  - Creator: 3–4 months (see `economics.md`).
- **Upload cadence:** one video a week, alternating arms. If music is the bottleneck, use 10 days.
- **What stays fixed:** visuals, title template, description template, thumbnail template, upload hour, and no paid promotion. Every video's music is different. Uniqueness is never manufactured with titles, thumbnails or EQ.
- **Thumbnails and titles:** use **Test & Compare** when the channel is eligible.
  - Thumbnails: character close-up vs scene without character.
  - Titles: use first ("Calm Japanese Flute Music for Meditation & Deep Focus — 45 min") vs an evocative Japanese word first.

## Expected ranges (real comparison channels, not targets)
Views of long on-niche uploads from channels under 10K subscribers, at the same age (MEASURED, 2026-10-27; `competitor_dataset.csv`):

{{benchmark_table}}

**How to use these ranges:**
- Compare each pilot video with its direction's row at the same age. "Below p25", "p25–p75" and "above p75" are the readings.
- No universal view target is used.
- Re-measure the ranges at day 28 with `viral_research.py prelaunch` (a new run). The market moves, and the ranges age with it.

## Measurement (YouTube Studio)

| Checkpoint | Where | What |
|---|---|---|
| Day 7 per video | Analytics → Content → the video → Reach | impressions, impressions CTR, traffic sources (Browse, Suggested, Search, External), search terms |
| Day 7 | Analytics → Engagement | average view duration, audience retention curve (key moments) |
| Day 28 per video | same + Analytics → Audience | returning vs new viewers, unique viewers, subscribers gained per video, geography |
| Day 28 | Advanced mode → Compare to → Groups | group "Calm" vs group "Lo-fi": watch time, AVD, views at matched age |
| Day 56 (only if day 28 is inconclusive) | same | same |
| Any time | Content → Restrictions / Copyright | claims, monetization limits |

**Notes:**
- With very small audiences Studio shows "not enough data" for retention, CTR or demographics. Record that rather than guessing.
- Log for every video: hours spent, generation minutes used and kept, render time. These decide sustainability.

## Decision rules
- **CONTINUE** (scale the primary arm to 1–2 videos a week) if all of these hold:
  - at least 2 independent primary-arm uploads are at or above the median of their benchmark row at day 28;
  - Browse + Suggested supply a meaningful part of their views, so YouTube, not only you, is finding listeners;
  - the retention curve shows listeners staying past the opening minutes, measured against your own other videos and Studio's typical-retention comparison where shown;
  - no rights blocker is open;
  - the logged hours and tool cost per video fit your limit. Write that limit down before the pilot starts.
- **PIVOT** if there is genuine interest but one variable limits it:
  - the alternative arm beats the primary on views at matched age and watch time in 2 of 2 or 2 of 3 comparisons → switch the promise to flute lo-fi focus;
  - views are at or above benchmark but retention collapses early → fix the music (dynamics, joins, variety) or the visuals (V1 vs V2);
  - the character thumbnail loses Test & Compare twice → lead with the scene and keep the character small.
- **STOP OR PAUSE** if any of these holds:
  - a rights blocker can't be resolved;
  - production cost or time exceeds the limit;
  - at day 56 every pilot video sits below p25 of its benchmark despite real impressions — YouTube showed them and people didn't choose or stay.
- **INCONCLUSIVE** if Studio reports too little data (impressions too few for CTR or retention) on most videos at day 28. Wait to day 56. If it is still too little, change the packaging (title/thumbnail) for two more uploads before judging the concept. *Insufficient distribution is not the same as rejection.*

**What would change the recommendation:**
- the lo-fi arm clearly winning;
- the blind music test preferring the beat tracks;
- the viewer test showing the character or the scroll distracts;
- Kling or ElevenLabs answers that narrow your rights;
- a policy action (reused or inauthentic content) on any upload.
