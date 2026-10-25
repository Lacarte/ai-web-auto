import unittest
from ai_web_auto_backend.core.models import Command, ActionType

class TestCommands(unittest.TestCase):
    def test_command_serialization(self):
        cmd = Command(
            action=ActionType.NAVIGATE,
            params={"url": "https://example.com"}
        )
        msg = cmd.to_message()
        self.assertEqual(msg["action"], "navigate")
        self.assertEqual(msg["params"]["url"], "https://example.com")
        self.assertIn("id", msg)

    def test_select_command(self):
        cmd = Command(
            action=ActionType.SELECT,
            params={"selector": "#dropdown", "value": "opt1", "by_text": False}
        )
        msg = cmd.to_message()
        self.assertEqual(msg["action"], "select")
        self.assertEqual(msg["params"]["selector"], "#dropdown")
        self.assertEqual(msg["params"]["value"], "opt1")

    def test_upload_command(self):
        cmd = Command(
            action=ActionType.UPLOAD,
            params={"selector": "input[type='file']", "files": ["C:/test.png"]}
        )
        msg = cmd.to_message()
        self.assertEqual(msg["action"], "upload")
        self.assertEqual(msg["params"]["files"], ["C:/test.png"])

    def test_key_combo_command(self):
        cmd = Command(
            action="key",
            params={"key": "Enter", "ctrl": True, "alt": False}
        )
        msg = cmd.to_message()
        self.assertEqual(msg["action"], "key")
        self.assertTrue(msg["params"]["ctrl"])

if __name__ == "__main__":
    unittest.main()
