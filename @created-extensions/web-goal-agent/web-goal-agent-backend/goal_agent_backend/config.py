"""Backend configuration: defaults, file loading, and token provisioning.

Resolution order for the config file:
  1. --config CLI flag
  2. WGA_CONFIG environment variable (the E2E suite uses this, and Chrome
     passes its environment down to the native host and the backend it starts)
  3. web-goal-agent-backend/config.json (created by install.bat)

Relative paths inside the file resolve against the file's own directory.
"""

from __future__ import annotations

import copy
import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import BACKEND_DIR

EXTENSION_ID = "lfejjmliceaahhcnbdlmafhklbehnpab"   # fixed by the "key" in manifest.json
DEFAULT_CONFIG_PATH = BACKEND_DIR / "config.json"

DEFAULTS: dict[str, Any] = {
    "host": "127.0.0.1",
    "port": 8766,
    "token": "",
    "extension_ids": [EXTENSION_ID],
    "goals_dir": "goals",
    "runs_dir": "runs",
    "state_dir": "state",
    # Only applies when the native host started the backend: it exits after
    # this long with no extension connected and no run active. 0 disables.
    "idle_shutdown_minutes": 30,
    "safety": {
        "require_confirmation": True,
        "max_actions_per_minute": 60,
        "domain_blacklist": [],
        # A goal spec may mark a step "sensitive": false. That only skips the
        # confirmation gate when this is true.
        "allow_spec_to_skip_confirmation": False,
    },
    "timeouts": {
        "command_s": 20,
        "locate_s": 10,
        "verify_s": 10,
        "settle_s": 4,
        "reconnect_grace_s": 45,
        "confirmation_s": 600,
    },
    "ai": {
        "model": "claude-opus-5-5",
        "effort": "medium",
        "max_steps": 30,
        "max_tokens": 16000,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


@dataclass
class Config:
    path: Path
    data: dict[str, Any] = field(default_factory=dict)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    @property
    def host(self) -> str:
        return self.data["host"]

    @property
    def port(self) -> int:
        return int(self.data["port"])

    @property
    def token(self) -> str:
        return self.data["token"]

    @property
    def allowed_origins(self) -> list[str]:
        return [f"chrome-extension://{ext_id}" for ext_id in self.data["extension_ids"]]

    def resolve(self, key: str) -> Path:
        p = Path(self.data[key])
        return p if p.is_absolute() else (self.path.parent / p).resolve()

    @property
    def goals_dirs(self) -> list[Path]:
        """`goals_dir` may be one path or a list (the E2E config adds its test-only goals)."""
        raw = self.data["goals_dir"]
        paths = [Path(p) for p in (raw if isinstance(raw, list) else [raw])]
        return [p if p.is_absolute() else (self.path.parent / p).resolve() for p in paths]

    @property
    def runs_dir(self) -> Path:
        return self.resolve("runs_dir")

    @property
    def state_dir(self) -> Path:
        return self.resolve("state_dir")

    def timeout(self, name: str) -> float:
        return float(self.data["timeouts"][name])


def config_path(explicit: str | os.PathLike | None = None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    env = os.environ.get("WGA_CONFIG")
    if env:
        return Path(env).resolve()
    return DEFAULT_CONFIG_PATH


def load_config(explicit: str | os.PathLike | None = None, *, provision_token: bool = True) -> Config:
    """Load config, filling defaults. Generates and persists a token if missing."""
    path = config_path(explicit)
    user: dict[str, Any] = {}
    if path.exists():
        user = json.loads(path.read_text(encoding="utf-8"))
    data = _deep_merge(DEFAULTS, user)
    cfg = Config(path=path, data=data)
    placeholder = str(cfg.token).startswith("<")  # copied from config.example.json
    if provision_token and (not cfg.token or placeholder):
        user["token"] = secrets.token_urlsafe(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(user, indent=2) + "\n", encoding="utf-8")
        cfg.data["token"] = user["token"]
    return cfg
