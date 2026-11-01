# Music quality report — the 12 existing tracks

Source: `raw/audio_audit.json` (built by `analysis/audio_audit.py` from `Seamless-app/INPUT/SOUNDS` in playlist order; originals in
`_ORIGINALS`). Labels: MEASURED unless stated. **Automatic measures are engineering signals for a listening check — they can't prove a
track is original or clear it legally.** Manual listening was not possible in this research (UNAVAILABLE); it is step 1 below.

## 1. Per-track measures

{{music_table}}

*Beat clarity:* ~0.1–0.35 = free time, no steady beat; > 0.6 = a clear steady beat. *Starts hard:* the first half-second is within 6 dB
of the track's body level. *EQ:* band energy of the published file minus the original (lows 20–150 Hz / low-mids 150–500 Hz / highs 6–11 kHz).

## 2. What the numbers say

- **Levels are consistent and safe (MEASURED).** Every published track sits at −13.5 to −14.3 LUFS with true peaks of −1.3 to −1.5 dBTP.
  The originals peaked at about 0 dBTP (one at +0.2 dBTP, an inter-sample clipping risk), so the processing fixed a real problem.
  YouTube plays loud uploads quieter; at about −14 LUFS nothing will be turned down much (INFERRED from YouTube's normalisation practice).
- **The "small EQ" was more than EQ (MEASURED).** Loudness range shrank on most tracks, for example 9.5 → 4.5 LU on *Sunrise After the
  Journey* and 6.7 → 5.0 LU on *Samurai Stillness*. The EQ is consistent: a small low lift, −0.5 to −2 dB in the low-mids and **+1 to
  +3.6 dB in the 6–11 kHz highs**. That matches Seamless-app's `audio.master` chain (`seamless/audio.py`, `config.json`):
  - high-pass;
  - low shelf;
  - −2 dB at 280 Hz;
  - +1.5 dB presence at 3.5 kHz;
  - air shelf;
  - harmonic exciter;
  - compressor;
  - limiter at 0.9.

  The processed files also carry the bundled ffmpeg's encoder tag (INFERRED). Brighter highs and an exciter can tire the ear over an
  hour (INFERRED). Include an original-vs-processed pair in the blind test before keeping the chain for the calm set.
- **Two promises in one playlist (MEASURED).** Six tracks have a clear steady beat (clarity ≥ 0.5: *Bamboo Afternoon*, *Bamboo
  Daydream*, *Misty Pass Reverie*, *Samurai Soul Flight*, both *Wisdom* tracks). The others are freer or carry only a soft pulse
  (lowest: *Misty Pass At Dawn* 0.18). A listener who came for "calm, no beat" gets beats, and the reverse.
- **No silences inside tracks; every track fades out (MEASURED).** Most of the next tracks start at full level within half a second.
  The 43:39 video therefore restarts abruptly after near-silence at most joins:

{{joins_table}}

## 3. Repetition and similarity (engineering signals, LOW confidence)

Each track is cut into ~2.3-s blocks of pitch-class and spectral-band features, standardised within the track. The most similar
~14-s passages are found along every time offset. Baseline: unrelated pairs score a median of {{unrelated_median}}, and 95% of them
score at or below {{unrelated_p95}}.

{{pairs_table}}

- **Inside each track, short phrases repeat heavily** (best self-match 0.93–0.995). That is normal for loop-based lo-fi. It does
  mean each track's melodic material is narrow.
- **Same-prompt pairs are mostly distinct** (the two files generated at the same timestamp). The exception is *Samurai Morning Drift* /
  *Samurai Stillness*, whose shared passage stands well above the unrelated baseline.
- A group of four tracks (*Morning Drift*, *Stillness*, *Wisdom In Soft Bamboo*, *Sunrise After the Journey*) share passages above
  the unrelated 95th percentile. They will likely sound alike if played in a row.

**Are the melodies varied enough for a long-form brand? Not yet shown.** Twelve tracks of 3–4 minutes from six prompts, with
heavy within-track looping and a similar-sounding group, support one ~44-minute video. They do not support a catalogue. A brand
needs each video to sound like its own listening session. This judgment is INFERRED and needs the listening check.

## 4. Corrections, in order

1. **Listen** to every track start to end on headphones and on a phone speaker. Note anything harsh, sudden, repetitive or
   artificial-sounding (AI artefacts, warbling flute, smeared reverb tails). Write the notes into the asset register.
2. **Split by promise.** Put the six beat-clear tracks into a *flute lo-fi focus* set and the free/soft-pulse tracks into a *calm
   meditation* set. Don't mix them in one video.
3. **Smooth the joins.** Use 2–3-s fade-ins or 3–5-s crossfades instead of fade-out → full-level restart. Seamless-app's assembly
   already supports a crossfade setting (`crossfade_seconds` is 0.0 in the last render).
4. **Revisit the processing for the calm set.** Keep the −1 dBTP ceiling, but test less limiting (a wider loudness range) and less
   high-shelf boost against the current files in the blind test. Don't adopt a universal LUFS target without that test.
5. **Don't place the similar group back to back.** Keep *Morning Drift* and *Stillness* apart in a running order (at least two
   tracks between them), or replace one.
6. **Keep provenance:** for every future generation save the ElevenLabs history entry (ID, date, plan, prompt, settings), the untouched
   download, and its SHA-256. Do the EQ/limiting on a copy.

## 5. Blind listening test (protocol)

- **Purpose:** compare candidate tracks and processing without revealing genre or production method. This is not a demand study.
- **Material:** 6 excerpts of 60 s, from 5 s into each track, all level-matched to the same LUFS:
  - 2 beat-clear tracks;
  - 2 calm tracks;
  - 1 calm track as the original, plus the same track as processed;
  - 1 decoy from a public-domain or self-made reference, if available.

  Excerpts are named `S1`–`S6`, with the order randomised per listener. Keep the key file away from the listeners.
- **Listeners:** 8–15 people who already use background music to study, work or relax, and who are not the owner's close family.
  Record how each was recruited.
- **Questions (1–7 scale, per excerpt):**
  - How much did you enjoy it?
  - How original does it sound?
  - Would it help you focus?
  - Would it help you relax or meditate?
  - Would you replay it?
  - Was anything distracting? (free text)

  Final question: which one would you keep playing for an hour?
- **Analysis:** report medians and ranges per excerpt and the count of first choices. With n < 15, treat differences under 1 point
  as no difference. **A convenience sample like this says which of your tracks to lead with, not whether the world wants them.**
