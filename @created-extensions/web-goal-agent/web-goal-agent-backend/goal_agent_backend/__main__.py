"""Backend entry point.

    python -m goal_agent_backend [--config PATH] [--started-by manual|native-host]

Exit codes: 0 clean shutdown, 2 bad config, 3 port already in use.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time

from loguru import logger

from . import __version__
from .browser import ExtensionBrowser
from .config import load_config
from .engine import Engine
from .planner import ClaudePlanner
from .protocol import UiRequest
from .server import AgentServer

EXIT_CONFIG = 2
EXIT_PORT_IN_USE = 3


def _setup_logging(state_dir, started_by: str):
    # websockets logs a traceback for every non-HTTP probe of the port (health
    # checks, scanners); our own handshake logging covers what matters.
    logging.getLogger("websockets").setLevel(logging.CRITICAL)
    logger.remove()
    logger.add(state_dir / "backend.log", rotation="1 MB", retention=3, level="INFO", enqueue=False,
               format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | {message}")
    if started_by == "manual":
        logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | {level:<7} | {message}")


async def amain(args) -> int:
    try:
        cfg = load_config(args.config)
    except (OSError, ValueError) as e:
        print(f"[web-goal-agent] invalid config: {e}", file=sys.stderr)
        return EXIT_CONFIG
    state_dir = cfg.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    cfg.runs_dir.mkdir(parents=True, exist_ok=True)
    _setup_logging(state_dir, args.started_by)

    engine_ref: dict[str, Engine] = {}

    def capabilities() -> dict:
        engine = engine_ref.get("engine")
        return {"ai_planner": engine.ai_available() if engine else {"available": False},
                "goals": len(engine.goals) if engine else 0}

    server = AgentServer(cfg.host, cfg.port, cfg.token, cfg.allowed_origins,
                         started_by=args.started_by, capabilities=capabilities)
    browser = ExtensionBrowser(server, cfg.timeout("command_s"))
    ai = cfg["ai"]
    engine = Engine(cfg, browser, server.push,
                    planner_factory=lambda: ClaudePlanner(ai["model"], ai.get("effort", "medium"),
                                                          int(ai.get("max_tokens", 16000))))
    engine_ref["engine"] = engine
    server.on_client_change(engine.on_client_change)
    server.on_event("tab_closed", engine.on_tab_closed)
    server.on_event("js_dialog", engine.on_js_dialog)
    server.on_event("debugger_detached", engine.on_debugger_detached)

    async def ui(req: UiRequest):
        a = req.parsed_args()
        if req.op == "get_state":
            return engine.state_snapshot()
        if req.op == "list_goals":
            return engine.list_goals()
        if req.op == "start_run":
            return await engine.start_run(a)
        if req.op == "pause_run":
            return await engine.pause(a)
        if req.op == "resume_run":
            return await engine.resume(a)
        if req.op == "stop_run":
            return await engine.stop(a)
        if req.op == "confirm":
            return await engine.confirm(a)
        raise ValueError(f"unsupported op {req.op}")

    server.set_ui_handler(ui)

    try:
        await server.start()
    except OSError as e:
        msg = f"port {cfg.port} on {cfg.host} is already in use ({e.strerror or e})"
        logger.error(msg)
        print(f"[web-goal-agent] {msg}", file=sys.stderr)
        return EXIT_PORT_IN_USE

    pid_file = state_dir / "backend.json"
    pid_file.write_text(json.dumps({**server.health(), "config": str(cfg.path)}, indent=2), encoding="utf-8")
    if args.started_by == "manual":
        print(f"\n  Web Goal Agent backend v{__version__} on ws://{cfg.host}:{cfg.port}")
        print(f"  Goals: {len(engine.goals)} loaded from {', '.join(map(str, cfg.goals_dirs))}"
              + (f" ({len(engine.goal_errors)} rejected - see log)" if engine.goal_errors else ""))
        print("  The extension gets the token from the native host; for manual pairing it is in")
        print(f"  {cfg.path}\n  Ctrl+C to stop.\n")

    watchdog = asyncio.create_task(_idle_watchdog(server, engine, cfg))
    try:
        await server.wait_for_shutdown()
    finally:
        watchdog.cancel()
        reason = server.shutdown_reason or "signal"
        logger.info(f"Shutting down ({reason})")
        if engine.run and not engine.run.terminal:
            run = engine.run
            run._stop_reason = f"backend shutting down ({reason})"
            run._resume.set()
            if run._confirm and not run._confirm.done():
                run._confirm.set_result(False)
            if run.task:
                try:
                    await asyncio.wait_for(asyncio.shield(run.task), 10)
                except (asyncio.TimeoutError, Exception):
                    pass
        await server.push("shutting_down", {"reason": reason})
        await server.stop()
        try:
            pid_file.unlink()
        except FileNotFoundError:
            pass
    return 0


async def _idle_watchdog(server: AgentServer, engine: Engine, cfg):
    minutes = float(cfg["idle_shutdown_minutes"] or 0)
    if server.started_by != "native-host" or minutes <= 0:
        return
    while True:
        await asyncio.sleep(15)
        idle = time.monotonic() - server.last_client_change
        if not server.session.connected and engine.run is None and idle > minutes * 60:
            server.request_shutdown("idle")
            return


def main():
    parser = argparse.ArgumentParser(prog="goal_agent_backend", description="Web Goal Agent backend")
    parser.add_argument("--config", help="config file (default: $WGA_CONFIG or config.json)")
    parser.add_argument("--started-by", choices=["manual", "native-host"], default="manual")
    args = parser.parse_args()
    try:
        code = asyncio.run(amain(args))
    except KeyboardInterrupt:
        code = 0
    sys.exit(code)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    main()
