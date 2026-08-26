"""Turn a distilled Recipe into a standalone Chrome extension.

This is the code path CLAUDE.md used to say did not exist: given an ordered
recipe (jobs/recipe.py), write a self-contained `created-extensions/<name>/`
tree — manifest, CDP replayer service worker, popup — that replays the recipe
without the LLM in the loop, with a best-effort backend heal for broken
selectors. Deterministic and data-driven: no LLM call happens here.

    from ai_web_auto_backend.extensiongen import generate
    path = generate(recipe, name="my-flow", dest="created-extensions")
"""

from __future__ import annotations

import re
from pathlib import Path

from . import templates

__all__ = ["generate", "slugify", "GenerationError"]

DEFAULT_HEAL_PORT = 8765


class GenerationError(Exception):
    """Raised when a recipe cannot be turned into a loadable extension."""


def slugify(name: str) -> str:
    """A filesystem- and id-safe extension folder name."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    if not slug:
        raise GenerationError("name produced an empty slug; pass --name with letters/digits")
    return slug


def _heal_ws(host: str, port: int) -> str:
    return f"ws://{host}:{port}"


def generate(
    recipe,
    name: str,
    dest: str | Path = "created-extensions",
    *,
    description: str = "",
    heal_host: str = "localhost",
    heal_port: int = DEFAULT_HEAL_PORT,
    overwrite: bool = False,
) -> Path:
    """Write a standalone extension for `recipe` under dest/<slug>/ and return it.

    Refuses to clobber an existing directory unless overwrite=True, so a second
    `generate` never silently eats a hand-tuned extension.
    """
    if not getattr(recipe, "steps", None):
        raise GenerationError(
            "recipe has no replayable steps — nothing to package. "
            "Run a job that clicks/types/navigates first."
        )

    slug = slugify(name)
    root = Path(dest) / slug
    if root.exists() and not overwrite:
        raise GenerationError(
            f"{root} already exists; pass overwrite=True (CLI: --force) to replace it"
        )

    recipe_dict = recipe.to_dict()
    heal_ws = _heal_ws(heal_host, heal_port)
    display_name = name.strip() or slug
    desc = description or f"Replays a recipe learned by ai-web-auto: {recipe_dict.get('objective', '')[:80]}"

    files: dict[str, str] = {
        "manifest.json": templates.manifest(display_name, desc, heal_ws),
        "js/recipe.js": templates.recipe_module(recipe_dict, heal_ws),
        "js/background.js": templates.BACKGROUND_JS,
        "js/popup.js": templates.POPUP_JS,
        "html/popup.html": templates.POPUP_HTML,
        "css/popup.css": templates.POPUP_CSS,
        "README.md": templates.readme(display_name, recipe_dict, heal_ws),
    }

    root.mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        # LF, no BOM: a BOM in front of an ES-module service worker is a parse
        # error, the exact class of bug the Scriptase staging code guards against.
        path.write_text(body, encoding="utf-8", newline="\n")

    return root
