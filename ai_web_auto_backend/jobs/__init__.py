"""Self-play job layer: persistent, resumable, knowledge-acquiring runs.

The lightweight, offline pieces (models, store, knowledge, recipe distillation)
import with no third-party runtime deps. `JobRunner` is the only heavy export —
it pulls in the browser/LLM stack (websockets, anthropic) via
automation_controller — so it is imported lazily. That keeps the offline
`jobs generate` / recipe path usable without the full runtime installed.
"""

from .knowledge import SiteKnowledge
from .models import JobStatus, classify_error
from .recipe import Recipe, build_recipe, load_recipe, save_recipe
from .store import JobStore

__all__ = [
    "JobStatus", "classify_error", "JobStore", "SiteKnowledge",
    "Recipe", "build_recipe", "load_recipe", "save_recipe",
    "JobRunner",
]


def __getattr__(name: str):
    # PEP 562 lazy attribute: only touch the heavy runtime stack when JobRunner
    # is actually requested, so importing the offline pieces stays dep-free.
    if name == "JobRunner":
        from .runner import JobRunner
        return JobRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
