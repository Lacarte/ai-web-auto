"""viral_research: baselines, outlier scores, features, caption parsing, patterns and the back-test."""

import json
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "@modules" / "viral_research"))
from analysis import metrics as M  # noqa: E402
from analysis import patterns as P  # noqa: E402

NOW = 1_800_000_000
DAY = 86400


class TestBaselines(unittest.TestCase):
    def test_long_form_uses_videos_7_to_180_days_old(self):
        entries = [{"view_count": 1000, "timestamp": NOW - 3 * DAY}]                          # too new
        entries += [{"view_count": v, "timestamp": NOW - (10 + i) * DAY} for i, v in enumerate(range(100, 1200, 100))]
        entries += [{"view_count": 999999, "timestamp": NOW - 400 * DAY}]                     # too old
        b = M.baseline_long(entries, NOW)
        self.assertEqual(b["n"], 11)
        self.assertEqual(b["median"], 600)

    def test_too_few_videos_is_not_scored(self):
        b = M.baseline_long([{"view_count": 5, "timestamp": NOW - 20 * DAY}] * 9, NOW)
        self.assertIsNone(b["median"])
        self.assertEqual(b["note"], "sample too small")

    def test_shorts_skip_the_newest_three(self):
        entries = [{"view_count": 1_000_000}] * 3 + [{"view_count": 100}] * 30 + [{"view_count": 5}] * 10
        b = M.baseline_shorts(entries)
        self.assertEqual((b["median"], b["n"]), (100, 30))

    def test_multiplier_tier_and_views_per_day(self):
        self.assertEqual(M.multiplier(3000, 1000), 3.0)
        self.assertEqual([M.tier(x) for x in (3.0, 2.9, 1.5, 1.4, None)], ["outlier", "borderline", "borderline", "normal", "unscored"])
        self.assertIsNone(M.multiplier(100, None))
        self.assertEqual(M.views_per_day(1000, NOW - 10 * DAY, NOW), 100.0)
        self.assertEqual(M.views_per_day(1000, NOW - 3600, NOW), 1000.0)  # under a day counts as one


class TestFeatures(unittest.TestCase):
    def test_a_short_is_vertical_and_at_most_180_seconds(self):
        self.assertTrue(M.is_short({"duration": 8, "width": 1080, "height": 1920}))
        self.assertFalse(M.is_short({"duration": 60, "width": 1920, "height": 1080}))
        self.assertFalse(M.is_short({"duration": 240, "width": 1080, "height": 1920}))
        self.assertIsNone(M.is_short({"duration": None}))

    def test_language_guess(self):
        self.assertEqual(M.language_guess("Hice una canción con inteligencia artificial"), "es")
        self.assertEqual(M.language_guess("I made a song with AI and this is how"), "en")
        self.assertIsNone(M.language_guess("SUNO V5 🔥"))
        self.assertEqual(M.language_guess("কিরে ভাই তুই রোজা না #aimusic"), "other")   # a Bengali Short under #aimusic

    def test_declared_audio_language_wins(self):
        self.assertEqual(M.spoken_language({"language": "en-US"}), "en")
        self.assertIsNone(M.spoken_language({"language": None}))

    def test_relevance_gate_and_paid_views(self):
        gate = r"\bai\b|suno|udio|artificial intelligence"
        self.assertTrue(M.relevant("BLACK WATER DAMNED BURNIN' BRIDGES Ai Music", gate))
        self.assertFalse(M.relevant("Ayra Starr - Rush (Official Music Video)", gate))
        self.assertTrue(M.relevant("anything", None))
        self.assertTrue(M.likely_paid_views(144_134_588, 50_000))      # 0.35 likes per 1,000 views
        self.assertFalse(M.likely_paid_views(2_027_204, 40_000))       # ~20 per 1,000: organic
        self.assertFalse(M.likely_paid_views(50_000, 10))              # too small to judge

    def test_on_topic_needs_the_ai_signal_and_the_topic(self):
        cfg = {"must_match": r"\bia\b|suno", "topic_match": r"m[uú]sica|canci[oó]n|cover", "categories": ["Music"]}
        goal = {"title": "El gol principal del Mundial 2026 😳", "channel": "Creatory – IA para crear música, fotos y vídeo",
                "categories": ["Sports"], "tags": []}
        self.assertFalse(M.on_topic(goal, cfg), "the channel name alone must not make football 'AI music'")
        cover = {"title": "Porque Te Vas – Jeanette (Flamenco Rumba IA Cover)", "channel": "La Fábrica", "categories": ["People & Blogs"]}
        self.assertTrue(M.on_topic(cover, cfg))
        by_category = {"title": "RIVER MUD", "channel": "BURNIN' BRIDGES IA Music", "categories": ["Music"]}
        self.assertTrue(M.on_topic(by_category, cfg))
        self.assertTrue(M.on_topic({"title": "x"}, {"must_match": None}))

    def test_sponsored(self):
        self.assertTrue(M.is_sponsored("Great track! Sponsored by Acme"))
        self.assertTrue(M.is_sponsored("nuevo tema #publicidad"))
        self.assertFalse(M.is_sponsored("an advertisement-free mix"))

    def test_title_features(self):
        f = M.title_features("I Made 3 Songs With SUNO AI?! 🔥 (Part 2)")
        self.assertTrue(all(f[k] for k in ("title_number", "title_question", "title_exclaim", "title_caps_word",
                                           "title_emoji", "title_brackets", "title_first_person", "title_names_ai_tool")))
        self.assertFalse(M.title_features("calm piano")["title_caps_word"])

    def test_json3_words_and_speech_stats(self):
        doc = {"events": [
            {"tStartMs": 0, "segs": [{"utf8": "[Music]"}]},
            {"tStartMs": 1200, "segs": [{"utf8": "this", "tOffsetMs": 0}, {"utf8": " song", "tOffsetMs": 300}, {"utf8": " was", "tOffsetMs": 600}]},
            {"tStartMs": 4000, "segs": [{"utf8": "made by AI", "tOffsetMs": 0}]},
            {"tStartMs": 20000, "segs": [{"utf8": "\n"}]},
        ]}
        words = M.parse_json3(doc)
        self.assertEqual([w["w"] for w in words][:4], ["[Music]", "this", "song", "was"])
        self.assertEqual(words[2]["t"], 1.5)
        s = M.speech_stats(words, 60)
        self.assertEqual(s["words_first_3s"], 3)
        self.assertEqual(s["first_words_at"], 1.2)
        self.assertEqual(s["caption_words"], 6)  # "[Music]" is not speech
        self.assertFalse(s["music_only"])

    def test_music_only_when_captions_are_just_music_tags(self):
        words = [{"t": float(i), "w": "[Music]"} for i in range(20)] + [{"t": 21.0, "w": "♪"}]
        self.assertTrue(M.speech_stats(words, 60)["music_only"])


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def time(self):
        return self.now

    def sleep(self, s):
        self.slept.append(round(s, 2))
        self.now += s


class TestYouTubeGate(unittest.TestCase):
    """The brake that pauses every worker after "Sign in to confirm you're not a bot" and retries."""

    def setUp(self):
        from platforms import youtube as YT
        self.YT = YT
        self.clock = FakeClock()
        self.gate = YT.Gate(min_interval=1.5, backoffs=(300, 600), max_blocked=1000, clock=self.clock.time, sleep=self.clock.sleep)

    def test_requests_are_paced(self):
        self.gate.wait()
        self.gate.wait()
        self.assertEqual(sum(self.clock.slept), 1.5)

    def test_a_bot_check_pauses_everyone_with_growing_backoff_then_resets(self):
        seen = []
        self.gate.on_block = lambda until, reason: seen.append(until - self.clock.now)
        self.gate.trip("Sign in to confirm you're not a bot")
        self.gate.trip("second worker, same block")          # ignored: already paused
        self.gate.wait()
        self.assertEqual(seen, [300])
        self.assertGreaterEqual(self.clock.now, 1300)
        self.gate.trip("still blocked")
        self.assertEqual(seen, [300, 600])
        self.gate.ok()                                          # a request worked: the block is over
        self.assertEqual((self.gate.trips, self.gate.blocked_since), (0, None))

    def test_gives_up_after_the_limit(self):
        self.gate.trip("bot")
        self.clock.now += 1000
        with self.assertRaises(self.YT.Blocked):
            self.gate.trip("bot")
        with self.assertRaises(self.YT.Blocked):
            self.gate.wait()                                    # every other worker stops too

    def test_ytdlp_retries_the_same_call_after_a_bot_check(self):
        from types import SimpleNamespace
        from unittest import mock
        answers = [SimpleNamespace(returncode=1, stdout="", stderr="Deprecated Feature: x\nERROR: [youtube] a: Sign in to confirm you're not a bot"),
                   SimpleNamespace(returncode=0, stdout='{"id": "a"}', stderr="")]
        with mock.patch.object(self.YT, "GATE", self.gate), mock.patch.object(self.YT.subprocess, "run", side_effect=answers) as run:
            self.assertEqual(self.YT._run(["--dump-json", "x"]), {"id": "a"})
        self.assertEqual(run.call_count, 2)
        self.assertGreaterEqual(self.clock.now, 1300)          # waited out the pause before retrying

    def test_saved_media_with_a_refused_caption_track_is_not_a_block(self):
        from types import SimpleNamespace
        from unittest import mock
        p = SimpleNamespace(returncode=1, stdout="", stderr="ERROR: Unable to download video subtitles for 'en': HTTP Error 429")
        with mock.patch.object(self.YT, "GATE", self.gate), mock.patch.object(self.YT.subprocess, "run", return_value=p) as run:
            self.YT._ytdlp(["x"], 10, done=lambda _p: True)
        self.assertEqual((run.call_count, self.gate.trips), (1, 0))

    def test_a_restart_keeps_the_pause_and_the_backoff(self):
        import tempfile
        state = Path(tempfile.mkdtemp()) / "gate.json"
        self.gate.persist(state)
        self.gate.trip("bot")
        again = self.YT.Gate(min_interval=1.5, backoffs=(300, 600), max_blocked=1000, clock=self.clock.time, sleep=self.clock.sleep)
        again.persist(state)
        self.assertEqual((again.blocked_until, again.trips), (1300, 1))
        again.wait()
        self.assertGreaterEqual(self.clock.now, 1300)          # no request before the old pause ended
        again.ok()
        self.assertFalse(state.exists())

    def test_a_pause_that_ended_long_ago_is_forgotten(self):
        import tempfile
        state = Path(tempfile.mkdtemp()) / "gate.json"
        state.write_text('{"until": 0, "since": 0, "trips": 3}', encoding="utf-8")
        self.gate.persist(state)
        self.assertEqual((self.gate.blocked_until, self.gate.trips), (0.0, 0))


class TestVisionLabels(unittest.TestCase):
    """Parsing the local vision model's short answers (MiniCPM-V 1B, as it really answers)."""

    def setUp(self):
        from analysis import vision as V
        self.V = V

    def test_text(self):
        self.assertEqual(self.V.parse_text("BLACKWATER\nDAMNED"), "BLACKWATER\nDAMNED")
        self.assertEqual(self.V.parse_text("Text: \"AI SONG\""), "AI SONG")
        for none in ("NONE", "None.", "There is no text in this image.", "", "N/A"):
            self.assertIsNone(self.V.parse_text(none), none)

    def test_face_accepts_the_kind_of_face_as_yes(self):
        for yes in ("Yes", "yes.", "Painted", "drawn face", "Rendered"):
            self.assertTrue(self.V.parse_yes_no(yes), yes)
        for no in ("No", "no.", "None"):
            self.assertFalse(self.V.parse_yes_no(no), no)
        self.assertIsNone(self.V.parse_yes_no("A woman in a red dress"))

    def test_style(self):
        self.assertEqual(self.V.parse_style("Illustration."), "illustration")
        self.assertEqual(self.V.parse_style("painting"), "illustration")
        self.assertEqual(self.V.parse_style("Photograph"), "photo")
        self.assertEqual(self.V.parse_style("CGI render"), "3d")
        self.assertIsNone(self.V.parse_style("beautiful"))

    def test_labels_and_features(self):
        thumb = self.V.labels("BLACKWATER DAMNED", "yes", "illustration", '"A hooded figure in a swamp"')
        self.assertEqual((thumb["text_words"], thumb["face"], thumb["style"], thumb["subject"]),
                         (2, True, "illustration", "A hooded figure in a swamp"))
        opening = self.V.labels("NONE", "maybe", "?")
        f = self.V.vision_features(thumb, opening)
        self.assertEqual(f, {"thumb_text": True, "thumb_text_words": 2, "thumb_face": True, "thumb_photo": False,
                             "thumb_illustration": True, "thumb_anime": False, "thumb_3d": False, "opening_text": False})
        self.assertEqual(self.V.vision_features(None, None), {})   # unparsed answers add no features

    def test_only_reworded_questions_are_asked_again(self):
        from unittest import mock
        asked = {k: q for k, (q, _) in self.V.QUESTIONS.items()}
        old = {"asked": {**asked, "style": "an older wording"},
               "answers": {"text": "FIRE & FROST", "face": "yes", "style": "text", "subject": "A fierce woman"}}
        self.assertEqual(set(self.V.reusable(old)), {"text", "face", "subject"})
        model = object.__new__(self.V.LocalVision)
        model.ffmpeg = "ffmpeg-not-needed"
        with mock.patch.object(self.V.LocalVision, "_ask", return_value="3d") as ask, \
             mock.patch.object(self.V.subprocess, "run"):
            image = Path(__file__)                                  # any readable file stands in for the image
            v = model.look(image, old)
        self.assertEqual(ask.call_count, 1)
        self.assertEqual((v["style"], v["text"], v["face"]), ("3d", "FIRE & FROST", True))
        self.assertEqual(self.V.reusable(v), v["answers"])        # a fresh label file is complete
        self.assertEqual(len(self.V.reusable(None)), 0)


def row(i, channel, features, mult=None, ts=None):
    return {"id": f"v{i}", "channel_id": channel, "features": features, "multiplier": mult, "timestamp": ts}


class TestPackIdeasMaterial(unittest.TestCase):
    """Material for the report's formula and 30 ideas: declared styles/prompts, and a wider outlier list."""

    def test_declared_style_and_tool_lines(self):
        from analysis.pack import declared
        desc = "Thanks for watching\nStyle: Emotional Live Performance, Piano Ballad\n🎶 Creado con IA (Suno)\nSubscribe!"
        self.assertEqual(declared(desc), ["Style: Emotional Live Performance, Piano Ballad", "🎶 Creado con IA (Suno)"])
        self.assertEqual(declared(None), [])

    def test_wider_outliers_are_on_niche_in_language_and_not_shortlisted(self):
        from analysis.pack import wider_outliers
        cfg = {"must_match": r"\bai\b|suno", "topic_match": r"song|music|cover"}
        base = {"bucket": "en-shorts", "tier": "outlier", "age_days": 10, "description": ""}
        rows = [{**base, "id": "a", "multiplier": 9, "title": "My AI song about the sea"},
                {**base, "id": "b", "multiplier": 50, "title": "AI football goal"},              # off-topic
                {**base, "id": "c", "multiplier": 40, "title": "Mi canción con la AI y la música"},   # Spanish
                {**base, "id": "d", "multiplier": 30, "title": "நாங்க தா மாடு AI SONG #ai #aisong #music"},  # other script
                {**base, "id": "e", "multiplier": 20, "title": "AI cover song", "age_days": 900},  # too old
                {**base, "id": "f", "multiplier": 8, "title": "Suno cover of a classic song"},
                {**base, "id": "g", "multiplier": 99, "title": "AI song shortlisted already"},
                {**base, "id": "h", "multiplier": 2, "title": "AI song", "tier": "borderline"}]
        got = [r["id"] for r in wider_outliers(rows, "en-shorts", {"g"}, cfg, 365)]
        self.assertEqual(got, ["a", "f"])


class TestDemand(unittest.TestCase):
    """Viewer requests, replay peaks, concept proof/crowding/holdout, autocomplete parsing."""

    def setUp(self):
        from analysis import demand as Dm
        self.Dm = Dm

    def test_viewer_requests_most_liked_first_without_the_uploader(self):
        comments = [{"text": "Please do a salsa version next!", "likes": 4, "author_is_uploader": False},
                    {"text": "Haz una versión en rumba porfa", "likes": 30, "author_is_uploader": False},
                    {"text": "Next one is coming, thanks!", "likes": 99, "author_is_uploader": True},
                    {"text": "Love this song", "likes": 500, "author_is_uploader": False}]
        self.assertEqual([x["likes"] for x in self.Dm.viewer_requests(comments)], [30, 4])

    def test_replay_peak_skips_the_opening(self):
        heat = [{"start_time": 0.0, "value": 1.0}, {"start_time": 30.0, "value": 0.4}, {"start_time": 75.0, "value": 0.8}]
        self.assertEqual(self.Dm.replay_peak(heat, 100), {"at_s": 75.0, "at_pct": 75, "value": 0.8})
        self.assertIsNone(self.Dm.replay_peak([], 100))

    def test_concepts_proof_lift_crowding_and_holdout(self):
        def r(i, ch, tier, title, age, mult=5.0, bucket="es-long"):
            return {"id": f"v{i}", "channel_id": ch, "tier": tier, "title": title, "age_days": age, "multiplier": mult,
                    "bucket": bucket, "description": ""}
        rows = [r(1, "a", "outlier", "Cumbia cover IA", 60), r(2, "b", "outlier", "Rumba cover IA", 10),
                r(3, "c", "outlier", "Metal cover IA", 200), r(4, "a", "ordinary", "Cover IA", 20),
                r(5, "d", "ordinary", "Mi canción", 40), r(6, "e", "ordinary", "Otra canción", 50),
                r(7, "f", "ordinary", "Vlog", 400), r(8, "g", "outlier", "Original song", 5)]
        got = self.Dm.concept_counts(rows, [{"name": "genre-swap cover", "match": r"\bcover\b"},
                                            {"name": "vlog", "match": r"vlog"}])
        cover = got[0]
        self.assertEqual((cover["name"], cover["hits"], cover["outliers"], cover["channels_with_outliers"], cover["proven"]),
                         ("genre-swap cover", 4, 3, 3, True))
        self.assertEqual(cover["outlier_rate"], 0.75)
        self.assertEqual(cover["lift"], round(0.75 / 0.5, 2))           # the bucket's overall rate is 4/8
        self.assertEqual(cover["recent_supply"], 3)                      # 60, 10 and 20 days old
        self.assertEqual(cover["holdout"], {"older_rate": 1.0, "older_n": 2, "newest_rate": 0.5, "newest_n": 2})
        self.assertFalse(got[1]["proven"])

    def test_autocomplete_jsonp(self):
        body = 'window.google.ac.h(["ai song",[["ai song cover",0,[512]],["ai song generator",0,[512]]],{"k":1}])'
        self.assertEqual(self.Dm.parse_autocomplete(body), ["ai song cover", "ai song generator"])
        self.assertEqual(self.Dm.parse_autocomplete("garbage"), [])


class TestVisionStage(unittest.TestCase):
    """The vision stage never holds the run hostage: a time limit, and a stop when its model server dies."""

    def run_stage(self, settings, alive=True):
        import tempfile
        from types import SimpleNamespace
        from unittest import mock
        import viral_research as VR
        d = Path(tempfile.mkdtemp())
        for vid in ("a", "b"):
            (d / "evidence" / vid / "frames").mkdir(parents=True)
            (d / "evidence" / vid / "thumbnail.jpg").write_bytes(b"jpg")
            (d / "evidence" / vid / "frames" / "frame_001.0s.jpg").write_bytes(b"jpg")
        run = SimpleNamespace(dir=d, load=lambda name, default=None: default)
        final = {"en-long": {"outliers": [{"id": "a"}], "controls": [{"id": "b"}]}}
        model = mock.Mock()
        model.proc.poll.return_value = None if alive else 1
        model.look.return_value = {"text": None}
        warnings = []
        with mock.patch.dict(VR.SETTINGS, {"vision": settings}), mock.patch.object(VR.V, "LocalVision", return_value=model), \
             mock.patch.object(VR, "warn", warnings.append), mock.patch.object(VR, "progress"), mock.patch.object(VR, "log"):
            VR.vision(run, final)
        return model, warnings

    def test_labels_thumbnails_first(self):
        model, _ = self.run_stage({"enabled": True, "server": "s", "model": "m", "mmproj": "p"})
        kinds = [call.args[0].name for call in model.look.call_args_list]
        self.assertEqual(kinds, ["thumbnail.jpg", "thumbnail.jpg", "frame_001.0s.jpg", "frame_001.0s.jpg"])

    def test_time_limit_moves_on(self):
        model, warnings = self.run_stage({"enabled": True, "server": "s", "model": "m", "mmproj": "p", "max_minutes": -1})
        self.assertEqual(model.look.call_count, 0)
        self.assertIn("4 images left for the next run", warnings[0])
        model.close.assert_called_once()

    def test_a_dead_model_server_stops_the_stage(self):
        model, warnings = self.run_stage({"enabled": True, "server": "s", "model": "m", "mmproj": "p"}, alive=False)
        self.assertEqual(model.look.call_count, 0)
        self.assertIn("model server stopped", warnings[0])


class TestReportHtml(unittest.TestCase):
    def test_the_markdown_the_reports_use(self):
        from analysis.report_html import to_html
        md = ("# Title\n\nSome **bold**, *italic*, `code` and a [link](https://example.com).\n\n"
              "| a | b |\n|---|---|\n| 1 | x \\| y |\n\n- one\n  continued\n- two\n\n1. first\n\n```\nffmpeg -i <in>\n```\n\n---\n")
        h = to_html(md, "T")
        for part in ("<h1>Title</h1>", "<strong>bold</strong>", "<em>italic</em>", "<code>code</code>",
                     '<a href="https://example.com"', "<td>1</td>", "<li>one continued</li>", "<ol><li>first</li></ol>",
                     "<pre><code>ffmpeg -i &lt;in&gt;</code></pre>", "<hr>"):
            self.assertIn(part, h)
        self.assertNotIn("<script", to_html("<script>alert(1)</script>"))   # text is escaped


class TestPlaybook(unittest.TestCase):
    """Slot mining, evidence resolved from stats (never typed), and a scorecard that keeps the user's numbers."""

    def test_title_and_description_slots(self):
        from analysis import playbook as PB
        t = PB.title_slots("Amanda Miguel – Él Me Mintió | Cover IA Heavy Metal 80s dramático")
        self.assertTrue(t["starts_artist_dash_song"] and t["pipe"] and t["genre_word"] and t["era_word"] and t["emotion_word"]
                        and t["cover_word"] and t["ai_word"])
        self.assertFalse(t["emoji"] or t["brackets"])
        d = PB.description_slots("Rumba flamenca con guitarra española y cajón.\nLa historia...\nCreado con IA\n#rumba #flamenco")
        self.assertEqual((d["first_line_kind"], d["first_line_is_style"], d["ai_disclosure"], d["hashtags"], d["hashtags_last"]),
                         ("style", True, True, 2, True))
        self.assertEqual(PB.title_signature("💔 Hasta Aquí | IA Flamenco Afro House Remix (Flamenco Fusión 2026)"), "flamenco fusión #")

    def test_channel_consistency_and_rhythm(self):
        from analysis import playbook as PB
        now = 100 * 86400
        entries = [{"title": f"Song {i} | Cover IA Heavy Metal emotivo", "timestamp": now - i * 7 * 86400, "views": 100 + i}
                   for i in range(20)]
        p = PB.channel_profile(entries, now, [{"name": "genre swap", "match": r"cover ia heavy metal"}])
        self.assertEqual((p["main_genre_word"], p["main_genre_share"], p["consistency"]), ("metal", 1.0, 1.0))
        self.assertEqual(p["uploads_per_week_90d"], 1.0)                     # 13 of the 20 weekly uploads are within 90 days

    def test_evidence_comes_from_the_stats(self):
        from analysis import playbook_page as PP
        stats = {"rankings": {"es-long": {"title": {"starts_artist_dash_song": {"outliers": 6, "controls": 1, "n": [12, 12]}},
                                          "tags": {"count": {"outliers_median": 10.5, "controls_median": 0.0, "n": [12, 12]}}}}}
        analysis = {"en-long": {"comparison": [{"feature": "lufs", "kind": "num", "median_outliers": -14.2, "median_controls": -15.65,
                                                "pattern": True}]}}
        r = PP.resolve({"label": "x", "slot": "es-long/title/starts_artist_dash_song"}, stats, analysis, [])
        self.assertEqual((r["value"], r["strength"]), ("6/12 outliers vs 1/12 controls", "strong"))
        self.assertEqual(PP.resolve({"slot": "es-long/tags/count"}, stats, analysis, [])["value"], "median 10.5 vs 0.0")
        self.assertEqual(PP.resolve({"measure": "en-long/lufs"}, stats, analysis, [])["value"], "median -14.2 vs -15.65")
        self.assertEqual(PP.resolve({"slot": "xx/title/nope"}, stats, analysis, [])["value"], "no data")
        self.assertEqual(PP.resolve({"kind": "policy", "text": "t"}, stats, analysis, [])["strength"], "info")

    def test_scorecard_keeps_what_the_user_filled_in(self):
        import csv
        import tempfile
        from analysis import playbook_page as PP
        path = Path(tempfile.mkdtemp()) / "scorecard.csv"
        ideas = [{"n": 1, "ranking": "es-long", "concept": "c", "title": "La Llorona", "score": 27, "band": "2–5×", "lever": "l"}]
        PP.merge_scorecard(path, ideas)
        read = lambda: list(csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()))
        rows = read()
        rows[0]["views_day_7"] = "1234"
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=PP.SCORE_FIELDS)
            w.writeheader()
            w.writerows(rows)
        PP.merge_scorecard(path, [{**ideas[0], "n": 5, "score": 26, "band": "2–5×"}])   # renumbered, rescored
        row = read()[0]
        self.assertEqual((row["views_day_7"], row["idea"], row["score"]), ("1234", "5", "26"))
        self.assertEqual((PP.score({"e": 3, "d": 3, "g": 3, "cost": 1}), PP.band(26), PP.band(3)), (26, "2–5×", "0.5–1.5×"))


class TestRequestBudget(unittest.TestCase):
    """Seeds first, then only channels with an on-niche video, up to the budget."""

    def test_pick_channels(self):
        import viral_research as VR
        cfg = {"en": {"must_match": r"samurai|zen", "topic_match": r"music|ambient"}}
        found = {"en": [
            {"id": "1", "channel_id": "big-offniche", "title": "Samurai sword fight scene", "views": 9_000_000, "sources": ["search:x"]},
            {"id": "2", "channel_id": "zen1", "title": "Samurai zen music for sleep", "views": 1_000, "sources": ["search:x"]},
            {"id": "3", "channel_id": "zen2", "title": "Zen ambient", "views": 50_000, "sources": ["search:x"]},
            {"id": "4", "channel_id": "zen2", "title": "Samurai music, 3 hours", "views": 10, "sources": ["search:x"]},
            {"id": "5", "channel_id": "seed", "title": "anything", "views": 1, "sources": ["seed:@AetherJourneyMusic"]},
            {"id": "6", "channel_id": None, "title": "Samurai music", "views": 1, "sources": ["hashtag:x"]}]}
        self.assertEqual(VR.pick_channels(found, cfg, 10), ["seed", "zen2", "zen1"])
        self.assertEqual(VR.pick_channels(found, cfg, 2), ["seed", "zen2"])

    def test_seed_handles_are_read_from_any_form(self):
        from unittest import mock
        from platforms import youtube as YT
        doc = {"channel_id": "UC1", "channel": "Aether Journey", "entries": [{"id": "a"}, None, {"title": "no id"}]}
        for given in ("@AetherJourneyMusic", "AetherJourneyMusic", "https://www.youtube.com/@AetherJourneyMusic/videos"):
            with mock.patch.object(YT, "_run", return_value=doc) as run:
                got = YT.handle_tab(given)
            self.assertEqual(run.call_args.args[0][-1], "https://www.youtube.com/@AetherJourneyMusic/videos")
            self.assertEqual((got["channel_id"], [e["id"] for e in got["entries"]]), ("UC1", ["a"]))


class TestShortlist(unittest.TestCase):
    """Old evergreen videos at the top of the list must not leave the ranking empty: keep confirming candidates."""

    def test_keeps_checking_candidates_until_the_ranking_is_full(self):
        import tempfile
        from types import SimpleNamespace
        from unittest import mock
        import viral_research as VR
        now = 2_000_000_000.0
        d = Path(tempfile.mkdtemp())
        (d / "meta").mkdir()

        def meta_for(vid, age_days, views, channel="ch"):
            return {"id": vid, "title": f"Samurai zen music {vid}", "description": "", "channel": "Chan", "channel_id": channel,
                    "view_count": views, "like_count": views // 20, "timestamp": now - age_days * 86400, "duration": 3600,
                    "width": 1280, "height": 720, "language": "en", "categories": ["Music"], "live_status": None,
                    "webpage_url": f"https://youtu.be/{vid}"}
        metas = {f"c{i}": meta_for(f"c{i}", 900 if i <= 6 else 30, 10_000 - i, channel=f"ch{i}") for i in range(1, 15)}
        metas.update({"o1": meta_for("o1", 40, 100, "ch7"), "o2": meta_for("o2", 50, 110, "ch7")})
        fetched = []

        def meta(vid):
            fetched.append(vid)
            (d / "meta" / f"{vid}.json").write_text(json.dumps(metas[vid]), encoding="utf-8")
            return metas[vid]
        run = SimpleNamespace(dir=d, now=now, path=lambda n: d / n, meta=meta, niche="t",
                              load=lambda n, default=None: json.loads((d / n).read_text(encoding="utf-8")) if (d / n).exists() else default,
                              save=lambda n, data: (d / n).write_text(json.dumps(data), encoding="utf-8"))
        rows = [{"id": f"c{i}", "bucket": "en-long", "channel_id": f"ch{i}", "multiplier": 100 - i, "age_days": None,
                 "channel_median": 100, "title": f"c{i}"} for i in range(1, 15)]
        channels = {"ch7": {"videos": {"median": 100, "entries": [{"id": "o1", "views": 100}, {"id": "o2", "views": 110}]}}}
        cfg = {"en": {"must_match": "samurai", "topic_match": "music", "categories": ["Music"]}}
        with mock.patch.object(VR, "N_OUTLIERS", 2), mock.patch.object(VR, "niche_config", return_value=cfg), \
             mock.patch.object(VR, "parallel", lambda fn, items, **kw: [fn(x) for x in items]), \
             mock.patch.object(VR, "log", lambda m: None), mock.patch.dict(VR.SETTINGS, {"max_shortlist_candidates": 60}):
            final = VR.shortlist(run, rows, channels)
        p = final["en-long"]
        self.assertEqual([r["id"] for r in p["outliers"]], ["c7", "c8"])
        self.assertEqual(len(p["excluded"]), 6)                               # c1-c6: older than 365 days
        self.assertTrue(all("older than" in x["reason"] for x in p["excluded"]))
        self.assertEqual([r["id"] for r in p["controls"]], ["o1", "o2"][:len(p["controls"])])
        self.assertNotIn("c13", fetched)       # round 1: c1-c6 (all too old), round 2: c7-c12 — then the ranking is full
        self.assertNotIn("c14", fetched)


class TestLongVideoOpening(unittest.TestCase):
    """Hours-long videos: only the opening is fetched; never the whole file."""

    def test_bytes_for_the_opening(self):
        from platforms import youtube as YT
        self.assertEqual(YT.opening_bytes(400, 90), int(400 * 1000 / 8 * 120 * 1.5))   # 9 MB for 400 kbps
        self.assertEqual(YT.opening_bytes(None, 90), int(800 * 1000 / 8 * 120 * 1.5))
        self.assertEqual(YT.opening_bytes(20, 10), 1_500_000)                            # floor

    def test_no_full_download_fallback_for_hours_long_videos(self):
        import tempfile
        from unittest import mock
        from platforms import youtube as YT
        d = Path(tempfile.mkdtemp())
        with mock.patch.object(YT, "opening", side_effect=YT.YtDlpError("403")), mock.patch.object(YT, "_ytdlp") as full:
            with self.assertRaises(YT.YtDlpError):
                YT.download("x", d, "ffdir", 90, duration=3 * 3600)
            full.assert_not_called()
            full.return_value.stderr = ""
            with self.assertRaises(YT.YtDlpError):    # a 4-minute song may fall back to the small full file
                YT.download("y", d, "ffdir", 90, duration=240)
            full.assert_called_once()


class TestDashboardLaunch(unittest.TestCase):
    def test_the_dashboard_is_not_a_child_of_the_run(self):
        """Stopping a run (taskkill /T, the Stop button) must not take its dashboard down."""
        import tempfile
        from types import SimpleNamespace
        from unittest import mock
        import viral_research as VR
        run = SimpleNamespace(dir=Path(tempfile.mkdtemp()), niche="t", load=lambda n, default=None: default)
        with mock.patch.object(VR.subprocess, "Popen") as popen, mock.patch.object(VR, "log"):
            VR.ensure_dashboard(run)
        self.assertEqual(popen.call_args.args[0][:5], ["cmd", "/c", "start", "", "/b"])


class TestPartialRun(unittest.TestCase):
    """YouTube refusing the media downloads still yields a pack, marked partial, and the run ends blocked."""

    def test_blocked_evidence_continues_to_a_partial_pack(self):
        import tempfile
        from types import SimpleNamespace
        from unittest import mock
        import viral_research as VR
        calls = []
        stubs = {name: mock.Mock() for name in ("discover", "resolve", "baselines", "score", "mine_shorts", "shortlist",
                                                "vision", "analyze", "check")}
        with mock.patch.multiple(VR, **stubs, evidence=mock.Mock(side_effect=VR.YT.Blocked("bot check")),
                                 stage=lambda key, title: calls.append(key), warn=lambda msg: None, log=lambda msg: None), \
             mock.patch("analysis.pack.write_pack", side_effect=lambda run, partial: calls.append(("pack", partial))):
            with self.assertRaises(VR.YT.Blocked):
                VR._pipeline(SimpleNamespace(dir=Path(tempfile.mkdtemp())), ["en"], len(VR.STAGES) - 1)
        self.assertEqual(calls, ["discover", "baseline", "score", "shortlist", "evidence", "vision", "analyze", "pack",
                                 ("pack", "bot check")])
        stubs["check"].assert_not_called()                          # watch pages are YouTube requests too


class TestPatterns(unittest.TestCase):
    def test_a_pattern_needs_a_gap_three_outliers_and_two_channels(self):
        outliers = [row(i, f"c{i % 3}", {"title_number": i < 4, "solo": i == 0, "cuts": 30.0 + i}) for i in range(5)]
        controls = [row(10 + i, f"c{i % 3}", {"title_number": i == 0, "solo": False, "cuts": 10.0 + i}) for i in range(5)]
        by = {c["feature"]: c for c in P.compare(outliers, controls)}
        self.assertTrue(by["title_number"]["pattern"])
        self.assertEqual(by["title_number"]["outliers"], "4/5")
        self.assertFalse(by["solo"]["pattern"], "one outlier is luck, not a recipe")
        self.assertTrue(by["cuts"]["pattern"])
        self.assertEqual(by["cuts"]["direction"], "higher")

    def test_one_channel_is_not_a_recipe(self):
        outliers = [row(i, "same", {"x": True}) for i in range(5)]
        controls = [row(10 + i, "same", {"x": False}) for i in range(5)]
        self.assertFalse(P.compare(outliers, controls)[0]["pattern"])

    def test_less_common_features_are_patterns_too(self):
        outliers = [row(i, f"c{i}", {"long_title": False}) for i in range(4)]
        controls = [row(10 + i, f"c{i}", {"long_title": True}) for i in range(4)]
        c = P.compare(outliers, controls)[0]
        self.assertEqual((c["direction"], c["pattern"]), ("less", True))
        self.assertTrue(P.matches({"features": {"long_title": False}}, c))

    def test_backtest_holds_when_the_feature_really_predicts(self):
        rng = random.Random(1)
        rows = []
        for i in range(120):
            hit = rng.random() < 0.4
            mult = rng.uniform(3.2, 9) if hit and rng.random() < 0.8 else rng.uniform(0.3, 1.4)
            rows.append(row(i, f"c{i % 9}", {"hook": hit, "noise": rng.random() < 0.5}, mult, NOW - (200 - i) * DAY))
        bt = P.backtest(rows)
        self.assertTrue(bt["ok"])
        self.assertIn("hook:more", bt["learned"])
        self.assertTrue(bt["holds"])

    def test_backtest_reports_too_little_data(self):
        self.assertFalse(P.backtest([row(i, "c", {}, 1.0, NOW) for i in range(10)])["ok"])


if __name__ == "__main__":
    unittest.main()
