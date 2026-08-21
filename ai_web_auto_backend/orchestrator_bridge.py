"""
Orchestrator command-file bridge.

Turns the running backend into something an external "brain" (Claude / a script)
can drive without a live terminal or extra network port. The brain writes a
command JSON into the inbox directory; the bridge executes it against the
connected Chrome extension and writes the result JSON (plus any screenshot PNG)
into the outbox directory. A rolling state.json reflects connection + activity.

Protocol
--------
inbox/<seq>.json   (written by the brain)
    { "action": "navigate", "params": {"url": "https://example.com"} }
    Optional top-level keys: "target", "timeout".

outbox/<seq>.json  (written by the bridge)
    { "seq": 12, "action": "navigate", "status": "success",
      "error": null, "data": {...}, "screenshot": "outbox/12.png"|null,
      "took_ms": 812, "ts": "..." }

state.json         (rewritten after every command + on connect/disconnect)
    { "connected": true, "url": "...", "title": "...",
      "last_seq": 12, "last_action": "navigate", "updated": "..." }

The bridge processes inbox files in ascending numeric order, deletes each after
handling it, and never blocks the server's own event loop for long — every
command is awaited with the command's own timeout.

Run standalone against an already-running server instance is not possible
(only one process can own the extension socket). Instead this is started in the
SAME process as the server — see run_server_with_bridge.py.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from .core.models import Command
from .core.server import WebSocketServer


DEFAULT_ROOT = Path("orchestrator")
_SEQ_RE = re.compile(r"^(\d+)\.json$")


class OrchestratorBridge:
    def __init__(self, server: WebSocketServer, root: str | Path = DEFAULT_ROOT,
                 poll_interval: float = 0.25):
        self.server = server
        self.root = Path(root)
        self.inbox = self.root / "inbox"
        self.outbox = self.root / "outbox"
        self.poll_interval = poll_interval
        self._running = False
        self._processed: set[int] = set()

    # ── Lifecycle ────────────────────────────────────────────────────────

    def _ensure_dirs(self):
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.outbox.mkdir(parents=True, exist_ok=True)

    async def run(self):
        """Main loop — poll the inbox and process commands as they appear."""
        self._ensure_dirs()
        self._running = True
        self._write_state()
        logger.info(f"Orchestrator bridge watching {self.inbox.resolve()}")
        while self._running:
            try:
                await self._drain_inbox()
            except Exception as e:  # never let the loop die
                logger.error(f"Bridge loop error: {e}")
            await asyncio.sleep(self.poll_interval)

    def stop(self):
        self._running = False

    # ── Inbox processing ─────────────────────────────────────────────────

    async def _drain_inbox(self):
        pending = []
        for p in self.inbox.glob("*.json"):
            m = _SEQ_RE.match(p.name)
            if not m:
                continue
            seq = int(m.group(1))
            if seq in self._processed:
                continue
            pending.append((seq, p))
        pending.sort(key=lambda t: t[0])
        for seq, path in pending:
            await self._process_one(seq, path)

    async def _process_one(self, seq: int, path: Path):
        # Read the command; a half-written file may fail to parse — retry next tick.
        try:
            raw = path.read_text(encoding="utf-8")
            spec = json.loads(raw)
        except (json.JSONDecodeError, OSError):
            return  # not fully written yet; try again next poll

        self._processed.add(seq)
        action = spec.get("action", "")
        started = time.monotonic()
        result: dict[str, Any] = {
            "seq": seq, "action": action, "status": "failure",
            "error": None, "data": {}, "screenshot": None,
        }

        try:
            if not self.server.session.connected:
                raise ConnectionError("Extension not connected")

            cmd = Command(
                action=action,
                target=spec.get("target", {"method": "css", "value": ""}),
                params=spec.get("params", {}),
                timeout=spec.get("timeout", 30000),
            )
            resp = await self.server.send_command(cmd)
            result["status"] = resp.status.value if hasattr(resp.status, "value") else str(resp.status)
            result["error"] = resp.error
            data = resp.data.model_dump(mode="json") if resp.data else {}

            # Pull screenshots out to a sibling PNG so the brain can view them.
            shot = data.pop("screenshot", None)
            if shot:
                png = self.outbox / f"{seq}.png"
                try:
                    png.write_bytes(base64.b64decode(shot))
                    result["screenshot"] = str(png)
                except Exception as e:
                    logger.warning(f"Could not save screenshot for seq {seq}: {e}")

            # Same for base64 asset payloads from download_asset(return_data=True).
            ed = data.get("extracted_data", {})
            if isinstance(ed, dict) and ed.get("b64"):
                asset = self.outbox / f"{seq}.asset"
                try:
                    asset.write_bytes(base64.b64decode(ed["b64"]))
                    ed["saved_to"] = str(asset)
                    ed["b64"] = f"<{ed.get('size', 0)} bytes saved to {asset.name}>"
                except Exception as e:
                    logger.warning(f"Could not save asset for seq {seq}: {e}")

            result["data"] = data
        except Exception as e:
            result["status"] = "failure"
            result["error"] = str(e)

        result["took_ms"] = int((time.monotonic() - started) * 1000)
        result["ts"] = datetime.now(timezone.utc).isoformat()

        # Write result then remove the inbox file.
        (self.outbox / f"{seq}.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        try:
            path.unlink()
        except OSError:
            pass

        self._write_state(last_seq=seq, last_action=action)
        logger.info(f"[bridge] seq={seq} {action} -> {result['status']} ({result['took_ms']}ms)")

    # ── State file ───────────────────────────────────────────────────────

    def _write_state(self, last_seq: int | None = None, last_action: str | None = None):
        b = self.server.session.browser
        state = {
            "connected": self.server.session.connected,
            "url": b.url,
            "title": b.title,
            "last_seq": last_seq,
            "last_action": last_action,
            "updated": datetime.now(timezone.utc).isoformat(),
        }
        try:
            (self.root / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
        except OSError:
            pass
