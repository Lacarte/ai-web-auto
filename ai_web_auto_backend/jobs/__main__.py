"""CLI: python -m ai_web_auto_backend.jobs <start|explore|resume|report|list|knowledge>

Exit codes: 0 completed, 2 failed, 3 interrupted, 4 could not run.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import webbrowser

from loguru import logger

from ..automation_controller import BrowserAgent
from ..logsetup import setup_logging
from .models import JobStatus
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
