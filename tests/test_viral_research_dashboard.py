"""viral_research dashboard: status from events, older-run fallback, and the HTTP server (incl. a real Stop)."""

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "@modules" / "viral_research"
sys.path.insert(0, str(MODULE))
import dashboard as D  # noqa: E402

T0 = 1_800_000_000.0


def ev(t, event, **data):
    return {"t": T0 + t, "run": data.pop("run", "r1"), "kind": event, **data}


def started(*more):
    return [ev(0, "run_start", pid=4242), ev(1, "stage_start", stage="discover"), ev(20, "stage_end", stage="discover"),
            ev(21, "stage_start", stage="baseline"), *more]


class TestReduceEvents(unittest.TestCase):
    def test_running_with_progress_and_time_left(self):
        s = D.reduce_events(started(ev(30, "progress", stage="baseline", label="Channel tabs read", done=10, total=110),
                                    ev(80, "progress", stage="baseline", label="Channel tabs read", done=60, total=110)),
                            now=T0 + 85, alive=True)
        self.assertEqual(s["status"], "running")
        self.assertEqual([g["status"] for g in s["stages"][:3]], ["done", "running", "pending"])
        self.assertEqual(s["stages"][0]["seconds"], 19)
        self.assertEqual((s["task"]["done"], s["task"]["total"], s["task"]["pct"]), (60, 110, 55))
        self.assertEqual(s["task"]["eta"], 50)      # 50 left at 1 per second

    def test_alive_without_progress_for_five_minutes_is_stalled(self):
        s = D.reduce_events(started(ev(30, "progress", stage="baseline", label="x", done=1, total=9),
                                    ev(31, "error", category="rate_limit", message="HTTP Error 429")),
                            now=T0 + 30 + D.STALL_SECONDS + 5, alive=True)
        self.assertEqual(s["status"], "stalled")
        self.assertEqual(s["errors"]["by_kind"], {"rate_limit": 1})
        self.assertEqual(s["errors"]["recent_rate_limits"], 1)

    def test_heartbeats_alone_do_not_count_as_progress(self):
        beats = [ev(30 + 15 * i, "heartbeat") for i in range(30)]
        s = D.reduce_events(started(*beats), now=T0 + 480, alive=True)
        self.assertEqual(s["status"], "stalled")

    def test_process_gone_without_an_ending_is_crashed(self):
        s = D.reduce_events(started(), now=T0 + 60, alive=False)
        self.assertEqual(s["status"], "crashed")
        self.assertEqual(s["stages"][1]["status"], "failed")
        self.assertIsNone(s["task"])

    def test_finished_failed_stopped(self):
        done = D.reduce_events(started(ev(50, "stage_end", stage="baseline"), ev(51, "run_end", status="finished")), now=T0 + 99, alive=False)
        self.assertEqual((done["status"], done["elapsed"]), ("finished", 51))
        failed = D.reduce_events(started(ev(50, "run_end", status="failed", error="RuntimeError: boom")), now=T0 + 99, alive=False)
        self.assertEqual(failed["status"], "failed")
        ctrl_c = D.reduce_events(started(ev(50, "run_end", status="failed", error="KeyboardInterrupt: ")), now=T0 + 99, alive=False)
        self.assertEqual(ctrl_c["status"], "stopped")
        stopped = D.reduce_events(started(ev(50, "stopped", by="dashboard")), now=T0 + 99, alive=False)
        self.assertEqual(stopped["status"], "stopped")

    def test_youtube_bot_check_pause_shows_as_waiting_not_stalled(self):
        s = D.reduce_events(started(ev(30, "blocked", until=T0 + 30 + 1200, reason="not a bot")), now=T0 + 30 + D.STALL_SECONDS + 60, alive=True)
        self.assertEqual((s["status"], s["blocked_until"]), ("waiting", T0 + 1230))
        self.assertEqual(s["stages"][1]["status"], "running")

    def test_blocked_and_incomplete_endings(self):
        b = D.reduce_events(started(ev(50, "run_end", status="blocked", error="YouTube kept asking")), now=T0 + 99, alive=False)
        i = D.reduce_events(started(ev(50, "run_end", status="incomplete", error="only 3 of 69")), now=T0 + 99, alive=False)
        self.assertEqual((b["status"], i["status"], i["end_reason"]), ("blocked", "incomplete", "only 3 of 69"))

    def test_only_the_latest_run_counts(self):
        events = started(ev(50, "run_end", status="failed", error="x")) + [ev(100, "run_start", pid=7, run="r2"),
                                                                          ev(101, "stage_start", stage="discover", run="r2")]
        s = D.reduce_events(events, now=T0 + 110, alive=True)
        self.assertEqual((s["status"], s["pid"], s["stages"][0]["status"]), ("running", 7, "running"))

    def test_events_written_by_the_pipeline_helpers(self):
        import viral_research as VR
        d = Path(tempfile.mkdtemp())
        try:
            VR.EVENTS_FILE, VR.RUN_ID, VR.STAGE = d / "events.jsonl", "t1", None
            VR.LOG_FILE = d / "log.txt"
            VR.emit("run_start", pid=1)
            VR.stage("discover", "1. discover")
            VR.parallel(lambda x: x, range(5), workers=2, label="Searches (en)")
            VR.warn("    abc: ERROR: HTTP Error 429: Too Many Requests")
            VR.warn("    def: Video unavailable")
            s = D.reduce_events(D.read_events(d / "events.jsonl"), now=time.time(), alive=True)
            self.assertEqual(s["status"], "running")
            self.assertEqual((s["task"]["label"], s["task"]["done"], s["task"]["total"]), ("Searches (en)", 5, 5))
            self.assertEqual(s["errors"]["by_kind"], {"rate_limit": 1, "unavailable": 1})
        finally:
            VR.EVENTS_FILE = VR.LOG_FILE = None
            shutil.rmtree(d, ignore_errors=True)


class TestOlderRuns(unittest.TestCase):
    def test_log_times_are_safe_across_midnight(self):
        lines = ["23:59:50 run folder: x", "23:59:55 1. discover", "00:00:10 2. baseline", "00:01:00   300/745 tabs"]
        t = D.log_times(lines, last_write=T0)
        self.assertEqual([round(x - T0) for x in t], [-70, -65, -50, 0])

    def test_stages_from_the_log(self):
        lines = ["10:00:00 run folder: x", "10:00:01 1. discover", "10:05:00 2. baseline", "10:20:00 3. score"]
        times = D.log_times(lines, last_write=T0)
        running = D.reduce_log(lines, times, now=T0 + 30, alive=True)
        self.assertEqual([g["status"] for g in running["stages"][:4]], ["done", "done", "running", "pending"])
        self.assertEqual(running["stages"][0]["seconds"], 299)
        stopped = D.reduce_log(lines, times, now=T0 + 30, alive=False)
        self.assertEqual((stopped["status"], stopped["stages"][2]["status"]), ("stopped", "stopped"))
        done = D.reduce_log(lines + ["10:30:00 8. pack", r"10:30:01   D:\runs\x\pack.md"], D.log_times(lines + ["10:30:00 8. pack", "10:30:01 p"], T0), now=T0, alive=False)
        self.assertEqual(done["status"], "finished")


class TestExplains(unittest.TestCase):
    """What the dashboard says about the run: processes, the YouTube pause, live facts per stage."""

    def test_processes_by_the_work_they_do(self):
        settings = {"ffmpeg_dir": r"D:\Seamless-app\bin", "vision": {"model": r"C:\AI\models\MiniCPM-V-4_7-1B-Q4_K_M.gguf",
                                                                    "server": r"D:\on-device\llama.cpp\llama-server.exe"}}
        run = r"@modules/viral_research/viral_research.py run --niche ai-music"
        rows = [
            {"ProcessId": 1, "Name": "python.exe", "CommandLine": rf"D:\venv\Scripts\python.exe -u {run}"},
            {"ProcessId": 2, "Name": "python.exe", "CommandLine": rf"C:\Python310\python.exe -u {run}"},   # the shim's child
            {"ProcessId": 3, "Name": "yt-dlp.exe", "CommandLine": r"D:\venv\Scripts\yt-dlp.exe --dump-json x"},
            {"ProcessId": 4, "Name": "ffmpeg.exe", "CommandLine": r"D:\Seamless-app\bin\ffmpeg.exe -i a.mp4"},
            {"ProcessId": 5, "Name": "ffmpeg.exe", "CommandLine": r"C:\other\ffmpeg.exe -i b.mp4"},           # someone else's
            {"ProcessId": 6, "Name": "llama-server.exe", "CommandLine": r"D:\on-device\llama.cpp\llama-server.exe -m C:\AI\models\MiniCPM-V-4_7-1B-Q4_K_M.gguf --port 8099"},
            {"ProcessId": 7, "Name": "llama-server.exe", "CommandLine": r"D:\on-device\llama.cpp\llama-server.exe -m C:\AI\models\other.gguf --port 8080"},
            {"ProcessId": 9, "Name": "llama-server.exe",   # another app serving the same model
             "CommandLine": r"D:\Omnitase-app\llama.cpp\llama-server.exe -m C:\AI\models\MiniCPM-V-4_7-1B-Q4_K_M.gguf --port 8080"},
            {"ProcessId": 8, "Name": "python.exe", "CommandLine": r'"C:\whisper\python.exe" D:\vr\analysis\whisper_worker.py a.wav small en'},
        ]
        got = {p["key"]: (p["count"], p["detail"]) for p in D.classify_processes(rows, settings)}
        self.assertEqual(got, {"pipeline": (1, "1"), "downloads": (1, "3"), "measure": (1, "4"), "whisper": (1, "8"),
                               "vision": (1, "port 8099"), "dashboard": (0, "")})

    def test_youtube_access_from_the_saved_pause(self):
        self.assertEqual(D.gate_info(None, T0, 5400)["state"], "ok")
        paused = D.gate_info({"until": T0 + 600, "since": T0 - 1200, "trips": 3}, T0, 5400)
        self.assertEqual((paused["state"], paused["attempts"], paused["gives_up_after"]), ("paused", 3, T0 + 4200))
        self.assertEqual(D.gate_info({"until": T0 - 5, "since": T0 - 1200, "trips": 3}, T0, 5400)["state"], "retrying")

    def test_facts_count_what_the_run_folder_holds(self):
        d = Path(tempfile.mkdtemp(prefix="vr-facts-"))
        try:
            (d / "discovery.json").write_text(json.dumps({"en": [{"id": "a"}, {"id": "b"}], "es": [{"id": "b"}]}))
            (d / "shortlist.json").write_text(json.dumps({"en-long": {"outliers": [{"id": "a"}], "controls": [{"id": "c"}],
                                                                      "excluded": [{"id": "z"}]}}))
            for vid in ("a", "c"):
                (d / "evidence" / vid).mkdir(parents=True)
                (d / "evidence" / vid / "thumbnail.jpg").write_bytes(b"jpg")
            (d / "evidence" / "a" / "measures.json").write_text("{}")
            from analysis import vision as V
            answers = {"text": "NONE", "face": "yes", "style": "photo", "subject": "a singer"}
            (d / "evidence" / "a" / "vision_thumbnail.json").write_text(json.dumps(
                {**V.labels(*answers.values()), "asked": {k: q for k, (q, _) in V.QUESTIONS.items()}, "answers": answers}))
            f = D.Watcher(d, "test").facts()
            self.assertEqual(f["discover"], "2 videos found (en: 2, es: 1)")
            self.assertTrue(f["shortlist"].startswith("1 outliers + 1 controls in 1 rankings; 1 excluded"))
            self.assertEqual(f["evidence"], "2/2 thumbnails; 1/2 videos measured")
            self.assertTrue(f["vision"].startswith("1/2 images labelled"))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="vr-dash-"))
        (cls.dir / "log.txt").write_text("10:00:00 run folder: x\n10:00:01 1. discover\n", encoding="utf-8")
        (cls.dir / "pack.md").write_text("# pack\n", encoding="utf-8")
        (cls.dir.parent / "vr-dash-secret.txt").write_text("secret", encoding="utf-8")
        cls.server = D.serve(cls.dir, "test-niche", open_browser=False)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        shutil.rmtree(cls.dir, ignore_errors=True)
        (cls.dir.parent / "vr-dash-secret.txt").unlink(missing_ok=True)

    def get(self, path, host=None):
        req = urllib.request.Request(self.base + path, headers={"Host": host} if host else {})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def post_stop(self, token):
        req = urllib.request.Request(self.base + "/api/stop", method="POST", headers={"X-Control-Token": token} if token else {})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_page_and_state(self):
        code, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn(self.server.token.encode(), body)
        code, body = self.get("/api/state")
        state = json.loads(body)
        self.assertEqual((state["niche"], state["files"].get("pack.md")), ("test-niche", "/file?p=pack.md"))

    def test_ping_tells_whether_a_window_is_showing_the_page(self):
        self.server.last_poll = 0.0
        ping = json.loads(self.get("/api/ping")[1])
        self.assertEqual(Path(ping["run_dir"]), self.dir)
        self.assertGreater(ping["idle_seconds"], 10)                      # nobody looking: a run reopens the window
        self.get("/api/state")
        self.assertLessEqual(json.loads(self.get("/api/ping")[1])["idle_seconds"], 1)

    def test_state_explains_the_run(self):
        state = json.loads(self.get("/api/state")[1])
        self.assertTrue(all(s["about"] for s in state["stages"]))
        self.assertEqual([p["key"] for p in state["processes"]], [k for k, _, _ in D.PROCESS_KINDS])
        self.assertEqual(state["gate"]["state"], "ok")
        self.assertIn("pack", state["facts"])

    def test_only_local_hosts_and_only_the_run_folder(self):
        self.assertEqual(self.get("/api/state", host="attacker.example")[0], 403)
        self.assertEqual(self.get("/file?p=pack.md")[0], 200)
        self.assertEqual(self.get("/file?p=../vr-dash-secret.txt")[0], 404)
        self.assertEqual(self.get("/file?p=" + str(self.dir.parent / "vr-dash-secret.txt"))[0], 404)

    def test_stop_needs_the_token_and_really_ends_the_process(self):
        self.assertEqual(self.post_stop(None), 403)
        self.assertEqual(self.post_stop(self.server.token), 409)       # nothing to stop yet
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        try:
            (self.dir / "run.pid").write_text(str(proc.pid))
            (self.dir / "events.jsonl").write_text(json.dumps({"t": time.time(), "run": "r9", "kind": "run_start", "pid": proc.pid}) + "\n")
            state = json.loads(self.get("/api/state")[1])
            self.assertEqual(state["status"], "running")
            self.assertEqual(self.post_stop(self.server.token), 200)
            proc.wait(timeout=20)
            listed = subprocess.run(["tasklist", "/FI", f"PID eq {proc.pid}"], capture_output=True, text=True).stdout
            self.assertNotIn(str(proc.pid), listed)                       # Windows confirms it is gone
            self.assertFalse(D.pid_alive(proc.pid))
            self.assertEqual(json.loads(self.get("/api/state")[1])["status"], "stopped")
        finally:
            if proc.poll() is None:
                proc.kill()


if __name__ == "__main__":
    unittest.main()
