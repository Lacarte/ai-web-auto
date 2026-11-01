"""Deal Watch page (price_watch/webapp/server.py): run logs, status JSON, safe file serving, Run-now guard, chime."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import wave
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "@modules" / "price_watch", ROOT / "@modules" / "price_watch" / "webapp"):
    sys.path.insert(0, str(p))
import server as S  # noqa: E402
import sounds  # noqa: E402

LOG_DONE = """[Fri 10/30/2026 18:00:01.12] build-deals run 2026-10-30-1800
STEP 1/3 price watch: eBay, Amazon, Best Buy, PC Server and Parts
2026-10-30 18:00:05.000 | DEBUG    | ai_web_auto_backend.core.server:send_command:194 - Sent command: list_tabs
price_watch exit code 0
STEP 2/3 config check: selectable eBay listings priced with the 64GB options selected
  WAITING: the eBay tab is hidden - bring the Chrome window with the eBay tab to the front
config_check exit code 0
STEP 3/3 Claude analysis and report
claude exit code {claude}
DONE Fri 10/30/2026 18:41:09.55
"""


class Logs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rep = Path(self.tmp.name)
        mock.patch.object(S, "REPORTS", self.rep).start()
        self.addCleanup(mock.patch.stopall)

    def log(self, ts, text, age_h=0.0):
        p = self.rep / f"run-{ts}.log"
        p.write_text(text, encoding="utf-8")
        t = time.time() - age_h * 3600
        os.utime(p, (t, t))
        return p

    def test_done_ok_warn_running_stopped(self):
        ok = S.parse_log(self.log("2026-10-30-1800", LOG_DONE.format(claude=0)))
        self.assertEqual((ok["status"], ok["step"], ok["exit_codes"]["claude"]), ("ok", 3, 0))
        self.assertEqual(ok["started"], "2026-10-30T18:00")
        self.assertFalse([l for l in ok["tail"] if "DEBUG" in l])  # backend debug noise is hidden
        self.assertTrue(any("WAITING" in n for n in ok["notes"]))
        self.assertEqual(S.parse_log(self.log("2026-10-30-1200", LOG_DONE.format(claude=1)))["status"], "warn")
        part = LOG_DONE.split("STEP 2/3")[0]
        self.assertEqual(S.parse_log(self.log("2026-10-31-0600", part))["status"], "running")
        self.assertEqual(S.parse_log(self.log("2026-10-29-0600", part, age_h=5))["status"], "stopped")

    def test_runs_newest_first_with_report_and_deals(self):
        self.log("2026-10-30-0600", LOG_DONE.format(claude=0))
        self.log("2026-10-30-1200", LOG_DONE.format(claude=0))
        (self.rep / "report-2026-10-30-1200.html").write_text("<html></html>", encoding="utf-8")
        (self.rep / "compare-2026-10-30-1200.json").write_text(json.dumps({"deals": [{"url": "u"}]}), encoding="utf-8")
        rs = S.runs()
        self.assertEqual([r["id"] for r in rs], ["2026-10-30-1200", "2026-10-30-0600"])
        self.assertEqual((rs[0]["report"], rs[0]["deals"], rs[1]["report"], rs[1]["deals"]),
                         ("report-2026-10-30-1200.html", 1, None, None))

    def test_run_now_refused_while_running(self):
        self.log("2026-10-31-0600", LOG_DONE.split("STEP 2/3")[0])
        with mock.patch.object(S.subprocess, "run") as run:
            self.assertEqual(S.start_run()[0], False)
            run.assert_not_called()


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        cls.patches = [mock.patch.object(S, "REPORTS", d / "reports"), mock.patch.object(S, "WATCH_DIR", d / "watch"),
                       mock.patch.object(S, "schedule", lambda: {"state": "Ready", "next": "2026-10-31T06:00:00", "times": ["06:00", "12:00", "18:00"]})]
        for p in cls.patches:
            p.start()
        (d / "reports").mkdir()
        (d / "watch").mkdir()
        (d / "reports" / "report-2026-10-30-1800.html").write_text("<p>report</p>", encoding="utf-8")
        deal = {"kind": "buy", "product": "RTX 5060 Ti 16GB", "store": "ebay", "url": "https://www.ebay.com/itm/1", "title": "t",
                "total": 440.0, "landed_est": 470.8, "deal_price": 450}
        (d / "watch" / "compare.json").write_text(json.dumps({"at": "2026-10-30T18:20:00", "cycle": 1, "deals": [deal],
                                                              "products": [{"product": "RTX 5060 Ti 16GB", "matches": 1, "best": deal,
                                                                            "by_store": {"ebay": deal}, "auctions": []}]}), encoding="utf-8")
        cls.srv = S.ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
        cls.port = cls.srv.server_port
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    def get(self, path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, b""

    def test_page_status_and_files(self):
        self.assertEqual(self.get("/")[0], 200)
        code, body = self.get("/api/status")
        st = json.loads(body)
        self.assertEqual((code, st["opportunities"]["deals"][0]["total"], st["products"][0]["deal_price"]), (200, 440.0, 450))
        self.assertEqual(self.get("/reports/report-2026-10-30-1800.html"), (200, b"<p>report</p>"))
        self.assertEqual(self.get("/fonts/inter-latin-400-normal.woff2")[0], 200)

    def test_no_path_tricks(self):
        for p in ("/reports/..%2F..%2Fwatches%2Fbuild-deals.json", "/reports/../server.py", "/fonts/..%2Fserver.py", "/dashboard/../x"):
            self.assertEqual(self.get(p)[0], 404, p)

    def test_run_now_only_from_this_page(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/run", method="POST", headers={"Origin": "https://evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(e.exception.code, 403)


class NoWindows(unittest.TestCase):
    """2026-10-30: under pythonw every powershell call of the page server opened a console window (once a minute)."""

    def test_child_processes_open_no_window(self):
        S._sched_cache.update(at=0.0, data=None)
        with mock.patch.object(S.subprocess, "run", return_value=mock.Mock(returncode=0, stdout='{"state":"Ready"}', stderr="")) as run, \
                mock.patch.object(S, "runs", return_value=[]):
            S.schedule()
            S.start_run()
        self.assertEqual(run.call_count, 2)
        for c in run.call_args_list:
            self.assertEqual(c.kwargs.get("creationflags"), S.NO_WINDOW)
        self.assertTrue(S.NO_WINDOW)  # CREATE_NO_WINDOW exists on Windows


class Chime(unittest.TestCase):
    def test_chime_is_a_short_valid_wav(self):
        with tempfile.TemporaryDirectory() as t:
            p = sounds.make_chime(Path(t) / "c.wav")
            with wave.open(str(p)) as w:
                self.assertEqual((w.getnchannels(), w.getsampwidth()), (1, 2))
                self.assertTrue(1.0 < w.getnframes() / w.getframerate() < 2.5)


if __name__ == "__main__":
    unittest.main()
