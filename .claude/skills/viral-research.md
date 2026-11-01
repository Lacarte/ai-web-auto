---
name: viral-research
description: Research a niche on YouTube (TikTok, Instagram, Facebook planned) - find the videos that beat their own channel's normal, measure what they do differently, and write a point-by-point report on how to go viral. Use when the user asks for viral / top / outlier videos in a niche or how to go viral with a topic.
---

# Viral research — run the module and write the report

**TRIGGER:** "find the viral / top videos in [niche]", "what makes [niche] videos go viral", "research
[niche] on YouTube", "run viral research on [niche]", "report on how to go viral with [topic]",
"investiga el nicho [X]", "videos virales de [X]".

**Do:** follow [@modules/viral_research/INSTRUCTIONS.md](../../@modules/viral_research/INSTRUCTIONS.md) —
set up the niche file, run `@modules\viral_research\run.bat <niche>` in the background (it always opens the live
dashboard for the user; don't pass `--no-dashboard`), then write the report from the run folder's `pack.md`
(and the thumbnails / frames it lists) in the structure given there. The report always ends with the viral
formula for the niche, its concepts (recipes vs bets), 30 ideas, how to spice them and how to replicate them.
Then build the niche playbook (`viral_research.py playbook --niche <niche>`, INSTRUCTIONS.md part 4): the
copy-ready page the user works from, in `@downloads/<niche>/playbook/`.

**Not this skill:** building a Chrome extension (`web-auto`), or one-off browser automation
(`ai-web-auto-driver`).
