# Four directions compared

## Data
- **Channel upload lists:** 2026-10-27, every listed upload (newest 60) of each channel, winners and failures alike. Channels come from two sources:
  - the samurai-zen channel refresh (132 channels);
  - 29 channels behind this month's on-niche uploads, read for this research.
- **Stratified sample:** up to 15 channels per direction, picked round-robin across channel-age × size strata, so new and small channels are not crowded out by large ones.
- **Video data:** `competitor_dataset.csv`, with all held videos in `normalized/all_videos.csv`. Channel labels and settings are in `normalized/channels.csv` and `research_manifest.json`.
- **Discovery is search-based.** A channel is held only if YouTube search showed one of its videos, so channels that never surface are missing. These numbers are therefore an optimistic view of a beginner's outcome.

## What the old research got wrong (reproduced from its raw files)

{{old_check_table}}

**The 3× "outlier" rule mostly measured age and search survivorship.** Search returns a channel's best old videos, and the rule
compares their lifetime views with the median of the channel's *recent* uploads. "Few discovered videos" in a concept also meant few
*visible* ones, not low competition. Medians below 100 views make ratios meaningless; this build refuses a channel-relative score when
a baseline has fewer than 5 uploads or a median under 100.

## The comparison (MEASURED; 90% bootstrap intervals in brackets)

{{comparison_table}}

**Directions as defined here:**

| Direction | What it covers |
|---|---|
| A | Lo-fi / hip-hop / beats |
| B | Calm Japanese ambient, including sleep |
| C | Meditation-, mindfulness- or breathing-titled Japanese ambient. Genuine **guided or breathing sessions are essentially absent** from the supply (3 regex hits, all false positives), so C measures "meditation music", not a breathing product. |
| D | Taiko, epic, trap or phonk, motivation |

**New channels (whole history visible, first upload ≤ 335 days ago):**

{{new_channels_table}}

## How to read it
- **Inside a channel, the label does not matter.** A channel's videos labelled sleep, meditation, lo-fi or epic perform alike: a median of 1.00–1.03× its own normal, with intervals within ±0.1 (MEASURED, 19–52 channels per label). The direction is a positioning decision for the whole channel; no label buys views by itself.
- **Between directions, the intervals overlap.** Channel-to-channel differences are larger than direction-to-direction ones. Excluding each direction's top channel barely moves the medians.
- **For beginners specifically:**
  - Small channels' uploads in epic (D) and meditation-titled ambient (C) sit around 1,200–1,900 views at 30–365 days.
  - Zen/sleep (B) sits around 800.
  - Small lo-fi channels (A) are too few to say (n=10 uploads).

  Of the new channels, only calm ambient and meditation ones reached thousands of subscribers (Seiiki, Zen Drift, Ronin Zen Ambience, Ronin Soul). Failures with 8–50 subscribers sit in the same lanes. Every new lo-fi and epic channel in the sample stayed under ~2,000 typical views.
- **Search competition differs sharply.** Lo-fi's top results are huge (median 718K views, 44% over 1M), so a beginner's lo-fi video depends on recommendations rather than search. Epic's incumbents are the weakest, but its searches are partly a rhythm game (*Taiko no Tatsujin*) and creators wanting "no copyright" music, which you cannot supply under ElevenLabs' music-library ban.
- **Momentum fades.** Lifetime views/day overstate current velocity (see the table below), so benchmarks in the pilot use views at matched age.

{{velocity_table}}

## Assessment per direction

| | A. Lo-fi focus | B. Zen / sleep ambient | C. Meditation (music; breathing untested) | D. Epic samurai |
|---|---|---|---|---|
| Demand evidence | Strong search and recommendation demand; dominated by giants | Strong ("japanese zen music for sleep", "no ads") | Strong for meditation *music*; guided-breathing demand untested | Real but split with games and "no copyright" seekers |
| Beginner accessibility | Weak (search incumbents; new channels in sample all tiny) | Medium | Medium (two new-channel breakouts; many failures) | Medium for views, low ceiling |
| Fit with your music | **6 of 12 tracks** (beat-clear flute lo-fi) | Calm tracks fit; sleep wants 3–10 h | **Calm tracks fit; your character is meditating** | No fit (no taiko or trap in the set) |
| Fit with your visuals | Good (masked character on flat colour is a lo-fi convention) | Good | **Best** (seated, eyes closed, breathing loop) | Poor (calm pose) |
| Production cost | ~1 h per video | 3–10 h for sleep (beyond the generation quota) | ~45–60 min per video | Short tracks; many needed |
| Monetization notes | Ads tolerated | Sleep listeners search "no ads" | YouTube: meditation may not suit mid-rolls | Low watch time per view toward 8,000 h |
| Confidence | LOW | MEDIUM | MEDIUM-LOW | MEDIUM |

## Recommendation
- **Primary:** C as calm meditation and focus *music*, not a breathing product. One promise: "calm Japanese flute music to quiet the mind — for meditation, reading and deep work".
- **Alternative to test:** A as soft flute lo-fi for focus, using the six beat-clear tracks.
- **Postpone:** guided breathing (C as a session product) and long sleep sessions (B-sleep).
- **Keep out of this channel:** epic (D). It contradicts the calm promise and doesn't match your music.

The choice rests on fit, cost and the observed beginner breakouts. No direction showed a decisive audience advantage (INFERRED).
