"""Distil a job's journal into an ordered, replayable recipe.

The journal already records every action the AI loop took, in order, with its
selector and whether it succeeded (`store.append(..., "action_result", ...)` in
runner.py). That sequence *is* the recipe — it just needs cleaning up:

  * keep only the steps that actually change the page and actually succeeded;
  * drop perception-only steps (screenshot/extract) and the terminal `done`;
  * drop failed attempts and the retries that supersede them;
  * collapse consecutive duplicates (the loop sometimes re-clicks to verify).

What's left is a short list of {action, method, selector, params} a dumb
replayer can execute top to bottom. This is the ordered artifact the extension
generator consumes; the flat knowledge/site store cannot provide order.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .models import now_iso

# Actions that change page state and belong in a replay. `navigate` is included
# because a recipe that doesn't start by going somewhere replays into whatever
# tab happens to be focused. Perception (screenshot/extract), control-flow
# (wait/done), and scroll (position is rarely reproducible and rarely load-
# bearing) are deliberately excluded from the executable core.
REPLAYABLE_ACTIONS = ("navigate", "click", "type")

# Actions worth remembering as hints even though the replayer skips them — they
# tell a human reading recipe.json what the run waited on.
ANNOTATION_ACTIONS = ("wait", "scroll")


@dataclass
class RecipeStep:
    action: str
    method: str = "css"          # css | xpath | url (url only for navigate)
    selector: str = ""           # the css/xpath, or the URL for navigate
    text: str = ""               # typed text, for action == "type"
    clear_first: bool = True     # for type: clear the field before typing
    note: str = ""               # free text, e.g. an original step index

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Recipe:
    """A replayable recipe distilled from one job."""
    job_id: str = ""
    objective: str = ""
    start_url: str = ""
    created_at: str = field(default_factory=now_iso)
    steps: list[RecipeStep] = field(default_factory=list)
    # Steps we saw but chose not to replay (waits/scrolls), kept as breadcrumbs.
    annotations: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "objective": self.objective,
            "start_url": self.start_url,
            "created_at": self.created_at,
            "steps": [s.to_dict() for s in self.steps],
            "annotations": list(self.annotations),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Recipe":
        steps = [RecipeStep(**s) for s in d.get("steps", [])]
        return cls(
            job_id=d.get("job_id", ""),
            objective=d.get("objective", ""),
            start_url=d.get("start_url", ""),
            created_at=d.get("created_at", now_iso()),
            steps=steps,
            annotations=list(d.get("annotations", [])),
        )


def _method_of(ev: dict) -> str:
    """The journal records the resolved selector but not whether it was css or
    xpath. Infer: an XPath starts with / or ( ; everything else is css. For
    navigate the 'selector' field is actually the URL."""
    if ev.get("action") == "navigate":
        return "url"
    sel = (ev.get("selector") or "").strip()
    if sel.startswith("/") or sel.startswith("(") or sel.startswith("./"):
        return "xpath"
    return "css"


def build_recipe(job_id: str, store, objective: str = "", start_url: str = "") -> Recipe:
    """Read the journal for `job_id` and return a distilled Recipe.

    `store` is a JobStore. objective/start_url override what the journal carries
    (the runner logs them in the `job_started`/`navigated` events); callers that
    already hold the JobSpec should pass them so the recipe is self-describing
    even if the journal's head is missing.
    """
    recipe = Recipe(job_id=job_id, objective=objective, start_url=start_url)

    for ev in store.read_journal(job_id):
        etype = ev.get("type")
        # Recover objective/start_url from the journal when not supplied.
        if etype == "job_started":
            recipe.objective = recipe.objective or ev.get("objective", "")
            recipe.start_url = recipe.start_url or ev.get("start_url", "")
            continue
        if etype == "navigated" and ev.get("ok") and not recipe.start_url:
            recipe.start_url = ev.get("url", "")
        if etype != "action_result":
            continue

        action = (ev.get("action") or "").strip()
        if not ev.get("ok"):
            continue  # failed attempts never make the recipe

        if action in ANNOTATION_ACTIONS:
            recipe.annotations.append({
                "after_step": len(recipe.steps),
                "action": action,
                "selector": ev.get("selector") or "",
            })
            continue

        if action not in REPLAYABLE_ACTIONS:
            continue  # screenshot / extract / done / anything unknown

        selector = (ev.get("selector") or "").strip()
        # A click/type with no selector cannot be replayed deterministically.
        if action in ("click", "type") and not selector:
            continue

        step = RecipeStep(
            action=action,
            method=_method_of(ev),
            selector=selector,
            text=ev.get("text", "") or "",
            clear_first=bool(ev.get("clear_first", True)),
            note=f"step {ev.get('step')}" if ev.get("step") is not None else "",
        )

        # Collapse an immediately repeated identical step (verify re-clicks).
        if recipe.steps:
            prev = recipe.steps[-1]
            if (prev.action, prev.method, prev.selector, prev.text) == (
                step.action, step.method, step.selector, step.text
            ):
                continue

        recipe.steps.append(step)

    # If the recipe has replayable steps but no leading navigate, prepend one so
    # the extension lands on the right page before touching anything.
    if recipe.start_url and (
        not recipe.steps or recipe.steps[0].action != "navigate"
    ):
        recipe.steps.insert(0, RecipeStep(
            action="navigate", method="url", selector=recipe.start_url,
            note="start_url (implicit)",
        ))

    return recipe


def save_recipe(recipe: Recipe, store, job_id: Optional[str] = None) -> Path:
    """Write recipe.json into the job dir. Returns the path."""
    job_id = job_id or recipe.job_id
    path = store.dir(job_id) / "recipe.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(recipe.to_dict(), indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def load_recipe(job_id: str, store) -> Recipe:
    path = store.dir(job_id) / "recipe.json"
    return Recipe.from_dict(json.loads(path.read_text(encoding="utf-8")))
