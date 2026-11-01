# Visual experiment — does the visual system help or hurt the listening?

## What exists (MEASURED, Seamless-app render reports and frames)

**Picture.** A masked samurai sits in meditation on a seamless beige backdrop, eyes closed, beside a wooden speaker, with scattered
petals and a stone.

**The loop.** It is one 6-second Kling clip of breathing, repeated ~379 times, with 26 slow-motion pauses that break the rhythm. Seam
checks passed: SSIM ≥ 0.998 at the seam, flow continuity within the clip's own range, and a 3-copy repeat validated frame by frame.
About half of the source clip's frames are duplicates (`duplicate_fraction` 0.51), so motion runs at roughly 12 distinct frames per
second. That is natural for a breathing loop. Pixel and SSIM scores can't prove the motion feels smooth; watching `loop_preview.mp4`
remains the check.

**The full system** (`final_video_light_scroll.mp4`) adds:
- **Speaker light:** a music-driven light, 2,061 kicks and 4,780 sparks in 43:39.
- **Quote scroll:** a parchment that shows a new bilingual quote every 11.15 s (234 showings of 70 quotes), with letters arriving
  scrambled before settling.
- **Fire glow:** under the ink.

**Safety:** no frame-to-frame luminance change of 10% or more in a 2-minute sample, so there is no flashing risk. The speaker region is
about 9% of the frame.

**Readability:** at phone size the English quote is small and the Japanese line is tiny. Mid-transition frames show scrambled letters.

**The quotes are currently BLOCKED** (see `rights_matrix.csv`): 9 modern authors and 3 well-known misattributions are presented as
samurai wisdom. Any variant with quotes must use the corrected set.

## What the genre does (MEASURED, n=7 winning openings reviewed)

- All 7 are one scene with slow ambient motion (water, petals, dust, mist) and no cuts.
- 3 of 7 open with a short poem or quote card, then a clean screen.
- None uses an audio visualizer or a breathing guide.

**The masked figure on a flat colour background already exists in samurai lo-fi** (MOONLIGHT SAMURAI). A recurring character is a
brand hypothesis, not verified differentiation.

## The three variants (rendered from the same 40-s excerpt, same audio)

| Variant | Contents | File |
|---|---|---|
| V1 Minimal Zen | samurai, backdrop, breathing loop. No speaker (it belongs to the overlay image), no light, no quotes | `visual/excerpt.mp4` |
| V2 Music Reactive | V1 + the wooden speaker with its music-reactive light | `visual/excerpt_light.mp4` |
| V3 Full Existing System | V2 + parchment quote scroll and fire glow | `visual/excerpt_light_scroll.mp4` |

The tool's own checks passed for V3: max flashes per second 0.0, fire glow correlated with the music at 0.92 (MEASURED,
`visual/excerpt_light_scroll.json`). The brief's "restrained illumination" for V1 isn't available without the speaker image. V1
is therefore the true no-effects baseline.

- **Blind copies:** `visual/clip_A.mp4`, `clip_B.mp4` and `clip_C.mp4`.
- **The key:** `visual/blind_key.json`. Keep it from the viewers.
- **V3 quotes:** V3 uses the current, uncorrected quote set. For a public test, re-render it with the corrected set. For this offline
  preference test, the quotes' *presence* is what is being judged.
- **No separate equalizer:** the speaker light *is* the audio-reactive equalizer. Its strip shows bass at the bottom and treble at
  the top of the arc. The code has no separate bar visualizer, so V2 is "speaker light only".

## Offline viewer test (before anything is published)

**Who.** 8–15 people who use background music for study, work or relaxation. If possible, use the same people as the music test, on
a different day.

**How:**
1. Each viewer watches A, B and C in a randomised order. They play each clip once with sound, on the device they normally use for
   background music; at least half should use a phone.
2. Then they leave all three running in a tab for 2 minutes while reading something. This mimics real use, where the screen is
   mostly ignored.

**Questions (1–7, per clip):**
- How relaxing was it?
- How distracting was it?
- Would you leave it running for an hour?
- How much did you like the look?

Then three closing questions:
- Which would you choose for an hour?
- Did you read any quote? Did reading pull you away from your task?
- Did you notice the light?

**Read the result like this:**
- If V1 or V2 is preferred or tied, and V3 scores as more distracting by about 1 point or more, drop the scroll for the calm promise.
  Keep one opening quote card at most.
- If V3 wins on liking without costing on distraction, keep it for the focus/lo-fi set only.
- Record n, how viewers were recruited, and the device mix. A convenience sample decides between *your* variants; it says nothing
  about the market.

## After publication (the pilot)
- Use YouTube's **Test & Compare** for thumbnails (character close-up vs scene vs no character) when the channel is eligible.
- Don't publish near-identical videos to test effects. Each pilot video differs in music; the visual system stays fixed within an arm.
- Compare retention curves only between videos with the same visual system.

## Pipeline notes for later (no changes made)
- Rendering the light and scroll pass costs ~2 minutes per minute of video; the base render is ~10× faster.
- The base render used no crossfade (`crossfade_seconds` 0.0). Turning on a crossfade fixes the abrupt restarts flagged in the music
  report.
