"""Safety policy: authorized origins, rate limiting, and the confirmation gate.

Rate limiting and the domain blacklist come from the platform's SafetyCheck.
Its whitelist does substring matching ("example.com" admits
"evil-example.com"), so authorized origins are checked here by exact origin.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Optional

from ai_web_auto_backend.ai.agent import SafetyCheck

from .goals import origin_of

_DESTRUCTIVE = re.compile(
    r"\b(delete|remove|erase|destroy|discard|wipe|purge|revoke|deactivate|terminate|"
    r"close (my )?account|unsubscribe|cancel (my )?(subscription|order|plan|membership)|reset|empty trash)\b", re.I)
_FINANCIAL = re.compile(
    r"\b(pay|payment|purchase|buy|checkout|check out|place (an )?order|order now|donate|transfer|withdraw|"
    r"subscribe|upgrade|confirm payment|add funds|top up|book now)\b", re.I)
_OUTWARD = re.compile(r"\b(send|post|publish|share|invite|tweet|reply|comment|submit application|apply now)\b", re.I)


@dataclass
class Sensitivity:
    category: str  # destructive | financial | outward | credential | declared
    reason: str


def classify(action: str, target: dict[str, Any] | None, declared: Optional[bool]) -> Optional[Sensitivity]:
    """Decide whether an action needs a human's explicit approval.

    `target` is the element context the page library returns from locate():
    accessible name, role, containing dialog, and form facts.
    """
    if declared is True:
        return Sensitivity("declared", "the goal marks this step as sensitive")
    t = target or {}
    form = t.get("form") or {}
    if action == "type":
        if t.get("type") == "password":
            return Sensitivity("credential", "typing into a password field")
        if t.get("payment_field"):
            return Sensitivity("financial", "typing into a payment field")
        return None
    if action not in ("click", "press", "select", "check"):
        return None
    if action == "click" and str(t.get("opens_popup") or "false") not in ("false", ""):
        # "Delete…" that opens a confirmation dialog only opens UI; the commit
        # button inside that dialog is what gets confirmed.
        return None
    name = str(t.get("name") or t.get("text") or "")
    # Generic confirm buttons take their meaning from the dialog that asks.
    context = name
    if t.get("dialog_role") == "alertdialog" or re.fullmatch(r"\s*(ok|yes|confirm|continue|proceed)\s*", name, re.I):
        context = f"{name} {t.get('dialog_name') or ''}"
    for pattern, category in ((_DESTRUCTIVE, "destructive"), (_FINANCIAL, "financial"), (_OUTWARD, "outward")):
        m = pattern.search(context)
        if m:
            return Sensitivity(category, f"'{m.group(0)}' looks {category}")
    if form.get("has_payment_fields") and (t.get("submits") or action == "press"):
        return Sensitivity("financial", "submits a form with payment fields")
    return None


class Safety:
    def __init__(self, cfg_safety: dict[str, Any]):
        self.require_confirmation = bool(cfg_safety.get("require_confirmation", True))
        self.allow_spec_skip = bool(cfg_safety.get("allow_spec_to_skip_confirmation", False))
        self.check = SafetyCheck(
            require_confirmation=self.require_confirmation,
            domain_blacklist=list(cfg_safety.get("domain_blacklist", [])),
            max_actions_per_minute=int(cfg_safety.get("max_actions_per_minute", 60)),
        )

    def needs_confirmation(self, action: str, target: dict | None, declared: Optional[bool]) -> Optional[Sensitivity]:
        if not self.require_confirmation:
            return None
        if declared is False and self.allow_spec_skip:
            return None
        return classify(action, target, declared)

    @staticmethod
    def origin_allowed(url: str, allowed: list[str]) -> bool:
        return origin_of(url) in allowed

    def blacklisted(self, url: str) -> bool:
        return bool(self.check.domain_blacklist) and not self.check.check_domain(url)

    async def rate_limit(self, checkpoint) -> None:
        """Block (cooperatively) until another action fits in the per-minute budget."""
        while not self.check.check_rate_limit():
            await checkpoint()
            await asyncio.sleep(1.0)
