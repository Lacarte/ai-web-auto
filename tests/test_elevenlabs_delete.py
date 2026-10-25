import asyncio
from datetime import datetime
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys


MODULE_PATH = Path(__file__).resolve().parents[1] / "@modules" / "elevenLabs" / "elevenlabs_downloader.py"
spec = importlib.util.spec_from_file_location("elevenlabs_downloader", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {
    "ai_web_auto_backend.automation_controller": SimpleNamespace(BrowserAgent=object),
}):
    spec.loader.exec_module(module)


class FakeServer:
    def __init__(self, click_failure=False):
        self.actions = []
        self.click_failure = click_failure

    async def send_command(self, command):
        self.actions.append(command.action.value)
        if command.action.value == "navigate":
            return SimpleNamespace(status="success", error=None)
        if command.action.value == "click_cdp":
            status = "failure" if self.click_failure else "success"
            return SimpleNamespace(status=status, error="CDP failed" if self.click_failure else None)
        script = command.params["script"]
        if "const li =" in script:
            result = {"x": 10, "y": 20, "title": "Track", "triggerId": "more-1", "count": 2}
        elif "const menu =" in script:
            result = {"x": 30, "y": 40}
        elif "const dialogs =" in script:
            result = {"x": 50, "y": 60}
        elif "targetPresent:" in script:
            result = {"count": 1, "targetPresent": False}
        else:
            result = True
        return SimpleNamespace(
            status="success", error=None,
            data=SimpleNamespace(extracted_data={"result": result}),
        )


class AcknowledgedButLostClickServer(FakeServer):
    def __init__(self):
        super().__init__()
        self.more_open = False
        self.dialog_open = False
        self.deleted = False

    async def send_command(self, command):
        if command.action.value != "execute_script":
            return await super().send_command(command)
        script = command.params["script"]
        if "const li =" in script:
            result = {"x": 10, "y": 20, "title": "Track", "triggerId": "more-1", "count": 2}
        elif "const rows =" in script and "onPointerDown" in script:
            self.more_open = True
            result = True
        elif "const menu =" in script:
            result = {"x": 30, "y": 40} if self.more_open else None
        elif "const menus =" in script and "onClick" in script:
            self.dialog_open = True
            result = True
        elif "const dialogs =" in script and "const buttons =" in script:
            self.deleted = True
            result = True
        elif "const dialogs =" in script:
            result = {"x": 50, "y": 60} if self.dialog_open else None
        elif "targetPresent:" in script:
            result = {"count": 1, "targetPresent": not self.deleted}
        elif "!" in script and "location.pathname" in script:
            result = self.deleted
        else:
            result = True
        self.actions.append(command.action.value)
        return SimpleNamespace(status="success", error=None,
                               data=SimpleNamespace(extracted_data={"result": result}))


class LostDownloadClickServer:
    def __init__(self):
        self.more_open = False
        self.mp3_selected = False
        self.cdp_clicks = 0

    async def execute_script(self, script):
        if "const row =" in script:
            result = {"x": 10, "y": 20, "triggerId": "download-1"}
        elif "const rows =" in script and "onPointerDown" in script:
            self.more_open = True
            result = True
        elif "const menu =" in script:
            result = {"x": 30, "y": 40} if self.more_open and not self.mp3_selected else None
        elif "const menus =" in script and "onClick" in script:
            self.mp3_selected = True
            result = True
        else:
            result = None
        return SimpleNamespace(status="success", error=None,
                               data=SimpleNamespace(extracted_data={"result": result}))

    async def click_cdp(self, x, y):
        self.cdp_clicks += 1
        return SimpleNamespace(status="success", error=None)


class TestElevenLabsDelete(unittest.TestCase):
    def test_download_batch_uses_next_daily_session_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "2026-10-23-session1").mkdir()
            (base / "2026-10-23-session3").mkdir()
            (base / "2026-10-22-session8").mkdir()
            with patch.object(module, "datetime") as clock:
                clock.now.return_value = datetime(2026, 10, 23)
                first = module.get_session_download_dir(base)
                second = module.get_session_download_dir(base)
            self.assertEqual(first.name, "2026-10-23-session4")
            self.assertEqual(second.name, "2026-10-23-session5")
            self.assertTrue(first.is_dir())
            self.assertTrue(second.is_dir())

    def test_verified_track_requires_three_cdp_clicks_and_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "track.mp3"
            path.write_bytes(b"ID3" + b"\0" * 11000)
            server = FakeServer()
            cleaner = module.ElevenLabsCleaner(SimpleNamespace(server=server), Path(tmp))

            self.assertTrue(asyncio.run(cleaner.delete_track_item(1, path, "project-1", "Track")))
            self.assertEqual(server.actions.count("click_cdp"), 3)
            self.assertEqual(server.actions.count("navigate"), 1)
            self.assertEqual(server.actions[-1], "execute_script")

    def test_unverified_file_never_clicks(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = FakeServer()
            cleaner = module.ElevenLabsCleaner(SimpleNamespace(server=server), Path(tmp))

            self.assertFalse(asyncio.run(cleaner.delete_track_item(0, Path(tmp) / "missing.mp3", "project-1", "Track")))
            self.assertEqual(server.actions, [])

    def test_acknowledged_but_lost_cdp_click_uses_scoped_react_handlers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "track.mp3"
            path.write_bytes(b"ID3" + b"\0" * 11000)
            server = AcknowledgedButLostClickServer()
            cleaner = module.ElevenLabsCleaner(SimpleNamespace(server=server), Path(tmp))

            self.assertTrue(asyncio.run(cleaner.delete_track_item(0, path, "project-1", "Track")))
            self.assertTrue(cleaner._cdp_input_broken)
            self.assertTrue(server.deleted)
            self.assertEqual(server.actions.count("click_cdp"), 1)
            self.assertEqual(server.actions.count("navigate"), 1)

    def test_missing_local_file_download_uses_fallback_when_cdp_click_is_lost(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = LostDownloadClickServer()
            cleaner = module.ElevenLabsCleaner(SimpleNamespace(server=server), Path(tmp))

            self.assertTrue(asyncio.run(cleaner.trigger_download_click(0, "project-1")))
            self.assertEqual(server.cdp_clicks, 1)
            self.assertTrue(server.mp3_selected)
            self.assertTrue(cleaner._cdp_input_broken)

    def test_cdp_failure_stops_before_menu(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "track.mp3"
            path.write_bytes(b"ID3" + b"\0" * 11000)
            server = FakeServer(click_failure=True)
            cleaner = module.ElevenLabsCleaner(SimpleNamespace(server=server), Path(tmp))

            self.assertFalse(asyncio.run(cleaner.delete_track_item(0, path, "project-1", "Track")))
            self.assertEqual(server.actions.count("click_cdp"), 1)

    def test_mismatched_audio_file_never_opens_delete_menu(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "other.mp3"
            path.write_bytes(b"ID3" + b"\0" * 11000)
            server = FakeServer()
            cleaner = module.ElevenLabsCleaner(SimpleNamespace(server=server), Path(tmp))

            self.assertFalse(asyncio.run(cleaner.delete_track_item(0, path, "project-1", "Track")))
            self.assertEqual(server.actions, [])


if __name__ == "__main__":
    unittest.main()
