"""AI agent that drives browser automation via Claude."""

from __future__ import annotations

import json
import time
from typing import Any, Optional

import anthropic
from loguru import logger
from pydantic import ValidationError

from ..core.models import Command, Response, ResponseStatus, ResponseType, ActionType, TaskResult
from ..core.server import WebSocketServer
from ..core.state import Task
from .prompts import PromptBuilder


DEFAULT_MODEL = "claude-sonnet-5-5"
# Policy declines are re-run server-side on Anthropic's recommended fallback model.
REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Actions that require human confirmation before execution
DESTRUCTIVE_ACTIONS = {"form_submit", "download", "execute_script"}
SENSITIVE_KEYWORDS = {"delete", "remove", "purchase", "buy", "pay", "submit order", "confirm payment"}


class SafetyCheck:
    def __init__(self, require_confirmation: bool = True, domain_whitelist: list[str] | None = None,
                 domain_blacklist: list[str] | None = None, max_actions_per_minute: int = 10):
        self.require_confirmation = require_confirmation
        self.domain_whitelist = domain_whitelist
        self.domain_blacklist = domain_blacklist or []
        self.max_actions_per_minute = max_actions_per_minute
        self._action_timestamps: list[float] = []

    def check_rate_limit(self) -> bool:
        now = time.time()
        self._action_timestamps = [t for t in self._action_timestamps if now - t < 60]
        if len(self._action_timestamps) >= self.max_actions_per_minute:
            return False
        self._action_timestamps.append(now)
        return True

    def check_domain(self, url: str) -> bool:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
        if self.domain_blacklist and any(b in domain for b in self.domain_blacklist):
            return False
        if self.domain_whitelist is not None and not any(w in domain for w in self.domain_whitelist):
            return False
        return True

    def needs_confirmation(self, action: str, params: dict) -> bool:
        if not self.require_confirmation:
            return False
        if action in DESTRUCTIVE_ACTIONS:
            return True
        text = json.dumps(params).lower()
        return any(kw in text for kw in SENSITIVE_KEYWORDS)


class AIAgent:
    def __init__(
        self,
        server: WebSocketServer,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        safety: SafetyCheck | None = None,
        effort: str = "medium",
        refusal_fallback: bool = True,
    ):
        self.server = server
        self.model = model
        self.effort = effort
        self.refusal_fallback = refusal_fallback
        # Async client: with thinking on, one decision can take tens of seconds, and a
        # blocking call would stall the WebSocket loop (pings, extension responses).
        self.client = anthropic.AsyncAnthropic(api_key=api_key) if api_key else anthropic.AsyncAnthropic()
        self.safety = safety or SafetyCheck()
        self._confirmation_callback: Optional[Any] = None
        self.last_error: Optional[str] = None

    def set_confirmation_callback(self, callback):
        """Set a callable(action_dict) -> bool for human-in-the-loop confirmation."""
        self._confirmation_callback = callback

    async def execute_task(self, task: Task) -> TaskResult:
        start_ms = int(time.time() * 1000)
        screenshots: list[str] = []

        # Navigate to start URL if provided
        if task.start_url:
            resp = await self.server.navigate(task.start_url)
            if resp.status == ResponseStatus.FAILURE:
                return TaskResult(
                    task_id=task.id, objective=task.objective, success=False,
                    error=f"Failed to navigate to {task.start_url}: {resp.error}",
                )
            # Wait for page load
            await self.server.wait_for("network_idle", timeout_ms=5000)

        # Get initial state and start DOM observation
        await self.server.screenshot()
        await self.server.extract("text")
        await self.server.observe_start()

        for step in range(task.max_steps):
            task.steps_taken = step + 1
            state = self.server.session

            # Domain safety check
            if state.browser.url and not self.safety.check_domain(state.browser.url):
                return TaskResult(
                    task_id=task.id, objective=task.objective, success=False,
                    steps_taken=step + 1, error=f"Domain blocked: {state.browser.url}",
                )

            # Rate limiting
            if not self.safety.check_rate_limit():
                logger.warning("Rate limit reached, waiting...")
                import asyncio
                await asyncio.sleep(6)

            # Ask Claude for next action
            action_dict = await self._decide_next_action(task, step)
            if not action_dict:
                return TaskResult(
                    task_id=task.id, objective=task.objective, success=False,
                    steps_taken=step + 1, error=f"AI failed to decide next action: {self.last_error}",
                )

            action = action_dict.get("action", "")
            logger.info(f"Step {step+1}: {action} — {action_dict.get('reasoning', '')}")

            # Check if done
            if action == "done":
                await self.server.observe_stop()
                result_data = action_dict.get("params", {}).get("result", {})
                return TaskResult(
                    task_id=task.id, objective=task.objective, success=True,
                    steps_taken=step + 1, data=result_data, screenshots=screenshots,
                    duration_ms=int(time.time() * 1000) - start_ms,
                )

            # Safety confirmation
            if self.safety.needs_confirmation(action, action_dict.get("params", {})):
                if self._confirmation_callback:
                    if not self._confirmation_callback(action_dict):
                        logger.info("Action rejected by human")
                        task.action_history.append({**action_dict, "status": "rejected"})
                        continue
                else:
                    logger.warning(f"Skipping action requiring confirmation (no callback): {action}")
                    task.action_history.append({**action_dict, "status": "skipped_no_confirmation"})
                    continue

            # Execute the action
            resp = await self._execute_action(action_dict)
            status = "success" if resp.status == ResponseStatus.SUCCESS else "failure"
            task.action_history.append({**action_dict, "status": status})

            # Capture screenshot after each action for AI context
            if action != "screenshot":
                ss_resp = await self.server.screenshot()
                if ss_resp.data.screenshot:
                    screenshots.append(ss_resp.data.screenshot)

            # If action failed, try fallback
            if resp.status == ResponseStatus.FAILURE and action_dict.get("fallback_action"):
                logger.info("Primary action failed, trying fallback...")
                fb = action_dict["fallback_action"]
                if isinstance(fb, dict) and fb.get("action"):
                    fb_resp = await self._execute_action(fb)
                    task.action_history.append({**fb, "status": "success" if fb_resp.status == ResponseStatus.SUCCESS else "failure"})

        await self.server.observe_stop()
        return TaskResult(
            task_id=task.id, objective=task.objective, success=False,
            steps_taken=task.max_steps, screenshots=screenshots,
            error=f"Max steps ({task.max_steps}) reached without completing objective",
            duration_ms=int(time.time() * 1000) - start_ms,
        )

    async def _decide_next_action(self, task: Task, step: int) -> dict[str, Any] | None:
        state = self.server.session

        # Get DOM diff since last action (structured change analysis)
        dom_changes = ""
        try:
            diff_resp = await self.server.observe_diff()
            if diff_resp.status == ResponseStatus.SUCCESS:
                dom_changes = diff_resp.data.extracted_data.get("summary", "")
        except Exception:
            pass  # non-critical — fall back to no DOM data

        # The session's element list is only filled from dom_snapshot responses, which
        # the extension doesn't send, so ask it for the interactive elements directly.
        elements = state.browser.interactive_elements
        try:
            found = await self.server.send_command(Command(action=ActionType.DISCOVER))
            if found.status == ResponseStatus.SUCCESS:
                elements = found.data.extracted_data.get("elements") or elements
        except Exception:
            pass  # non-critical - decide from the screenshot alone

        prompt = PromptBuilder.build_action_prompt(
            url=state.browser.url,
            title=state.browser.title,
            interactive_elements=elements,
            objective=task.objective,
            action_history=task.action_history,
            step=step,
            max_steps=task.max_steps,
            dom_changes=dom_changes,
        )

        messages = [{"role": "user", "content": prompt}]

        # Include screenshot if available
        if state.browser.last_screenshot:
            messages = [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": state.browser.last_screenshot}},
                {"type": "text", "text": prompt},
            ]}]

        request: dict[str, Any] = {
            "model": self.model,
            # Thinking is on by default and counts toward max_tokens.
            "max_tokens": 16000,
            "system": PromptBuilder.SYSTEM,
            "messages": messages,
            "output_config": {"effort": self.effort},
        }
        try:
            if self.refusal_fallback:
                # The installed SDK predates the `fallbacks` parameter; it travels in extra_body.
                response = await self.client.beta.messages.create(
                    **request, betas=[REFUSAL_FALLBACK_BETA], extra_body={"fallbacks": "default"},
                )
            else:
                response = await self.client.messages.create(**request)
        except anthropic.AuthenticationError as e:
            return self._fail(f"Anthropic authentication failed: {e.message}")
        except anthropic.NotFoundError as e:
            return self._fail(f"model {self.model!r} is not available: {e.message}")
        except anthropic.RateLimitError:
            return self._fail("Anthropic rate limit reached")
        except anthropic.APIStatusError as e:
            return self._fail(f"Anthropic API error {e.status_code}: {e.message}")
        except anthropic.APIConnectionError:
            return self._fail("could not reach the Anthropic API")

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            return self._fail(f"the model declined this step (category: {getattr(details, 'category', None)})")
        # Responses can start with thinking blocks: read the text block by type, not position.
        text = next((b.text for b in response.content if b.type == "text"), "").strip()
        if not text:
            reason = "it ran out of output tokens" if response.stop_reason == "max_tokens" else "it was empty"
            return self._fail(f"the model's reply had no text ({reason})")
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        try:
            decision = json.loads(text)
        except json.JSONDecodeError:
            return self._fail(f"the model did not return JSON: {text[:200]!r}")
        if not isinstance(decision, dict):
            return self._fail("the model's JSON was not an object")
        self.last_error = None
        return decision

    def _fail(self, message: str) -> None:
        logger.error(f"AI decision error: {message}")
        self.last_error = message
        return None

    async def _execute_action(self, action_dict: dict[str, Any]) -> Response:
        action = action_dict.get("action", "")
        target = action_dict.get("target", {})
        params = action_dict.get("params", {})

        try:
            cmd = Command(
                action=action,
                target=target,
                params=params,
            )
        except ValidationError:
            # Recorded as a failed step so the model can correct itself next turn.
            return Response(type=ResponseType.ERROR, status=ResponseStatus.FAILURE,
                            error=f"invalid action from the model: {action!r}")
        return await self.server.send_command(cmd)
