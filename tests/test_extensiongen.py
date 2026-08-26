"""Recipe distillation + standalone-extension generation.

Pure-unit tests over temporary directories: they write a synthetic journal, run
the same distiller and generator the `jobs generate` CLI uses, and assert the
recipe is clean and the emitted extension is loadable (valid MV3 manifest, no
BOM, the recipe embedded, each action reachable in the replayer).

No server, no extension, no LLM. Run with the project venv:

    venv\\Scripts\\python.exe -m pytest tests/test_extensiongen.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_web_auto_backend.extensiongen import (
    GenerationError,
    generate,
    slugify,
)
from ai_web_auto_backend.jobs.recipe import build_recipe, load_recipe, save_recipe
from ai_web_auto_backend.jobs.store import JobStore


# --- fixtures without pytest fixtures --------------------------------------


def _journal_job(root: Path, job_id: str, events: list[dict]) -> JobStore:
    """Create a job dir under root with a synthetic journal and return a store."""
    store = JobStore(root / "jobs")
    d = store.dir(job_id)
    (d / "screenshots").mkdir(parents=True, exist_ok=True)
    with open(store.journal_path(job_id), "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return store


# A run that: navigates, types, clicks, hits a failed selector, re-clicks a dup,
# clicks via xpath, screenshots, waits, and finishes.
SAMPLE_EVENTS = [
    {"type": "job_started", "objective": "Search for cats", "start_url": "https://example.com/"},
    {"type": "navigated", "url": "https://example.com/", "ok": True},
    {"type": "action_result", "step": 1, "action": "type", "selector": "input#q", "text": "cats", "clear_first": True, "ok": True},
    {"type": "action_result", "step": 2, "action": "click", "selector": "button.search", "ok": True},
    {"type": "action_result", "step": 3, "action": "click", "selector": ".missing", "ok": False, "error_code": "ELEMENT_NOT_FOUND"},
    {"type": "action_result", "step": 4, "action": "click", "selector": "button.search", "ok": True},
    {"type": "action_result", "step": 5, "action": "click", "selector": "//a[@id='first']", "ok": True},
    {"type": "action_result", "step": 6, "action": "screenshot", "selector": "", "ok": True},
    {"type": "action_result", "step": 7, "action": "wait", "selector": ".results", "ok": True},
    {"type": "action_result", "step": 8, "action": "done", "selector": "", "ok": True},
]


# --- recipe distillation ----------------------------------------------------


def test_recipe_keeps_only_proven_replayable_steps(tmp_path: Path):
    store = _journal_job(tmp_path, "j1", SAMPLE_EVENTS)
    recipe = build_recipe("j1", store)

    actions = [(s.action, s.selector) for s in recipe.steps]
    assert actions == [
        ("navigate", "https://example.com/"),  # prepended from start_url
        ("type", "input#q"),
        ("click", "button.search"),
        ("click", "//a[@id='first']"),
    ], actions


def test_recipe_drops_failed_steps(tmp_path: Path):
    store = _journal_job(tmp_path, "j1", SAMPLE_EVENTS)
    recipe = build_recipe("j1", store)
    assert all(s.selector != ".missing" for s in recipe.steps)


def test_recipe_collapses_immediate_duplicates(tmp_path: Path):
    store = _journal_job(tmp_path, "j1", SAMPLE_EVENTS)
    recipe = build_recipe("j1", store)
    clicks = [s.selector for s in recipe.steps if s.action == "click"]
    assert clicks.count("button.search") == 1, clicks


def test_recipe_infers_xpath_vs_css(tmp_path: Path):
    store = _journal_job(tmp_path, "j1", SAMPLE_EVENTS)
    recipe = build_recipe("j1", store)
    by_sel = {s.selector: s.method for s in recipe.steps}
    assert by_sel["input#q"] == "css"
    assert by_sel["//a[@id='first']"] == "xpath"
    assert by_sel["https://example.com/"] == "url"


def test_recipe_carries_typed_text_and_clear_flag(tmp_path: Path):
    store = _journal_job(tmp_path, "j1", SAMPLE_EVENTS)
    recipe = build_recipe("j1", store)
    typed = [s for s in recipe.steps if s.action == "type"][0]
    assert typed.text == "cats"
    assert typed.clear_first is True


def test_recipe_records_wait_as_annotation_not_step(tmp_path: Path):
    store = _journal_job(tmp_path, "j1", SAMPLE_EVENTS)
    recipe = build_recipe("j1", store)
    assert all(s.action != "wait" for s in recipe.steps)
    assert any(a["action"] == "wait" and a["selector"] == ".results"
               for a in recipe.annotations)


def test_recipe_recovers_objective_and_url_from_journal(tmp_path: Path):
    store = _journal_job(tmp_path, "j1", SAMPLE_EVENTS)
    recipe = build_recipe("j1", store)
    assert recipe.objective == "Search for cats"
    assert recipe.start_url == "https://example.com/"


def test_recipe_roundtrips_through_disk(tmp_path: Path):
    store = _journal_job(tmp_path, "j1", SAMPLE_EVENTS)
    recipe = build_recipe("j1", store)
    save_recipe(recipe, store)
    reloaded = load_recipe("j1", store)
    assert [s.to_dict() for s in reloaded.steps] == [s.to_dict() for s in recipe.steps]
    assert reloaded.objective == recipe.objective


def test_empty_journal_yields_no_steps(tmp_path: Path):
    store = _journal_job(tmp_path, "empty", [
        {"type": "job_started", "objective": "nothing", "start_url": ""},
    ])
    recipe = build_recipe("empty", store)
    assert recipe.steps == []


# --- slug safety ------------------------------------------------------------


def test_slugify_makes_safe_names():
    assert slugify("My Flow! v2") == "my-flow-v2"
    assert slugify("  Trailing --- ") == "trailing"


def test_slugify_rejects_empty():
    for bad in ["", "   ", "!!!"]:
        try:
            slugify(bad)
        except GenerationError:
            continue
        raise AssertionError(f"slugify({bad!r}) should have raised")


# --- generation -------------------------------------------------------------


def _gen(tmp_path: Path, **kw):
    store = _journal_job(tmp_path, "j1", SAMPLE_EVENTS)
    recipe = build_recipe("j1", store)
    dest = tmp_path / "created-extensions"
    return generate(recipe, name="Cat Search Demo", dest=dest, **kw), dest


def test_generate_writes_a_loadable_tree(tmp_path: Path):
    root, _ = _gen(tmp_path)
    for rel in ["manifest.json", "js/background.js", "js/recipe.js",
                "js/popup.js", "html/popup.html", "css/popup.css", "README.md"]:
        assert (root / rel).is_file(), f"missing {rel}"


def test_generated_manifest_is_valid_mv3_module_worker(tmp_path: Path):
    root, _ = _gen(tmp_path)
    m = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert m["manifest_version"] == 3
    assert m["background"]["service_worker"] == "js/background.js"
    assert m["background"]["type"] == "module"
    assert m["action"]["default_popup"] == "html/popup.html"


def test_generated_recipe_module_embeds_the_recipe(tmp_path: Path):
    root, _ = _gen(tmp_path)
    text = (root / "js/recipe.js").read_text(encoding="utf-8")
    assert "export const RECIPE" in text
    assert "export const HEAL_WS" in text
    assert "input#q" in text and "button.search" in text
    # The heal endpoint defaults to the ai-web-auto backend port.
    assert "ws://localhost:8765" in text


def test_generated_worker_handles_every_action(tmp_path: Path):
    root, _ = _gen(tmp_path)
    bg = (root / "js/background.js").read_text(encoding="utf-8")
    for token in ['import { RECIPE, HEAL_WS }',
                  'step.action === "navigate"',
                  'step.action === "click"',
                  'step.action === "type"',
                  "Input.insertText",       # React-safe typing
                  "Input.dispatchMouseEvent",  # trusted clicks
                  "function heal("]:        # runtime self-heal fallback
        assert token in bg, f"background.js missing {token!r}"


def test_generated_files_have_no_bom(tmp_path: Path):
    root, _ = _gen(tmp_path)
    for path in root.rglob("*"):
        if path.is_file():
            head = path.open("rb").read(3)
            assert head != b"\xef\xbb\xbf", f"{path} has a BOM"


def test_generate_refuses_to_clobber_without_force(tmp_path: Path):
    root, dest = _gen(tmp_path)
    store = JobStore(tmp_path / "jobs")
    recipe = build_recipe("j1", store)
    try:
        generate(recipe, name="Cat Search Demo", dest=dest)
    except GenerationError:
        pass
    else:
        raise AssertionError("second generate should refuse without overwrite")
    # ...and succeeds with force.
    again = generate(recipe, name="Cat Search Demo", dest=dest, overwrite=True)
    assert again == root


def test_generate_rejects_a_recipe_with_no_steps(tmp_path: Path):
    store = _journal_job(tmp_path, "empty", [
        {"type": "job_started", "objective": "nothing", "start_url": ""},
    ])
    recipe = build_recipe("empty", store)
    try:
        generate(recipe, name="nothing", dest=tmp_path / "out")
    except GenerationError:
        return
    raise AssertionError("generate should reject an empty recipe")


# --- run without pytest -----------------------------------------------------
# The vendored venv has no pytest; the CI/dev loop runs this file directly.
# Every test takes a `tmp_path`, so a tiny runner supplies one per test.

def _run_standalone() -> int:
    import inspect
    import tempfile
    import traceback

    tests = [
        (name, fn) for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    passed = failed = 0
    for name, fn in tests:
        params = inspect.signature(fn).parameters
        try:
            if "tmp_path" in params:
                with tempfile.TemporaryDirectory() as tmp:
                    fn(Path(tmp))
            else:
                fn()
            passed += 1
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_standalone())
