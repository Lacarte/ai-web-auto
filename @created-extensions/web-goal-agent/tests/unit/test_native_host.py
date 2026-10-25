"""Native host lifecycle with real processes: start, detect, no duplicates, ownership, conflicts."""

from __future__ import annotations

import concurrent.futures
import http.server
import json
import os
import struct
import subprocess
import sys
import threading
import time
import unittest

from helpers import BACKEND, PYTHON, free_port, tempdir

HOST = BACKEND / "native_host" / "host.py"
EXT = "chrome-extension://lfejjmliceaahhcnbdlmafhklbehnpab/"


class NativeHostCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempdir()
        self.port = free_port()
        self.config = self.tmp / "config.json"
        self.config.write_text(json.dumps({"port": self.port, "token": "host-test-token", "goals_dir": str(self.tmp / "goals"),
                                           "runs_dir": str(self.tmp / "runs"), "state_dir": str(self.tmp / "state")}))
        self.env = {**os.environ, "WGA_CONFIG": str(self.config)}

    def tearDown(self):
        self.call({"cmd": "stop", "force": True})

    def call(self, msg: dict, caller: str = EXT) -> dict:
        data = json.dumps(msg).encode()
        p = subprocess.run([str(PYTHON), str(HOST), caller], input=struct.pack("<I", len(data)) + data,
                           capture_output=True, env=self.env, timeout=60)
        (n,) = struct.unpack("<I", p.stdout[:4])
        return json.loads(p.stdout[4:4 + n])

    def test_full_lifecycle(self):
        self.assertEqual(self.call({"cmd": "status"})["status"], "down")
        started = self.call({"cmd": "ensure"})
        self.assertTrue(started["ok"], started)
        self.assertEqual(started["status"], "started")
        self.assertEqual((started["port"], started["token"]), (self.port, "host-test-token"))
        pid = started["backend"]["pid"]
        again = self.call({"cmd": "ensure"})
        self.assertEqual((again["status"], again["backend"]["pid"]), ("already_running", pid))
        stopped = self.call({"cmd": "stop"})
        self.assertEqual((stopped["status"], stopped["forced"]), ("stopped", False))
        self.assertEqual(self.call({"cmd": "status"})["status"], "down")

    def test_concurrent_ensure_starts_exactly_one_backend(self):
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            replies = list(pool.map(lambda _: self.call({"cmd": "ensure"}), range(4)))
        self.assertTrue(all(r["ok"] for r in replies), replies)
        self.assertEqual(len({r["backend"]["pid"] for r in replies}), 1)
        self.assertEqual(sum(r["status"] == "started" for r in replies), 1)

    def test_manual_backend_is_detected_and_not_stopped_without_force(self):
        proc = subprocess.Popen([str(PYTHON), "-m", "goal_agent_backend", "--started-by", "manual", "--config", str(self.config)],
                                cwd=str(BACKEND), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                if self.call({"cmd": "status"})["running"]:
                    break
                time.sleep(0.1)
            ensure = self.call({"cmd": "ensure"})
            self.assertEqual((ensure["status"], ensure["backend"]["started_by"]), ("already_running", "manual"))
            refused = self.call({"cmd": "stop"})
            self.assertEqual(refused["status"], "not_owned")
            self.assertEqual(self.call({"cmd": "restart"})["status"], "restart_refused")
            forced = self.call({"cmd": "stop", "force": True})
            self.assertEqual(forced["status"], "stopped")
            proc.wait(10)
        finally:
            proc.kill()

    def test_port_taken_by_another_program_is_reported(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(404)
                self.end_headers()

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", self.port), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            reply = self.call({"cmd": "ensure"})
            self.assertFalse(reply["ok"])
            self.assertEqual(reply["status"], "port_conflict")
        finally:
            srv.shutdown()

    def test_backend_startup_failure_is_reported_with_log_tail(self):
        blocker = self.tmp / "runs-is-a-file"
        blocker.write_text("x")
        self.config.write_text(json.dumps({**json.loads(self.config.read_text()), "runs_dir": str(blocker)}))
        started = time.monotonic()
        reply = self.call({"cmd": "ensure"})
        self.assertFalse(reply["ok"])
        self.assertEqual(reply["status"], "start_failed")
        self.assertIn("exited during startup", reply["error"])
        self.assertIn("FileExistsError", reply["log_tail"])
        self.assertLess(time.monotonic() - started, 15, "crash detected from the process exit, not a timeout")

    def test_unlisted_caller_is_refused(self):
        reply = self.call({"cmd": "ensure"}, caller="chrome-extension://someotherextensionidxxxxxxxxxxxx/")
        self.assertFalse(reply["ok"])
        self.assertIn("not an allowed extension", reply["error"])


if __name__ == "__main__":
    unittest.main()
