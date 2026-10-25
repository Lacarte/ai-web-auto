"""Goal specs: what to achieve, how to recognise it, and a plan to get there.

A goal is its `success` criteria. Steps are the plan; each step carries the
evidence (`expect`) that proves it worked, and may be skipped when the page is
already in the target state (`skip_if`), so re-running a goal is safe.

Locators are semantic descriptors resolved in the page (see agent-page.js):
    {"role": "button", "name": "Save", "within": {"role": "dialog", "name": "Edit customer"}}
Strategies inside one locator are ANDed; `any` lists alternatives in priority
order. XPath must be relative to the scope (".//..."): absolute document paths
break on any layout change, so they are rejected.

Strings may reference inputs as {{inputs.name}}.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal, Optional, Union
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

ACTIONS = ("navigate", "click", "type", "select", "check", "press", "hover", "scroll", "wait", "assert", "extract")
# Actions whose effect is a page state we can re-check, so repeating them is harmless.
REPEATABLE_ACTIONS = {"navigate", "type", "select", "check", "hover", "scroll", "wait", "assert", "extract"}
NEEDS_EXPECT = {"click", "press", "wait", "assert"}
NEEDS_TARGET = {"click", "type", "select", "check", "hover"}
SCOPES = ("active-dialog", "page")

_TEMPLATE = re.compile(r"\{\{\s*inputs\.([A-Za-z0-9_]+)\s*\}\}")
_ORIGIN = re.compile(r"^(https?)://[^/\s?#]+$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── Text matching ───────────────────────────────────────────────────────────

class TextMatch(_Strict):
    """Whitespace-normalised, case-insensitive unless case_sensitive is set."""
    equals: Optional[str] = None
    contains: Optional[str] = None
    regex: Optional[str] = None
    case_sensitive: bool = False

    @model_validator(mode="before")
    @classmethod
    def _from_string(cls, v: Any) -> Any:
        return {"equals": v} if isinstance(v, str) else v

    @model_validator(mode="after")
    def _one_mode(self) -> "TextMatch":
        modes = [m for m in (self.equals, self.contains, self.regex) if m is not None]
        if len(modes) != 1:
            raise ValueError("text match needs exactly one of equals/contains/regex")
        if self.regex is not None:
            re.compile(self.regex)
        return self


# ── Locators ────────────────────────────────────────────────────────────────

class Locator(_Strict):
    role: Optional[str] = Field(default=None, max_length=40)
    name: Optional[TextMatch] = None
    testid: Optional[str] = Field(default=None, max_length=200)
    id: Optional[str] = Field(default=None, max_length=200)
    label: Optional[TextMatch] = None
    placeholder: Optional[TextMatch] = None
    text: Optional[TextMatch] = None
    css: Optional[str] = Field(default=None, max_length=500)
    xpath: Optional[str] = Field(default=None, max_length=500)
    within: Optional[Union[Literal["active-dialog", "page"], "Locator"]] = None
    nth: Optional[int] = Field(default=None, ge=0)
    any: Optional[list["Locator"]] = Field(default=None, min_length=1, max_length=10)

    @field_validator("xpath")
    @classmethod
    def _relative_xpath(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.startswith("."):
            raise ValueError("xpath must be relative to its scope (start with '.', e.g. './/button')")
        return v

    @model_validator(mode="after")
    def _has_strategy(self) -> "Locator":
        strategies = [self.role, self.name, self.testid, self.id, self.label,
                      self.placeholder, self.text, self.css, self.xpath]
        if self.any is not None:
            if any(s is not None for s in strategies):
                raise ValueError("'any' cannot be combined with other strategies in the same locator")
        elif all(s is None for s in strategies):
            raise ValueError("locator needs at least one of role/name/testid/id/label/placeholder/text/css/xpath")
        if self.name is not None and self.role is None:
            raise ValueError("'name' (accessible name) needs a 'role'; use 'text' to match visible text")
        return self

    def wire(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


Locator.model_rebuild()

LocatorOrScope = Union[Literal["active-dialog", "page"], Locator]


# ── Expectations ────────────────────────────────────────────────────────────

class _TextIn(_Strict):
    text: TextMatch
    within: Optional[LocatorOrScope] = None


class _DialogMatch(_Strict):
    name: Optional[TextMatch] = None
    role: Optional[Literal["dialog", "alertdialog"]] = None


class _UrlMatch(_Strict):
    equals: Optional[str] = None
    contains: Optional[str] = None
    regex: Optional[str] = None
    changed: Optional[bool] = None

    @model_validator(mode="after")
    def _one(self) -> "_UrlMatch":
        if sum(x is not None for x in (self.equals, self.contains, self.regex, self.changed)) != 1:
            raise ValueError("url expectation needs exactly one of equals/contains/regex/changed")
        return self


_TargetRef = Union[Literal["$target"], Locator]


class _ValueMatch(_Strict):
    target: _TargetRef
    equals: Optional[str] = None
    contains: Optional[str] = None
    regex: Optional[str] = None


class _Checked(_Strict):
    target: _TargetRef
    value: bool = True


class _Attribute(_Strict):
    target: _TargetRef
    name: str = Field(max_length=100)
    equals: Optional[str] = None
    contains: Optional[str] = None
    regex: Optional[str] = None
    present: Optional[bool] = None


class _Count(_Strict):
    target: _TargetRef
    eq: Optional[int] = None
    gte: Optional[int] = None
    lte: Optional[int] = None
    gt: Optional[int] = None
    lt: Optional[int] = None

    @model_validator(mode="after")
    def _one(self) -> "_Count":
        if sum(x is not None for x in (self.eq, self.gte, self.lte, self.gt, self.lt)) < 1:
            raise ValueError("count expectation needs eq/gte/lte/gt/lt")
        return self


EXPECTATION_KINDS: dict[str, Any] = {
    "url": _UrlMatch,
    "title": TextMatch,
    "visible": _TargetRef,
    "hidden": _TargetRef,
    "enabled": _TargetRef,
    "disabled": _TargetRef,
    "focused": _TargetRef,
    "dialog_open": _DialogMatch,
    "dialog_closed": _DialogMatch,
    "text_present": _TextIn,
    "text_absent": _TextIn,
    "value": _ValueMatch,
    "checked": _Checked,
    "attribute": _Attribute,
    "count": _Count,
}


class Expectation(_Strict):
    """One observable fact, written as {"<kind>": <args>}."""
    kind: str
    args: Any

    @model_validator(mode="before")
    @classmethod
    def _from_mapping(cls, v: Any) -> Any:
        if isinstance(v, dict) and set(v) != {"kind", "args"}:
            if len(v) != 1:
                raise ValueError(f"an expectation has exactly one key, got {sorted(v)}")
            (kind, args), = v.items()
            return {"kind": kind, "args": args}
        return v

    @model_validator(mode="after")
    def _validate_args(self) -> "Expectation":
        schema = EXPECTATION_KINDS.get(self.kind)
        if schema is None:
            raise ValueError(f"unknown expectation '{self.kind}' (known: {', '.join(EXPECTATION_KINDS)})")
        from pydantic import TypeAdapter
        parsed = TypeAdapter(schema).validate_python(self.args)
        self.args = parsed
        return self

    def wire(self, step_target: Optional[Locator] = None) -> dict[str, Any]:
        """The JSON the page library evaluates, with $target replaced by the step's locator."""
        def dump(v: Any) -> Any:
            if v == "$target":
                if step_target is None:
                    raise ValueError(f"'{self.kind}' refers to $target but the step has no target")
                return step_target.wire()
            if isinstance(v, BaseModel):
                return {k: dump(getattr(v, k)) for k in type(v).model_fields if getattr(v, k) is not None}
            if isinstance(v, list):
                return [dump(x) for x in v]
            return v
        return {"kind": self.kind, "args": dump(self.args)}

    def describe(self) -> str:
        a = self.args
        if self.kind in ("dialog_open", "dialog_closed"):
            state = "open" if self.kind == "dialog_open" else "closed"
            return f'dialog "{_match_text(a.name)}" is {state}' if a.name else f"a dialog is {state}"
        if self.kind in ("text_present", "text_absent"):
            return f"text {_match_text(a.text)!r} is {'shown' if self.kind == 'text_present' else 'gone'}"
        if self.kind == "url":
            return "the URL changed" if a.changed else f"the URL matches {a.equals or a.contains or a.regex!r}"
        if self.kind == "value":
            return f"the field value is {a.equals or a.contains or a.regex!r}"
        if self.kind in ("visible", "hidden", "enabled", "disabled", "focused"):
            what = "the target" if a == "$target" else describe_locator(a)
            return f"{what} is {self.kind}"
        return self.kind.replace("_", " ")


def _match_text(m: Optional[TextMatch]) -> str:
    if m is None:
        return ""
    return m.equals or m.contains or m.regex or ""


def describe_locator(loc: Union[Locator, str, None]) -> str:
    """Human phrase for overlays and logs: 'the "Save" button inside dialog "Edit customer"'."""
    if loc is None:
        return "the page"
    if isinstance(loc, str):
        return "the active dialog" if loc == "active-dialog" else "the page"
    if loc.any:
        return describe_locator(loc.any[0])
    if loc.role and loc.name:
        base = f'the "{_match_text(loc.name)}" {loc.role}'
    elif loc.label:
        base = f'the "{_match_text(loc.label)}" field'
    elif loc.placeholder:
        base = f'the field "{_match_text(loc.placeholder)}"'
    elif loc.text:
        base = f'"{_match_text(loc.text)}"'
    elif loc.testid:
        base = f"[data-testid={loc.testid}]"
    elif loc.role:
        base = f"the {loc.role}"
    elif loc.id:
        base = f"#{loc.id}"
    else:
        base = loc.css or loc.xpath or "the element"
    if loc.within is None:
        return base
    if loc.within == "active-dialog":
        return f"{base} inside the active dialog"
    if loc.within == "page":
        return base
    return f"{base} inside {describe_locator(loc.within)}"


# ── Steps and goals ─────────────────────────────────────────────────────────

class RetryPolicy(_Strict):
    attempts: int = Field(default=2, ge=1, le=5)
    # None: inferred from the action (see REPEATABLE_ACTIONS). Clicks and key
    # presses can submit, so they are dispatched at most once unless true.
    safe_to_repeat: Optional[bool] = None


class Step(_Strict):
    id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    intent: str = Field(default="", max_length=200)
    action: Literal["navigate", "click", "type", "select", "check", "press", "hover", "scroll", "wait", "assert", "extract"]
    target: Optional[Locator] = None
    value: Optional[str] = Field(default=None, max_length=10000)
    url: Optional[str] = Field(default=None, max_length=2000)
    key: Optional[str] = Field(default=None, max_length=40)
    modifiers: list[Literal["ctrl", "alt", "shift", "meta"]] = Field(default_factory=list)
    checked: Optional[bool] = None
    clear: bool = True
    direction: Literal["down", "up"] = "down"
    fields: dict[str, Locator] = Field(default_factory=dict)
    expect: list[Expectation] = Field(default_factory=list)
    skip_if: list[Expectation] = Field(default_factory=list)
    timeout_s: Optional[float] = Field(default=None, gt=0, le=300)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    sensitive: Optional[bool] = None
    optional: bool = False

    @model_validator(mode="after")
    def _shape(self) -> "Step":
        a = self.action
        if a in NEEDS_TARGET and self.target is None:
            raise ValueError(f"step '{self.id}': '{a}' needs a target")
        if a in NEEDS_EXPECT and not self.expect:
            raise ValueError(f"step '{self.id}': '{a}' must declare 'expect' - the evidence that it worked")
        if a == "navigate" and not self.url:
            raise ValueError(f"step '{self.id}': navigate needs 'url'")
        if a in ("type", "select") and self.value is None:
            raise ValueError(f"step '{self.id}': {a} needs 'value'")
        if a == "check" and self.checked is None:
            raise ValueError(f"step '{self.id}': check needs 'checked': true/false")
        if a == "press" and not self.key:
            raise ValueError(f"step '{self.id}': press needs 'key'")
        if a == "extract" and not self.fields:
            raise ValueError(f"step '{self.id}': extract needs 'fields'")
        return self

    @property
    def repeatable(self) -> bool:
        if self.retry.safe_to_repeat is not None:
            return self.retry.safe_to_repeat
        return self.action in REPEATABLE_ACTIONS

    def label(self) -> str:
        if self.intent:
            return self.intent
        if self.action == "navigate":
            return f"Open {self.url}"
        return f"{self.action.capitalize()} {describe_locator(self.target)}"


class Interrupt(_Strict):
    """A known popup that may appear at any time (cookie banner, promo modal)."""
    id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    when: list[Expectation] = Field(min_length=1)
    do: Step
    max_times: int = Field(default=3, ge=1, le=20)


class InputSpec(_Strict):
    description: str = Field(default="", max_length=200)
    default: Optional[str] = Field(default=None, max_length=4000)
    secret: bool = False


class GoalSpec(_Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,119}$")
    title: str = Field(max_length=200)
    description: str = Field(default="", max_length=2000)
    start_url: Optional[str] = Field(default=None, max_length=2000)
    allowed_origins: list[str] = Field(min_length=1, max_length=20)
    inputs: dict[str, InputSpec] = Field(default_factory=dict)
    steps: list[Step] = Field(min_length=1, max_length=200)
    success: list[Expectation] = Field(min_length=1)
    interrupts: list[Interrupt] = Field(default_factory=list)
    check_success_first: bool = True
    max_duration_s: int = Field(default=600, ge=10, le=7200)

    @field_validator("allowed_origins")
    @classmethod
    def _origins(cls, v: list[str]) -> list[str]:
        out = []
        for o in v:
            o = o.strip().rstrip("/").lower()
            if not _ORIGIN.match(o):
                raise ValueError(f"'{o}' is not an origin like https://example.com")
            out.append(o)
        return out

    @model_validator(mode="after")
    def _consistent(self) -> "GoalSpec":
        ids = [s.id for s in self.steps]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate step ids: {sorted(dupes)}")
        for url in [self.start_url, *(s.url for s in self.steps if s.url)]:
            if url and not _TEMPLATE.search(url) and origin_of(url) not in self.allowed_origins:
                raise ValueError(f"{url} is outside allowed_origins {self.allowed_origins}")
        return self

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "description": self.description,
            "start_url": self.start_url, "allowed_origins": self.allowed_origins,
            "steps": len(self.steps),
            "inputs": {k: {"description": i.description, "default": None if i.secret else i.default,
                           "secret": i.secret, "required": i.default is None}
                       for k, i in self.inputs.items()},
        }


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}".lower()


# ── Loading and rendering ───────────────────────────────────────────────────

class GoalError(ValueError):
    pass


def render(spec: GoalSpec, provided: dict[str, str]) -> tuple[GoalSpec, dict[str, str], set[str]]:
    """Substitute {{inputs.x}} and re-validate. Returns (spec, inputs, secret values)."""
    unknown = set(provided) - set(spec.inputs)
    if unknown:
        raise GoalError(f"unknown inputs: {sorted(unknown)}")
    values: dict[str, str] = {}
    for name, inp in spec.inputs.items():
        v = provided.get(name) or inp.default
        if v is None:
            raise GoalError(f"input '{name}' is required")
        values[name] = v
    secrets = {values[n] for n, i in spec.inputs.items() if i.secret and values.get(n)}

    def sub(obj: Any) -> Any:
        if isinstance(obj, str):
            def rep(m: re.Match) -> str:
                key = m.group(1)
                if key not in values:
                    raise GoalError(f"template refers to undeclared input '{key}'")
                return values[key]
            return _TEMPLATE.sub(rep, obj)
        if isinstance(obj, list):
            return [sub(x) for x in obj]
        if isinstance(obj, dict):
            return {k: sub(v) for k, v in obj.items()}
        return obj

    # Expectations dump as {kind, args}, which the Expectation validator accepts back.
    raw = sub(spec.model_dump(mode="json", exclude_none=True))
    try:
        return GoalSpec.model_validate(raw), values, secrets
    except ValidationError as e:
        raise GoalError(_format_validation(e)) from e


def load_goal_file(path: Path) -> GoalSpec:
    try:
        return GoalSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as e:
        raise GoalError(f"{path.name}: invalid JSON: {e}") from e
    except ValidationError as e:
        raise GoalError(f"{path.name}: {_format_validation(e)}") from e


def load_goals(directory: Path) -> tuple[dict[str, GoalSpec], list[str]]:
    goals: dict[str, GoalSpec] = {}
    errors: list[str] = []
    if not directory.exists():
        return goals, errors
    for path in sorted(directory.glob("*.json")):
        try:
            spec = load_goal_file(path)
        except GoalError as e:
            errors.append(str(e))
            continue
        if spec.id in goals:
            errors.append(f"{path.name}: duplicate goal id '{spec.id}'")
            continue
        goals[spec.id] = spec
    return goals, errors


def _format_validation(e: ValidationError) -> str:
    parts = []
    for err in e.errors()[:5]:
        loc = ".".join(str(x) for x in err["loc"])
        parts.append(f"{loc}: {err['msg']}" if loc else err["msg"])
    more = f" (+{e.error_count() - 5} more)" if e.error_count() > 5 else ""
    return "; ".join(parts) + more
