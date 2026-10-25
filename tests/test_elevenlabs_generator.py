import asyncio
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock


MODULE_PATH = Path(__file__).resolve().parents[1] / "@modules" / "elevenLabs" / "elevenlabs_generator.py"
spec = importlib.util.spec_from_file_location("elevenlabs_generator", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TestMusicPromptRunner(unittest.TestCase):
    def test_numbered_multiline_prompt_file_stays_two_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "promps.txt"
            path.write_text("[prompt 1]\nFirst line\n\nSecond line\n\n[prompt 2]\nNext song\n", encoding="utf-8")
            self.assertEqual(module.read_prompts(path), ["First line\n\nSecond line", "Next song"])

    def test_submit_rejects_wrong_model_or_lyrics_before_click(self):
        async def check(model, lyrics):
            runner = module.MusicPromptRunner(SimpleNamespace())
            runner.snapshot = AsyncMock(return_value={
                "model": model, "lyrics_checked": lyrics, "prompt": "Track prompt",
                "generate_loading": False, "generate_disabled": False, "generate_present": True,
            })
            runner.evaluate = AsyncMock(return_value=True)
            with self.assertRaises(module.GenerationError):
                await runner.submit("Track prompt")
            runner.evaluate.assert_not_awaited()

        asyncio.run(check("Music v2", "false"))
        asyncio.run(check("Music v2.5", "true"))


if __name__ == "__main__":
    unittest.main()
