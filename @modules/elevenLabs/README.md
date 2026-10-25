# ElevenLabs Music module

Generates music from prompts, downloads the MP3s, verifies them, and cleans verified tracks from
ElevenLabs Music history, by driving your signed-in Chrome through the ai-web-auto platform.

| Order | Command | Safety |
|---|---|---|
| Inspect the page | `python elevenlabs_generator.py --check` | read-only |
| Generate | `python elevenlabs_generator.py --prompts-file promps.txt` | spends credits; Generate is never retried |
| Download + verify | `python elevenlabs_downloader.py` (`simulate_run.bat`) or `--keep` | nothing deleted |
| Download, verify, clean | `python elevenlabs_downloader.py --force-delete` (`run_cleaner.bat`) | deletes only verified tracks |

Each command starts with a **preflight**: AI Web-Auto connected, ElevenLabs tab open and in front,
history page loaded (reloaded once if needed), signed in, and - for generating - no track still
generating. Run the preflight alone with `python ../module_kit.py preflight elevenLabs --action generate_track`.

## Knowledge

| File | Holds |
|---|---|
| [module.json](module.json) | URLs, origins, settings (model `Music v2.5`, 10 KB minimum, ±2 s duration) |
| [knowledge/map.json](knowledge/map.json) | history and project pages, ready/busy signals |
| [knowledge/locators.json](knowledge/locators.json) | every selector and UI text the scripts use |
| [knowledge/actions.json](knowledge/actions.json) | list, generate, download, delete: steps, evidence, safety |
| [knowledge/rules.md](knowledge/rules.md) | when to download, verify, and delete |
| [knowledge/lessons.md](knowledge/lessons.md) | what was learned about this app |

When ElevenLabs changes its UI: open the history page, run
`python ../module_kit.py verify elevenLabs`, fix the missing selectors in `locators.json`, and run
`verify --write` again. The scripts pick up the change; no code edit is needed.

`find_tracks.py` is an older read-only probe; `python ../module_kit.py discover elevenLabs` replaces it.
Tests: `tests/test_elevenlabs_delete.py`, `tests/test_elevenlabs_generator.py` (project root).
