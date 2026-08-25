"""Central logging configuration — console + rotating file sinks.

Every entrypoint (controller CLI, learner, runner-execution) calls
setup_logging() with its own name so concurrent processes never fight over
the same rotating file on Windows.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

_CONFIGURED = False


def setup_logging(name: str = "backend", level: str = "INFO",
                  logs_dir: str | Path = "logs") -> Path:
    """Keep the default console sink and add a rotated file sink.

    Idempotent per process. diagnose=False keeps variable values (which may
    include page content or credentials) out of traceback rendering.
    Returns the resolved log directory.
    """
    global _CONFIGURED

    logs_path = Path(logs_dir)
    logs_path.mkdir(parents=True, exist_ok=True)

    if not _CONFIGURED:
        logger.add(
            logs_path / f"ai-web-auto-{name}.log",
            level=level,
            rotation="10 MB",
            retention=5,
            enqueue=True,
            backtrace=False,
            diagnose=False,
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} - {message}",
        )
        _CONFIGURED = True
    else:
        logger.warning(f"setup_logging already configured; ignoring request for sink '{name}'")

    return logs_path
