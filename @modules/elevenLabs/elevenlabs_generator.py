"""Sequential ElevenLabs Music generation through the connected browser tab.

Examples:
    python elevenlabs_generator.py --check
    python elevenlabs_generator.py --prompts-file prompts.txt
    python elevenlabs_generator.py --prompt "Instrumental piano at dawn"

The runner stops on an uncertain submission or incomplete generation. It never
retries Generate automatically, since a second click could spend credits twice.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from typing import Any

from loguru import logger

ROOT_DIR = Path(__file__).resolve().parents[2]
MODULES_DIR = Path(__file__).resolve().parents[1]
for _path in (ROOT_DIR, MODULES_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from ai_web_auto_backend.automation_controller import BrowserAgent
from ai_web_auto_backend.core.models import Command
import module_kit

# Selectors and UI texts come from knowledge/locators.json (@@<key>@@ / @@<key>.text@@).
KNOWLEDGE = module_kit.load(Path(__file__).parent)
js = KNOWLEDGE.js
HISTORY_URL = KNOWLEDGE.page("history")["url"]
MODEL = KNOWLEDGE.settings["music_model"]
DEFAULT_REPORT = Path(__file__).parent / KNOWLEDGE.settings.get("downloads_dir", "@downloads") / "generation_report.json"


class GenerationError(RuntimeError):
    pass


class MusicPromptRunner:
    def __init__(self, agent: BrowserAgent):
        self.agent = agent

    async def evaluate(self, script: str) -> Any:
        response = await self.agent.server.send_command(
            Command(action="execute_script", params={"script": script})
        )
        if response.status != "success":
            raise GenerationError(response.error or "Browser script failed")
        result = response.data.extracted_data.get("result")
        if isinstance(result, dict) and "_error" in result:
            raise GenerationError(str(result["_error"]))
        return result

    async def snapshot(self) -> dict[str, Any]:
        raw = await self.evaluate(js(r"""(() => {
            const model = document.querySelector(@@music_model_button@@);
            const lyrics = document.querySelector(@@lyrics_switch@@);
            const editor = document.querySelector(@@prompt_editor@@);
            const generate = document.querySelector(@@generate_button@@);
            const rows = [...document.querySelectorAll(@@track_row@@)]
                .map(row => {
                    const link = row.querySelector(@@track_link@@);
                    const id = link && new URL(link.href).pathname.split('/').filter(Boolean).at(-1);
                    const duration = [...row.querySelectorAll(@@track_text@@)]
                        .map(el => el.textContent.trim()).find(s => /^\d+:\d+$/.test(s)) || '';
                    return {
                        project_id: id || '',
                        title: row.querySelector(@@track_title@@)?.textContent?.trim() || '',
                        duration,
                        ready: !!row.querySelector(@@download_button_enabled@@) &&
                            !!row.querySelector(@@more_button_enabled@@) &&
                            !row.querySelector(@@track_busy@@)
                    };
                });
            return JSON.stringify({
                url: location.href,
                model: model?.innerText?.trim() || '',
                model_control: !!model?.querySelector(@@music_model_tooltip@@),
                lyrics_checked: lyrics?.getAttribute('aria-checked') ?? null,
                prompt: editor?.querySelector(@@prompt_region@@)?.innerText?.trim() || '',
                editor_ready: !!editor?.editor?.commands?.setContent,
                generate_present: !!generate,
                generate_loading: generate?.getAttribute('data-loading') === 'true',
                generate_disabled: !!generate?.disabled,
                rows
            });
        })()"""))
        if not isinstance(raw, str):
            raise GenerationError("Could not inspect the music page")
        state = json.loads(raw)
        if state.get("url", "").split("?")[0] != HISTORY_URL:
            raise GenerationError(f"Unexpected page: {state.get('url')}")
        return state

    async def wait_until(self, predicate, label: str, timeout: float = 8.0) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            state = await self.snapshot()
            if predicate(state):
                return state
            await asyncio.sleep(0.25)
        raise GenerationError(f"Timed out waiting for {label}")

    async def ensure_model(self) -> None:
        state = await self.snapshot()
        if not state["model_control"]:
            raise GenerationError("Music model control was not found")
        if state["model"] == MODEL:
            return
        opened = await self.evaluate(js(r"""(() => {
            const button = document.querySelector(@@music_model_button@@);
            if (!button?.querySelector(@@music_model_tooltip@@)) return false;
            const key = Object.keys(button).find(k => k.startsWith('__reactProps$'));
            if (!key || typeof button[key].onPointerDown !== 'function') return false;
            const event = new PointerEvent('pointerdown',
                {bubbles:true, cancelable:true, pointerType:'mouse', button:0, isPrimary:true});
            Object.defineProperty(event, 'currentTarget', {value:button});
            Object.defineProperty(event, 'target', {value:button});
            button[key].onPointerDown(event);
            return true;
        })()"""))
        if opened is not True:
            raise GenerationError("Could not open Music model menu")
        deadline = asyncio.get_running_loop().time() + 5.0
        while asyncio.get_running_loop().time() < deadline:
            # The option's text starts with the model name, then whitespace or nothing (same as /^name(?:\s|$)/).
            selected = await self.evaluate(js(r"""((model) => {
                const options = [...document.querySelectorAll(@@model_option@@)]
                    .filter(el => { const t = el.innerText.trim();
                        return t.startsWith(model) && (t.length === model.length || /\s/.test(t[model.length])); });
                if (options.length !== 1) return false;
                const option = options[0];
                const key = Object.keys(option).find(k => k.startsWith('__reactProps$'));
                if (!key || typeof option[key].onClick !== 'function') return false;
                const event = new MouseEvent('click', {bubbles:true, cancelable:true, button:0});
                Object.defineProperty(event, 'currentTarget', {value:option});
                Object.defineProperty(event, 'target', {value:option});
                option[key].onClick(event);
                return true;
            })(""") + json.dumps(MODEL) + ")")
            if selected is True:
                break
            await asyncio.sleep(0.2)
        await self.wait_until(lambda s: s["model"] == MODEL, f"{MODEL} selection")

    async def ensure_lyrics_off(self) -> None:
        state = await self.snapshot()
        if state["lyrics_checked"] == "false":
            return
        if state["lyrics_checked"] != "true":
            raise GenerationError("Lyrics switch was not found")
        changed = await self.evaluate(js(r"""(() => {
            const button = document.querySelector(@@lyrics_switch@@);
            if (button?.getAttribute('aria-checked') !== 'true') return false;
            const key = Object.keys(button).find(k => k.startsWith('__reactProps$'));
            if (!key || typeof button[key].onClick !== 'function') return false;
            const event = new MouseEvent('click', {bubbles:true, cancelable:true, button:0});
            Object.defineProperty(event, 'currentTarget', {value:button});
            Object.defineProperty(event, 'target', {value:button});
            button[key].onClick(event);
            return true;
        })()"""))
        if changed is not True:
            raise GenerationError("Could not turn lyrics off")
        await self.wait_until(lambda s: s["lyrics_checked"] == "false", "lyrics off")

    async def set_prompt(self, prompt: str) -> None:
        if not prompt.strip():
            raise GenerationError("Prompt is empty")
        result = await self.evaluate(js(r"""((prompt) => {
            const element = document.querySelector(@@prompt_editor@@);
            const editor = element?.editor;
            const doc = editor?.getJSON?.();
            if (!doc || !editor?.commands?.setContent ||
                doc.content?.filter(n => n.type === 'promptRegion').length !== 1 ||
                doc.content?.filter(n => n.type === 'lyricsRegion').length !== 1) return false;
            const next = {type:'doc', content:[
                {type:'promptRegion', content:[{type:'paragraph', content:[{type:'text', text:prompt}]}]},
                {type:'lyricsRegion', content:[{type:'paragraph'}]}
            ]};
            return editor.commands.setContent(next);
        })(""") + json.dumps(prompt) + ")")
        if result is not True:
            raise GenerationError("Tiptap rejected the prompt")
        await self.wait_until(lambda s: s["prompt"] == prompt.strip(), "prompt in editor")

    async def submit(self, prompt: str) -> None:
        state = await self.snapshot()
        if (state["model"] != MODEL or state["lyrics_checked"] != "false" or
                state["prompt"] != prompt.strip() or state["generate_loading"] or
                state["generate_disabled"] or not state["generate_present"]):
            raise GenerationError("Pre-submit model, lyrics, prompt, or Generate check failed")
        clicked = await self.evaluate(js(r"""(() => {
            const button = document.querySelector(@@generate_button@@);
            if (!button || button.disabled || button.getAttribute('data-loading') === 'true') return false;
            const key = Object.keys(button).find(k => k.startsWith('__reactProps$'));
            if (!key || typeof button[key].onClick !== 'function') return false;
            const event = new MouseEvent('click', {bubbles:true, cancelable:true, button:0});
            Object.defineProperty(event, 'currentTarget', {value:button});
            Object.defineProperty(event, 'target', {value:button});
            button[key].onClick(event);
            return true;
        })()"""))
        if clicked is not True:
            raise GenerationError("Generate handler was unavailable")

    async def wait_for_audio(self, before_ids: set[str], expected_tracks: int, timeout: float) -> list[dict[str, Any]]:
        # ElevenLabs can publish the completed project just after the nominal
        # generation limit; keep observing briefly without resubmitting.
        deadline = asyncio.get_running_loop().time() + timeout + 45.0
        stable_since: float | None = None
        previous_ids: set[str] = set()
        while asyncio.get_running_loop().time() < deadline:
            state = await self.snapshot()
            new_rows = [r for r in state["rows"] if r["project_id"] and r["project_id"] not in before_ids]
            ids = {r["project_id"] for r in new_rows}
            complete = (len(ids) >= expected_tracks and all(
                r["ready"] and r["title"] and r["duration"] for r in new_rows
            ) and not state["generate_loading"])
            if complete:
                if ids != previous_ids:
                    stable_since = asyncio.get_running_loop().time()
                elif stable_since is not None and asyncio.get_running_loop().time() - stable_since >= 8.0:
                    return new_rows
            else:
                stable_since = None
            previous_ids = ids
            await asyncio.sleep(2.0)
        raise GenerationError(
            f"Audio not verified ready within {timeout + 45:g}s; do not submit another prompt"
        )

    async def run(self, prompts: list[str], expected_tracks: int, timeout: float,
                  report_path: Path) -> dict[str, Any]:
        report: dict[str, Any] = {"started_at": datetime.now().isoformat(), "results": []}
        report_path.parent.mkdir(parents=True, exist_ok=True)
        for number, prompt in enumerate(prompts, 1):
            item: dict[str, Any] = {"number": number, "prompt": prompt, "status": "preparing"}
            report["results"].append(item)
            try:
                state = await self.snapshot()
                if not state["editor_ready"]:
                    raise GenerationError("Music prompt editor is unavailable")
                if any(not row["ready"] for row in state["rows"]):
                    raise GenerationError("An existing track is still generating")
                before_ids = {row["project_id"] for row in state["rows"] if row["project_id"]}
                await self.ensure_model()
                await self.ensure_lyrics_off()
                await self.set_prompt(prompt)
                item["status"] = "submitting"
                report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
                await self.submit(prompt)
                item["status"] = "waiting_for_audio"
                report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
                tracks = await self.wait_for_audio(before_ids, expected_tracks, timeout)
                item.update(status="audio_ready", tracks=tracks)
                logger.info(f"Prompt {number}/{len(prompts)}: {len(tracks)} new audio tracks ready")
            except Exception as exc:
                item.update(status="stopped", error=str(exc))
                logger.error(f"Stopped at prompt {number}: {exc}")
                break
            finally:
                report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        report["finished_at"] = datetime.now().isoformat()
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report


def read_prompts(path: Path) -> list[str]:
    content = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        value = json.loads(content)
        if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
            raise ValueError("Prompt JSON must be an array of strings")
        return [p.strip() for p in value if p.strip()]
    if re.search(r"(?m)^\[prompt\s+\d+\]\s*$", content, flags=re.IGNORECASE):
        blocks = re.split(r"(?mi)^\[prompt\s+\d+\]\s*$", content)
        if blocks[0].strip():
            raise ValueError("Unexpected text before the first [prompt N] marker")
        return [block.strip() for block in blocks[1:] if block.strip()]
    return [line.strip() for line in content.splitlines() if line.strip() and not line.lstrip().startswith("#")]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Generate ElevenLabs Music prompts one at a time")
    parser.add_argument("--check", action="store_true", help="Inspect current model, lyrics switch, and editor")
    parser.add_argument("--prompt", action="append", default=[], help="Prompt text; may be repeated")
    parser.add_argument("--prompts-file", type=Path, help="UTF-8 text (one prompt per line) or JSON array")
    parser.add_argument("--expected-tracks", type=int, default=2, help="New ready tracks expected per prompt")
    parser.add_argument("--max-wait", type=float, default=900, help="Maximum seconds for each generation, plus a 45 second reconciliation window")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    prompts = [p.strip() for p in args.prompt if p.strip()]
    if args.prompts_file:
        prompts.extend(read_prompts(args.prompts_file))
    if not args.check and not prompts:
        parser.error("Provide --prompt or --prompts-file")
    if args.expected_tracks < 1 or args.max_wait <= 0:
        parser.error("--expected-tracks and --max-wait must be positive")

    agent = BrowserAgent()
    await agent.connect(timeout=25)
    try:
        # Preflight: app tab, history page ready, signed in; generating also waits for the app to be idle.
        preflight = await module_kit.preflight(
            KNOWLEDGE, agent.server, None if args.check else "generate_track", page="history",
            idle_timeout=args.max_wait)
        print(preflight.summary())
        if not preflight.ok:
            raise SystemExit(1)
        runner = MusicPromptRunner(agent)
        if args.check:
            print(json.dumps(await runner.snapshot(), indent=2))
            return
        report = await runner.run(prompts, args.expected_tracks, args.max_wait, args.report)
        print(json.dumps(report, indent=2))
        if len(report["results"]) != len(prompts) or any(
            item["status"] != "audio_ready" for item in report["results"]
        ):
            raise SystemExit(1)
    finally:
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
