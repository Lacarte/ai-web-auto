"""Wire protocol shared with the extension.

Transport is the ai-web-auto protocol unchanged: `auth` -> `auth_response`,
then `command`/`response` pairs matched by correlation_id, plus `event`
messages. This module adds only what the goal agent needs on top:

- a protocol version, checked during auth (major must match)
- `ui_request` events (extension -> backend) with validated payloads,
  answered by a `ui_reply` push carrying the same request_id
- push events (backend -> extension): run_event, state_snapshot, shutting_down
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

PROTOCOL_VERSION = 1
APP_NAME = "web-goal-agent"
CLIENT_NAME = "web-goal-agent-extension"


def is_compatible(client_protocol: Any) -> bool:
    return isinstance(client_protocol, int) and client_protocol == PROTOCOL_VERSION


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartRunArgs(_Strict):
    tab_id: int
    goal_id: Optional[str] = Field(default=None, max_length=120)
    goal_text: Optional[str] = Field(default=None, max_length=4000)
    inputs: dict[str, str] = Field(default_factory=dict)
    # Origins the user granted in the popup; the AI planner may only act there.
    granted_origins: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("inputs")
    @classmethod
    def _bounded_inputs(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 50:
            raise ValueError("too many inputs")
        for key, value in v.items():
            if len(key) > 64 or len(value) > 4000:
                raise ValueError(f"input '{key[:64]}' is too long")
        return v


class ConfirmArgs(_Strict):
    run_id: str = Field(max_length=64)
    confirmation_id: str = Field(max_length=64)
    approved: bool


class RunRef(_Strict):
    run_id: Optional[str] = Field(default=None, max_length=64)


class Empty(_Strict):
    pass


UI_OPS: dict[str, type[BaseModel]] = {
    "get_state": Empty,
    "list_goals": Empty,
    "start_run": StartRunArgs,
    "pause_run": RunRef,
    "resume_run": RunRef,
    "stop_run": RunRef,
    "confirm": ConfirmArgs,
}


class UiRequest(_Strict):
    request_id: str = Field(max_length=64)
    op: Literal["get_state", "list_goals", "start_run", "pause_run", "resume_run", "stop_run", "confirm"]
    args: dict[str, Any] = Field(default_factory=dict)

    def parsed_args(self) -> BaseModel:
        return UI_OPS[self.op].model_validate(self.args)
