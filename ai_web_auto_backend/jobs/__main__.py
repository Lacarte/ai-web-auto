"""CLI: python -m ai_web_auto_backend.jobs <start|explore|resume|report|generate|list|knowledge>

`generate` is offline (like list/report/knowledge): it distils a finished job's
journal into a replayable recipe and packages it as a standalone extension under
created-extensions/ — no browser connection required.

Exit codes: 0 completed, 2 failed, 3 interrupted, 4 could not run.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import webbrowser

from loguru import logger

from ..automation_controller import BrowserAgent
from ..extensiongen import GenerationError, generate as generate_extension
from ..logsetup import setup_logging
from .models import JobStatus
from .recipe import build_recipe, load_recipe, save_recipe
from .runner import JobRunner


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jobs", description="Self-play automation jobs")
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--model", default="claude-sonnet-4-20250514")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start", help="Run an objective against a site")
    s.add_argument("--url", required=True)
    s.add_argument("--objective", required=True)
    s.add_argument("--max-steps", type=int, default=40)
    s.add_argument("--max-minutes", type=int, default=20)

    e = sub.add_parser("explore", help="Self-play: map a site without a hard goal")
    e.add_argument("--url", required=True)
    e.add_argument("--max-steps", type=int, default=40)
    e.add_argument("--max-minutes", type=int, default=20)

    r = sub.add_parser("resume", help="Continue an interrupted/failed job")
    r.add_argument("job_id", nargs="?", help="Job id, or --latest")

    rep = sub.add_parser("report", help="(Re)generate the HTML report for a job")
    rep.add_argument("job_id")
    rep.add_argument("--open", action="store_true", dest="open_browser")

    gen = sub.add_parser(
        "generate",
        help="Package a job's proven recipe into a standalone extension")
    gen.add_argument("--job", required=True, dest="job_id")
    gen.add_argument("--name", required=True, help="extension name / folder slug")
    gen.add_argument("--dest", default="created-extensions",
                     help="output root (default: created-extensions)")
    gen.add_argument("--force", action="store_true",
                     help="overwrite an existing extension folder")

    sub.add_parser("list", help="List jobs and their status")
    sub.add_parser("knowledge", help="Summarize accumulated per-site knowledge")
    return p


def _print_jobs(runner: JobRunner) -> None:
    specs = runner.list_jobs()
    if not specs:
        print("No jobs yet.")
        return
    print(f"{'JOB ID':<22} {'STATUS':<12} {'STEP':<6} {'OK':<4} DETAIL")
    for s in specs:
        detail = (s.error or s.objective)[:60]
        print(f"{s.id:<22} {s.status.value:<12} {s.steps_done}/{s.max_steps:<4} "
              f"{'yes' if s.success else 'no':<4} {detail}")


def _generate(runner: JobRunner, args) -> int:
    """Offline: distil the job's recipe (or reuse recipe.json) and package it as
    a standalone extension. No browser connection needed."""
    store = runner.store
    if not store.dir(args.job_id).is_dir():
        print(f"No such job: {args.job_id}  (see: jobs list)")
        return 4

    recipe_path = store.dir(args.job_id) / "recipe.json"
    try:
        if recipe_path.exists():
            recipe = load_recipe(args.job_id, store)
        else:
            # Older jobs (pre-recipe) or one that never finished cleanly: distil
            # from the journal now and persist it for next time.
            recipe = build_recipe(args.job_id, store)
            save_recipe(recipe, store)
    except Exception as e:
        print(f"Could not read the recipe for {args.job_id}: {e}")
        return 4

    if not recipe.steps:
        print(f"Job {args.job_id} produced no replayable steps — nothing to package.")
        print("Run a job that clicks/types/navigates, then generate from it.")
        return 4

    try:
        path = generate_extension(recipe, name=args.name, dest=args.dest,
                                  overwrite=args.force)
    except GenerationError as e:
        print(str(e))
        return 4

    print(f"Generated extension: {path}  ({len(recipe.steps)} steps)")
    print("Load unpacked in chrome://extensions (Developer mode), open the target "
          "site, and press Run recipe.")
    return 0


async def _connect(args) -> BrowserAgent:
    agent = BrowserAgent(host=args.host, port=args.port, model=args.model)
    await agent.connect()
    return agent


def _summary(spec) -> int:
    icon = {"completed": "COMPLETED", "failed": "FAILED",
            "interrupted": "INTERRUPTED", "cancelled": "CANCELLED"}
    label = icon.get(spec.status.value, spec.status.value.upper())
    print(f"\n{label}: {spec.objective[:80]}")
    print(f"  Steps: {spec.steps_done}/{spec.max_steps} | Duration: {spec.duration_ms}ms "
          f"| Resumes: {spec.resume_count}")
    if spec.error:
        print(f"  Error: [{spec.error_code}] {spec.error}")
    if spec.result_data:
        data = str(spec.result_data)
        print(f"  Result: {data[:400]}{'...' if len(data) > 400 else ''}")
    print(f"  Report: jobs/{spec.id}/report.html")
    return {"completed": 0, "failed": 2}.get(spec.status.value, 3)


async def main_async(argv: list[str]) -> int:
    args = _build_parser().parse_args(argv)

    # Pure file operations — no connection needed.
    probe_runner = JobRunner(agent=None)
    if args.cmd == "list":
        _print_jobs(probe_runner)
        return 0
    if args.cmd == "knowledge":
        stats = probe_runner.knowledge.stats()
        if not stats:
            print("No knowledge yet.")
            return 0
        for domain, st in sorted(stats.items()):
            print(f"  {domain:<40} selectors: {st['selectors']:<5} proven: {st['proven']}")
        return 0
    if args.cmd == "report":
        path = probe_runner.report(args.job_id)
        print(path)
        if args.open_browser:
            webbrowser.open(path.resolve().as_uri())
        return 0
    if args.cmd == "generate":
        return _generate(probe_runner, args)

    setup_logging("jobs")
    try:
        agent = await _connect(args)
    except Exception as e:
        print(f"Could not connect to extension on ws://{args.host}:{args.port}: {e}")
        print("Start runner.bat and make sure the extension is loaded.")
        return 4

    runner = JobRunner(agent)
    try:
        if args.cmd == "start":
            spec = await runner.start(objective=args.objective, start_url=args.url,
                                      max_steps=args.max_steps,
                                      max_minutes=args.max_minutes)
        elif args.cmd == "explore":
            spec = await runner.start(objective="", start_url=args.url,
                                      max_steps=args.max_steps,
                                      max_minutes=args.max_minutes, explore=True)
        elif args.cmd == "resume":
            job_id = args.job_id
            if not job_id or job_id == "--latest":
                resumable = [s for s in runner.list_jobs()
                             if s.status in (JobStatus.INTERRUPTED, JobStatus.FAILED)]
                if not resumable:
                    print("No resumable jobs.")
                    return 0
                job_id = sorted(s.id for s in resumable)[-1]
                print(f"Resuming latest: {job_id}")
            spec = await runner.resume(job_id)
        else:  # pragma: no cover - argparse enforces choices
            return 4
        return _summary(spec)
    except ValueError as e:
        print(str(e))
        return 4
    except KeyboardInterrupt:
        logger.warning("Interrupted by user — job marked interrupted; resume with: "
                       "python -m ai_web_auto_backend.jobs resume --latest")
        return 3
    finally:
        await agent.stop()


def main() -> None:
    sys.exit(asyncio.run(main_async(sys.argv[1:])))


if __name__ == "__main__":
    main()
