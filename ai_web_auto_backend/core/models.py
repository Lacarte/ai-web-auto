"""Pydantic models for the communication protocol."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


# ── Enums ────────────────────────────────────────────────────────────────────

class ActionType(str, Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    SCREENSHOT = "screenshot"
    SCROLL = "scroll"
    EXTRACT = "extract"
    HOVER = "hover"
    WAIT = "wait"
    EXECUTE_SCRIPT = "execute_script"
    FORM_SUBMIT = "form_submit"
    DOWNLOAD = "download"
    DRAG_DROP = "drag_drop"
    SELECT = "select"
    UPLOAD = "upload"
    KEY = "key"
    ACTIVATE_TAB = "activate_tab"
    LIST_TABS = "list_tabs"
    CLOSE_TAB = "close_tab"  # closes only the tab the order is bound to
    WHEEL = "wheel"  # trusted CDP mouse-wheel notches at a pointer position
    TYPE_CDP = "type_cdp"
    CLICK_CDP = "click_cdp"
    SET_DOWNLOAD_PATH = "set_download_path"
    DETACH = "detach"
    DISCOVER = "discover"
    FIND = "find"
    HOVER_CDP = "hover"
    OBSERVE_START = "observe_start"
    OBSERVE_DIFF = "observe_diff"
    OBSERVE_STOP = "observe_stop"


class TargetMethod(str, Enum):
    XPATH = "xpath"
    CSS = "css"
    COORDINATES = "coordinates"
    TEXT = "text"


class ResponseType(str, Enum):
    RESPONSE = "response"
    EVENT = "event"
    ERROR = "error"


class ResponseStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    PENDING = "pending"


class EventType(str, Enum):
    PAGE_LOAD = "page_load"
    DOM_MUTATION = "dom_mutation"
    DIALOG_OPENED = "dialog_opened"
    DOWNLOAD_STARTED = "download_started"
    ERROR = "error"
    CONSOLE_LOG = "console_log"


class WaitCondition(str, Enum):
    ELEMENT_VISIBLE = "element_visible"
    ELEMENT_HIDDEN = "element_hidden"
    NETWORK_IDLE = "network_idle"
    TIME = "time"


class ExtractType(str, Enum):
    TEXT = "text"
    HTML = "html"
    COOKIES = "cookies"
    LOCAL_STORAGE = "localStorage"
    SESSION_STORAGE = "sessionStorage"
    ATTRIBUTES = "attributes"


# ── Command Models (Python → Extension) ─────────────────────────────────────

class Target(BaseModel):
    method: TargetMethod = TargetMethod.CSS
    value: str = ""
    frame_index: int = 0


class Command(BaseModel):
    id: str = Field(default_factory=_uuid)
    timestamp: str = Field(default_factory=_utcnow)
    type: str = "command"
    action: ActionType
    target: Target = Field(default_factory=Target)
    params: dict[str, Any] = Field(default_factory=dict)
    priority: int = 1
    timeout: int = 30000

    def to_message(self) -> dict:
        return self.model_dump(mode="json")


# ── Response Models (Extension → Python) ─────────────────────────────────────

class DOMSnapshot(BaseModel):
    html: str = ""
    title: str = ""
    url: str = ""
    interactive_elements: list[dict[str, Any]] = Field(default_factory=list)


class ResponseData(BaseModel):
    screenshot: Optional[str] = None
    dom_snapshot: Optional[DOMSnapshot] = None
    extracted_data: dict[str, Any] = Field(default_factory=dict)
    tabs: list[dict[str, Any]] = Field(default_factory=list)
    tabId: Optional[int] = None  # the tab a navigate / activate_tab acted on
    cookies: list[dict[str, Any]] = Field(default_factory=list)
    localStorage: dict[str, str] = Field(default_factory=dict)
    sessionStorage: dict[str, str] = Field(default_factory=dict)
    execution_time_ms: int = 0


class Response(BaseModel):
    id: str = Field(default_factory=_uuid)
    correlation_id: str = ""
    timestamp: str = Field(default_factory=_utcnow)
    type: ResponseType = ResponseType.RESPONSE
    status: ResponseStatus = ResponseStatus.SUCCESS
    data: ResponseData = Field(default_factory=ResponseData)
    error: Optional[str] = None


class EventMessage(BaseModel):
    id: str = Field(default_factory=_uuid)
    timestamp: str = Field(default_factory=_utcnow)
    type: ResponseType = ResponseType.EVENT
    event: EventType
    data: dict[str, Any] = Field(default_factory=dict)


# ── Task Result ──────────────────────────────────────────────────────────────

class TaskResult(BaseModel):
    task_id: str
    objective: str
    success: bool
    steps_taken: int = 0
    data: dict[str, Any] = Field(default_factory=dict)
    screenshots: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    duration_ms: int = 0
