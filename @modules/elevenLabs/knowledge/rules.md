# ElevenLabs Music — decision rules

When to download, when a file counts as verified, and when a track may be deleted. The code
enforces every rule here; if you change one, change the code and this file together.

## Identity

A track is identified by its **project id** (last path segment of its `/app/music/project/` link),
never by its position: positions shift on every rerender and deletion. If two rows share a title or a
project id, leave both online (`ambiguous_identity`).

## When to download

Download a track when all hold:

| Condition | How it is checked |
|---|---|
| The track is listed | its row has a project link and a title (`list_tracks`) |
| Generation finished | `download_button` and `more_button` present, no `track_busy` in the row |
| Not already saved, or a fresh copy is wanted | default re-downloads into the current session folder; `--skip-existing` reuses a verified local copy |

Downloads land in `@downloads/<YYYY-MM-DD>-sessionN/` (a new session folder per run), via Chrome's
download path set over CDP; files that land in `~/Downloads` instead are moved there.

## When a file counts as verified

All must hold — a track is never deleted unless they do:

1. The file exists, and its name starts with the track title (Chrome's ` (1)` suffix is removed).
2. Size ≥ `settings.min_audio_bytes` (10 KB) — guards against empty or partial writes.
3. A valid audio header: `ID3`, an MPEG frame sync (`0xFF 0xE0` mask), `RIFF…WAVE`, or `OggS`.
4. When the online duration is known: `ffprobe` duration within `settings.duration_tolerance_s` (±2 s).
   If `ffprobe` is missing, verification fails (it does not pass silently).

## When a track may be deleted from ElevenLabs

```
listed & ready ─▶ downloaded ─▶ verified (all four) ─▶ --force-delete and not --keep ─▶ DELETE
```

- Deletion happens per track, right after its file verifies, working from the end of the list.
- The title shown in the row must still equal the expected title when the menu is opened.
- A delete counts only when, after reloading history, the project id is absent and the list has loaded.
- Do **not** delete when: the file failed verification; the identity is ambiguous; the confirmation
  dialog did not appear; the row's title changed. An unfinished delete is cancelled and the page reloaded.
- After the run, the history is re-read until it is stable; any "deleted" track still online becomes
  `delete_unverified` in `deletion_report.json`.

## Generating

- Model `settings.music_model` (Music v2.5), lyrics off, one prompt at a time.
- Before clicking Generate: model, lyrics, prompt text, and an enabled, non-loading Generate are re-checked.
- **Generate is never retried.** If anything is uncertain the run stops and reports; a second click
  can spend credits twice.
- A prompt counts as done when `expected_tracks` new ready rows (title + duration) are stable for 8 s.
  Wait up to `--max-wait` + 45 s; the results land in `@downloads/generation_report.json`.

## Run modes

| Command | What happens |
|---|---|
| `python elevenlabs_downloader.py` (`simulate_run.bat`) | Dry run (the default): download + verify, nothing deleted |
| `python elevenlabs_downloader.py --keep` | Download + verify, nothing deleted |
| `python elevenlabs_downloader.py --force-delete` (`run_cleaner.bat`) | Download + verify, then delete verified tracks |
| `--skip-existing` | Reuse verified local copies instead of re-downloading (with `--force-delete`: delete those tracks without re-downloading) |
| `--max-items N` | Process at most N tracks (default 50) |
| `python elevenlabs_generator.py --prompts-file promps.txt` | Generate each prompt in order; `--check` only inspects the page |
