"""Session state and task queue management."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from loguru import logger


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    objective: str = ""
    status: TaskStatus = TaskStatus.QUEUED
    start_url: str = ""
    max_steps: int = 50
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    steps_taken: int = 0
    action_history: list[dict[str, Any]] = field(default_factory=list)
    result_data: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class BrowserState:
    url: str = ""
    title: str = ""
    cookies: list[dict[str, Any]] = field(default_factory=list)
    localStorage: dict[str, str] = field(default_factory=dict)
    sessionStorage: dict[str, str] = field(default_factory=dict)
    scroll_position: dict[str, int] = field(default_factory=lambda: {"x": 0, "y": 0})
    viewport_size: dict[str, int] = field(default_factory=lambda: {"width": 1920, "height": 1080})
    last_screenshot: Optional[str] = None
    last_dom_snapshot: Optional[dict] = None
    interactive_elements: list[dict[str, Any]] = field(default_factory=list)


class SessionState:
    def __init__(self):
        self.browser = BrowserState()
        self.connected = False
        self.connection_time: Optional[str] = None
        self.auth_token: Optional[str] = None
        self._lock = asyncio.Lock()

    async def update_from_response(self, data: dict[str, Any]):
        async with self._lock:
            dom = data.get("dom_snapshot")
            if dom:
                self.browser.url = dom.get("url", self.browser.url)
                self.browser.title = dom.get("title", self.browser.title)
                self.browser.last_dom_snapshot = dom
                self.browser.interactive_elements = dom.get("interactive_elements", [])
            if data.get("screenshot"):
                self.browser.last_screenshot = data["screenshot"]
            if data.get("cookies"):
                self.browser.cookies = data["cookies"]
            if data.get("localStorage"):
                self.browser.localStorage = data["localStorage"]
            if data.get("sessionStorage"):
                self.browser.sessionStorage = data["sessionStorage"]

    async def update_from_event(self, event_type: str, data: dict[str, Any]):
        async with self._lock:
            if event_type == "page_load":
                self.browser.url = data.get("url", self.browser.url)
                self.browser.title = data.get("title", self.browser.title)

    def set_connected(self, connected: bool):
        self.connected = connected
        if connected:
            self.connection_time = datetime.now(timezone.utc).isoformat()
        else:
            self.connection_time = None

    def get_page_summary(self) -> dict[str, Any]:
        return {
            "url": self.browser.url,
            "title": self.browser.title,
            "interactive_elements_count": len(self.browser.interactive_elements),
            "has_screenshot": self.browser.last_screenshot is not None,
            "viewport": self.browser.viewport_size,
        }

    def save_session(self) -> dict[str, Any]:
        return {
            "url": self.browser.url,
            "cookies": self.browser.cookies,
            "localStorage": self.browser.localStorage,
            "sessionStorage": self.browser.sessionStorage,
            "scroll_position": self.browser.scroll_position,
        }


class TaskQueue:
    def __init__(self):
        self._queue: asyncio.Queue[Task] = asyncio.Queue()
        self._tasks: dict[str, Task] = {}
        self._current: Optional[Task] = None
        self._lock = asyncio.Lock()

    async def add(self, objective: str, start_url: str = "", max_steps: int = 50) -> Task:
        task = Task(objective=objective, start_url=start_url, max_steps=max_steps)
        self._tasks[task.id] = task
        await self._queue.put(task)
        logger.info(f"Task queued: {task.id} - {objective}")
        return task

    async def get_next(self) -> Optional[Task]:
        try:
            task = self._queue.get_nowait()
            async with self._lock:
                task.status = TaskStatus.RUNNING
                task.started_at = datetime.now(timezone.utc).isoformat()
                self._current = task
            logger.info(f"Task started: {task.id}")
            return task
        except asyncio.QueueEmpty:
            return None

    async def complete(self, task_id: str, result_data: dict[str, Any] | None = None, error: str | None = None):
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            task.completed_at = datetime.now(timezone.utc).isoformat()
            if error:
                task.status = TaskStatus.FAILED
                task.error = error
            else:
                task.status = TaskStatus.COMPLETED
                task.result_data = result_data or {}
            if self._current and self._current.id == task_id:
                self._current = None
            logger.info(f"Task {'completed' if not error else 'failed'}: {task_id}")

    async def cancel(self, task_id: str):
        async with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status in (TaskStatus.QUEUED, TaskStatus.RUNNING):
                task.status = TaskStatus.CANCELLED
                if self._current and self._current.id == task_id:
                    self._current = None

    def get_task(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    @property
    def current_task(self) -> Optional[Task]:
        return self._current

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()
