"""viral_research prelaunch: dedupe, ages, small baselines, deterministic statistics, evidence ledger, rights matrix,
failed fetches and report regeneration - no network, no media."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "@modules" / "viral_research"))
from analysis import prelaunch as PL  # noqa: E402
import prelaunch as P  # noqa: E402

NOW = 1_800_000_000
DAY = 86400


def entry(i, views, age_days, duration=3600, title="Japanese Zen Music for Meditation"):
    return {"id": f"v{i}", "views": views, "timestamp": NOW - age_days * DAY, "duration": duration, "title": title}


class TestClassification(unittest.TestCase):
    def test_on_niche_needs_japanese_theme_and_music_and_not_an_unrelated_artist(self):
        self.assertTrue(PL.on_niche("Japanese Zen Music for Sleep"))
        self.assertFalse(PL.on_niche("JENNIE - ZEN (Official Video)"))      # pop song that matched "zen"
        self.assertFalse(PL.on_niche("Samurai game walkthrough part 3"))
        self.assertFalse(PL.on_niche("Relaxing piano music"))               # music but not Japanese-themed

    def test_direction_rules(self):
        self.assertEqual(PL.direction("Guided breathing with shakuhachi"), "mindfulness")
        self.assertEqual(PL.direction("Epic Taiko War Drums"), "epic")
        self.assertEqual(PL.direction("Samurai Lofi Hip Hop beats to study"), "lofi")
        self.assertEqual(PL.direction("Japanese meditation music for deep sleep"), "zen_sleep")   # sleep beats meditation
        self.assertEqual(PL.direction("Samurai Meditation Music"), "mindfulness")
        self.assertEqual(PL.direction("Shakuhachi & Koto ambience"), "zen_sleep")


class TestSnapshots(unittest.TestCase):
    def test_dedupe_keeps_the_latest_snapshot_and_sorts_by_id(self):
        rows = [{"id": "b", "views": 10, "fetched_at": 1}, {"id": "a", "views": 5, "fetched_at": 1},
                {"id": "b", "views": 12, "fetched_at": 2}, {"id": None, "views": 1}]
        out = PL.dedupe(rows)
        self.assertEqual([r["id"] for r in out], ["a", "b"])
        self.assertEqual(out[1]["views"], 12)

    def test_missing_metadata_does_not_break_anything(self):
        self.assertIsNone(PL.age_days(None, NOW))
        self.assertEqual(PL.age_band(None), "unknown")
        self.assertEqual(PL.views_precision(None), "missing")
        self.assertEqual(PL.summarize([None, None]), {"n": 0})
        b = PL.baseline([{"id": "x", "views": None, "timestamp": None}], NOW)
        self.assertFalse(b["usable"])

    def test_age_normalisation(self):
        self.assertEqual(PL.age_days(NOW - 10 * DAY, NOW), 10.0)
        self.assertEqual(PL.age_days(NOW + DAY, NOW), 0.0)                 # clock skew never gives a negative age
        self.assertEqual([PL.age_band(d) for d in (3, 20, 60, 200, 500)], ["0-7d", "8-30d", "31-90d", "91-365d", ">365d"])
        self.assertEqual(PL.views_precision(999), "exact")
        self.assertEqual(PL.views_precision(1200), "rounded")

    def test_channel_age_is_only_new_when_the_whole_history_is_visible(self):
        self.assertEqual(PL.channel_age_band(200, True), "new (<12 months)")
        self.assertEqual(PL.channel_age_band(365, True), "established")    # "1 year ago" is too coarse to call new
        self.assertEqual(PL.channel_age_band(200, False), "unknown (history truncated)")
        self.assertEqual(PL.channel_age_band(800, False), "established")


class TestBaselinesAndScores(unittest.TestCase):
    def test_relative_score_against_a_usable_baseline(self):
        ents = [entry(i, 1000, 30 + i) for i in range(6)]
        b = PL.baseline(ents, NOW)
        self.assertTrue(b["usable"])
        self.assertEqual(PL.relative(3000, b), 3.0)

    def test_small_baselines_are_refused(self):
        tiny = PL.baseline([entry(i, 40, 30 + i) for i in range(8)], NOW)
        self.assertFalse(tiny["usable"])
        self.assertIn("median", tiny["reason"])
        self.assertIsNone(PL.relative(4000, tiny))                         # no "100x" from a 40-view channel
        few = PL.baseline([entry(i, 5000, 30 + i) for i in range(3)], NOW)
        self.assertFalse(few["usable"])
        self.assertIn("only 3", few["reason"])

    def test_baseline_window_excludes_too_new_too_old_and_shorts(self):
        ents = [entry(1, 10, 3), entry(2, 10, 900), entry(3, 10, 30, duration=60)] + [entry(10 + i, 500, 40) for i in range(5)]
        self.assertEqual(PL.baseline(ents, NOW)["n"], 5)

    def test_bootstrap_and_summary_are_deterministic(self):
        v = [5, 80, 120, 300, 310, 900, 1500, 40000]
        self.assertEqual(PL.bootstrap_median_ci(v), PL.bootstrap_median_ci(v))
        self.assertEqual(PL.summarize(v), PL.summarize(list(v)))
        lo, hi = PL.bootstrap_median_ci(v)
        self.assertLessEqual(lo, PL.summarize(v)["median"])
        self.assertGreaterEqual(hi, PL.summarize(v)["median"])
        self.assertIsNone(PL.bootstrap_median_ci([1, 2]))
        self.assertTrue(PL.summarize([1, 2, 3])["small_sample"])
        self.assertEqual(PL.without_top([1, 9, 5]), [1, 5])

    def test_stratified_sample_is_deterministic_and_mixes_strata(self):
        chans = [{"channel_id": f"c{i}", "direction": "lofi", "channel_age_band": "new (<12 months)" if i < 3 else "established",
                  "size_band": "<1K" if i % 2 else ">=100K"} for i in range(10)]
        a, b = P.stratified(chans, 4), P.stratified(list(reversed(chans)), 4)
        self.assertEqual([c["channel_id"] for c in a["lofi"]], [c["channel_id"] for c in b["lofi"]])
        self.assertEqual(len({(c["channel_age_band"], c["size_band"]) for c in a["lofi"]}), 4)


class TestEvidenceAndRights(unittest.TestCase):
    def test_claim_rules(self):
        ok = {"status": "MEASURED", "confidence": "HIGH", "source": "x", "date": "2026-10-27", "limitations": "y"}
        self.assertEqual(PL.check_claim(ok), [])
        self.assertTrue(PL.check_claim({**ok, "source": ""}))
        self.assertTrue(PL.check_claim({**ok, "status": "UNTESTED"}))       # UNTESTED can't be HIGH
        self.assertTrue(PL.check_claim({**ok, "status": "GUESS"}))
        self.assertTrue(PL.check_claim({**ok, "limitations": ""}))

    def test_rights_strictest_problem_wins(self):
        full = {"terms_verified": True, "plan_known": True, "provenance_complete": True}
        self.assertEqual(PL.rights_status(full)[0], "VERIFIED FOR INTENDED USE")
        self.assertEqual(PL.rights_status({**full, "conditions": ["disclose AI"]})[0], "CONDITIONALLY PERMITTED")
        self.assertEqual(PL.rights_status({**full, "clarify": ["after cancellation?"], "conditions": ["x"]})[0], "NEEDS WRITTEN CLARIFICATION")
        status, why = PL.rights_status({**full, "plan_known": False, "clarify": ["x"]})
        self.assertEqual(status, "NEEDS EVIDENCE")
        self.assertIn("subscription tier at creation not documented", why)
        self.assertEqual(PL.rights_status({**full, "blocked_reasons": ["misattributed quote"]})[0], "BLOCKED")
        self.assertEqual(PL.rights_status({})[0], "NEEDS EVIDENCE")        # nothing known is never "cleared"

    def test_only_verified_or_conditional_assets_are_publishable(self):
        self.assertTrue(PL.publishable("CONDITIONALLY PERMITTED"))
        for s in ("NEEDS EVIDENCE", "NEEDS WRITTEN CLARIFICATION", "BLOCKED"):
            self.assertFalse(PL.publishable(s))


def make_run(tmp: Path) -> P.Run:
    """A complete miniature run: raw snapshots, owner inventory, audio audit and knowledge templates."""
    knowledge = tmp / "knowledge"
    knowledge.mkdir()
    raw = tmp / "run" / "raw"
    (raw / "validation").mkdir(parents=True)
    (raw / "samurai_zen_run").mkdir()
    at = NOW
    tabs = {}
    for c in range(4):
        title = ["Samurai Lofi Hip Hop beats", "Japanese Zen Music for Sleep", "Samurai Meditation Music", "Epic Taiko War Drums"][c]
        tabs[f"UC{c}"] = {"fetched_at": at, "channel": f"Chan {c}", "subs": [500, 5000, 50000, 900][c],
                          "entries": [{**entry(f"{c}_{i}", 100 * (i + 1) * (c + 1), 10 + 20 * i, title=title)} for i in range(12)]}
    (raw / "validation" / "tabs.json").write_text(json.dumps(tabs))
    (raw / "validation" / "fresh_month.json").write_text(json.dumps({"lofi|samurai lofi": {"entries": [
        {"id": "f1", "title": "Samurai Lofi beats", "channel_id": "UCnew", "views": 50}]}}))
    (raw / "validation" / "searches.json").write_text(json.dumps({"lofi|samurai lofi|relevance": {"entries": [
        {"id": "s1", "views": 2_000_000}, {"id": "s2", "views": 3000}]}}))
    (raw / "validation" / "velocity.json").write_text(json.dumps({"v": {"then": 10000, "now": 10100, "then_at": at - DAY, "now_at": at,
                                                                        "timestamp": at - 200 * DAY}}))
    (raw / "samurai_zen_run" / "discovery.json").write_text(json.dumps({"en": [{}, {}], "es": [{}]}))
    (raw / "samurai_zen_run" / "scored.json").write_text(json.dumps({"rows": [
        {"id": "a", "bucket": "en-long", "tier": "outlier", "age_days": 400, "channel": "x", "title": "Zen"},
        {"id": "a", "bucket": "es-long", "tier": "outlier", "age_days": 400, "channel": "x", "title": "Zen"},
        {"id": "b", "bucket": "en-long", "tier": "normal", "age_days": 5, "channel": "JENNIE", "title": "ZEN"}],
        "dropped": {"language": 1}}))
    (raw / "assets.json").write_text(json.dumps({"files": [
        {"group": "sounds", "path": "INPUT/SOUNDS/t1_2026.mp3", "kind": "music", "sha256": "ab"},
        {"group": "fonts", "path": "@assets/fonts/X.ttf", "kind": "font", "sha256": "cd"}],
        "quotes": [{"en": "Perseverance is falling seven times and rising eight.", "jp": "七転び八起き"},
                   {"en": "Gratitude turns what we have into more than enough.", "jp": "..."}]}))
    track = {"duration_s": 180.0, "integrated_lufs": -14.0, "true_peak_dbtp": -1.4, "lra_lu": 3.0, "tempo_bpm_est": 80.0,
             "beat_clarity": 0.7, "edges": {"hard_start": True}, "original": {"integrated_lufs": -13.0, "true_peak_dbtp": 0.0, "lra_lu": 5.0},
             "eq_change_db": {"20-150Hz": 0.5, "150-500Hz": -1.0, "6000-11000Hz": 2.0}}
    (raw / "audio_audit.json").write_text(json.dumps({
        "tracks": {"t1_2026.mp3": track, "t2_2026.mp3": track},
        "pairs": [{"a": "t1_2026.mp3", "b": "t2_2026.mp3", "same_generation": True, "score": 0.9, "a_at_s": 5, "b_at_s": 9,
                   "above_unrelated_p95": True}],
        "unrelated_pair_p95": 0.8, "unrelated_pair_median": 0.7,
        "joins": [{"from": "t1_2026.mp3", "to": "t2_2026.mp3", "end_of_a_db": -40, "start_of_b_db": -12, "jump_db": 28, "b_hard_start": True}]}))
    (raw / "tools.json").write_text(json.dumps({"python": "test"}))
    (knowledge / "rights.json").write_text(json.dumps({
        "groups": {"sounds": {"terms_verified": True, "plan_known": False, "provenance_complete": False},
                   "fonts": {"terms_verified": True, "plan_known": True, "provenance_complete": True, "conditions": ["OFL"]}},
        "quotes": {"classes": {"PROVERB": {"terms_verified": True, "plan_known": True, "provenance_complete": True, "conditions": ["label"]},
                               "OTHER-SOURCE-MODERN": {"blocked_reasons": ["modern author"]},
                               "ORIGINAL/GENERIC": {"terms_verified": True, "plan_known": True, "provenance_complete": True}},
                   "by_number": {"1": "PROVERB", "2": "OTHER-SOURCE-MODERN"}}}))
    (knowledge / "economics.json").write_text(json.dumps({
        "budget_usd": 100, "plans_usd": {"Creator": 22, "Kling": 32.56}, "music_generation_minutes": {"Creator": 62},
        "illustrative_rpm_usd": [1], "scenarios": [{"name": "base", "music_plan": "Creator", "paid_tools": ["Creator", "Kling"],
                                                     "kept_share": 0.7, "video_minutes": 45, "note": ""}]}))
    (knowledge / "claims.json").write_text(json.dumps({"claims": [
        {"id": "C1", "claim": "x", "status": "MEASURED", "confidence": "HIGH", "source": "s", "date": "d", "limitations": "l"}]}))
    (knowledge / "values.json").write_text(json.dumps({"limitations": "- none"}))
    for name in P.OUTPUTS:
        (knowledge / name).write_text("# t\n{{comparison_table}}\n{{benchmark_table}}\n{{music_table}}\n{{rights_summary}}\n"
                                      "{{rights_blocked}}\n{{capacity_table}}\n{{old_check_table}}\n{{limitations}}\n", encoding="utf-8")
    run = P.Run.__new__(P.Run)
    run.niche, run.knowledge, run.dir, run.raw, run.norm, run.created = "test", knowledge, tmp / "run", raw, tmp / "run" / "normalized", "test"
    run.norm.mkdir()
    run.cfg = {"sample": {"channels_per_direction": 15, "new_candidates_per_direction": 2, "page_limit": 60,
                          "min_on_niche_uploads": 3, "min_on_niche_share": 0.3},
               "owner": {"ffmpeg": "ffmpeg"}, "sources": {}}
    return run


class TestRunStages(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.run = make_run(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_writes_every_output_and_regenerates_identically(self):
        P.build(self.run)
        names = P.OUTPUTS + ["competitor_dataset.csv", "rights_matrix.csv", "evidence_ledger.csv", "analysis.json", "research_manifest.json"]
        first = {n: (self.run.dir / n).read_text(encoding="utf-8") for n in names}
        P.build(self.run)
        second = {n: (self.run.dir / n).read_text(encoding="utf-8") for n in names}
        self.assertEqual(first, second)
        rights = first["rights_matrix.csv"]
        self.assertIn("BLOCKED", rights)                                    # the modern-author quote
        self.assertIn("NEEDS EVIDENCE", rights)                             # music without plan proof
        old = json.loads(first["analysis.json"])["old_report_check"]
        self.assertEqual((old["scored_rows"], old["unique_ids"], old["duplicate_ids"], old["off_topic_rows"]), (3, 2, 1, 1))

    def test_a_bad_ledger_entry_stops_the_build(self):
        (self.run.knowledge / "claims.json").write_text(json.dumps({"claims": [
            {"id": "C9", "claim": "x", "status": "MEASURED", "confidence": "HIGH", "source": "", "date": "", "limitations": "l"}]}))
        with self.assertRaises(ValueError):
            P.build(self.run)

    def test_a_template_placeholder_without_data_is_an_error(self):
        with self.assertRaises(KeyError):
            P.render("{{no_such_table}}", {})

    def test_raw_files_are_never_overwritten(self):
        p = self.run.raw / "x.json"
        self.assertTrue(P.write_once(p, {"a": 1}))
        self.assertFalse(P.write_once(p, {"a": 2}))
        self.assertEqual(json.loads(p.read_text()), {"a": 1})

    def test_failed_channel_reads_are_recorded_and_the_rest_continue(self):
        from platforms import youtube as YT
        (self.run.raw / "validation" / "fresh_month.json").write_text(json.dumps({"lofi|q": {"entries": [
            {"id": "1", "title": "Samurai Lofi beats", "channel_id": "UCbad", "views": 1},
            {"id": "2", "title": "Samurai Lofi beats", "channel_id": "UCgood", "views": 1}]}}))

        def fake(cid, limit):
            if cid == "UCbad":
                raise YT.YtDlpError("This channel does not exist")
            return {"channel_id": cid, "channel": "Good", "subs": 10, "entries": [entry(1, 5, 20)]}
        with mock.patch.object(YT, "channel_page", side_effect=fake):
            P.sample(self.run)
            P.sample(self.run)                                               # a rerun doesn't retry the failure
        self.assertIn("UCbad", json.loads((self.run.raw / "failed.json").read_text()))
        self.assertTrue((self.run.raw / "channel_pages" / "UCgood.json").exists())


if __name__ == "__main__":
    unittest.main()
