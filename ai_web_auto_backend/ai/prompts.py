"""Prompt templates for the AI decision-making layer."""

from __future__ import annotations

from typing import Any


class PromptBuilder:
    SYSTEM = (
        "You are a web automation expert controlling a browser via API. "
        "You analyze the current page state and decide the next action to take. "
        "Always return valid JSON. Never guess selectors - use the provided DOM data. "
        "Prefer reusing selectors that already worked in the action history. "
        "Every action should have an observable verification goal. "
        "If the task is complete, return action: \"done\"."
    )

    ACTION_TEMPLATE = """\
CURRENT STATE:
- URL: {url}
- Page Title: {title}
- Available Interactive Elements:
{elements}

TASK: {objective}

PREVIOUS ACTIONS ({step}/{max_steps} steps taken):
{history}

DOM CHANGES SINCE LAST ACTION:
{dom_changes}

RULES:
1. Pick the single best next action with a clear expected outcome.
2. Reuse a previously successful selector from history before proposing a new one.
3. Prefer CSS selectors backed by stable id, name, aria-label, placeholder, or role patterns; fall back to XPath only when it is clearly more stable.
4. For typing, always specify clear_first: true unless appending.
5. If the previous action failed, do not repeat the exact same selector more than once without changing strategy.
6. Prefer "wait" for async loading or generation and "extract" for text or state checks before asking for an extra screenshot.
7. If a confirmation dialog, login wall, or CAPTCHA appears, return action: "wait" so a human can intervene.
8. If the objective is achieved, return action: "done" with extracted data in params.result.
9. Use DOM_CHANGES to verify whether previous actions succeeded. Loading states, hidden/shown elements, and text updates tell you what actually happened — not just what the screenshot shows.
10. If DOM_CHANGES shows a loading state was applied, wait for it to clear before proceeding.
11. If DOM_CHANGES shows an error state, change strategy rather than repeating.

Return JSON only - no markdown fences, no commentary:
{{
  "reasoning": "brief explanation",
  "action": "click|type|scroll|screenshot|extract|wait|navigate|done",
  "target": {{"method": "css|xpath", "value": "selector"}},
  "params": {{}},
  "expected_outcome": "what should happen next",
  "fallback_action": {{}}
}}"""

    @classmethod
    def build_action_prompt(
        cls,
        url: str,
        title: str,
        interactive_elements: list[dict[str, Any]],
        objective: str,
        action_history: list[dict[str, Any]],
        step: int,
        max_steps: int,
        dom_changes: str = "",
    ) -> str:
        elements_str = cls._format_elements(interactive_elements)
        history_str = cls._format_history(action_history)
        return cls.ACTION_TEMPLATE.format(
            url=url,
            title=title,
            elements=elements_str or "  (none detected - try scrolling or taking a screenshot)",
            objective=objective,
            history=history_str or "  (none yet)",
            step=step,
            max_steps=max_steps,
            dom_changes=dom_changes or "  (no observation active)",
        )

    @staticmethod
    def _format_elements(elements: list[dict[str, Any]], limit: int = 80) -> str:
        if not elements:
            return ""
        lines = []
        for i, el in enumerate(elements[:limit]):
            tag = el.get("tag", "?")
            text = (el.get("text") or "")[:60]
            selector = el.get("selector", "")
            el_type = el.get("type", "")
            line = f"  [{i}] <{tag}> {f'type={el_type} ' if el_type else ''}text=\"{text}\" selector=\"{selector}\""
            lines.append(line)
        if len(elements) > limit:
            lines.append(f"  ... and {len(elements) - limit} more elements")
        return "\n".join(lines)

    @staticmethod
    def _format_history(history: list[dict[str, Any]], limit: int = 15) -> str:
        if not history:
            return ""
        recent = history[-limit:]
        lines = []
        for i, h in enumerate(recent):
            action = h.get("action", "?")
            target = h.get("target", {}).get("value", "")
            status = h.get("status", "?")
            lines.append(f"  {i+1}. {action} -> {target} [{status}]")
        return "\n".join(lines)
