"""
Start the WebSocket server AND the orchestrator bridge in one process.

This is the mode that lets an external brain (Claude) drive the browser:
  - the server owns the single Chrome-extension connection
  - the bridge watches ./orchestrator/inbox for command files and executes them

Usage:
    venv\\Scripts\\python -m ai_web_auto_backend.run_orchestrated
    (optionally)  --root <dir>   --port 8765   --token local-dev-token

Then the brain writes orchestrator/inbox/<n>.json and reads orchestrator/outbox/<n>.json.
Keep this running; Ctrl+C to stop.
"""

from __future__ import annotations

import argparse
import asyncio

from loguru import logger

from .core.server import WebSocketServer
from .orchestrator_bridge import OrchestratorBridge


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="orchestrator", help="Bridge command-file root dir")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--token", default=None)
    ap.add_argument("--wait-timeout", type=float, default=0.0,
                    help="If >0, wait this many seconds for the extension before starting the bridge")
    args = ap.parse_args()

    server = WebSocketServer(host=args.host, port=args.port, auth_token=args.token)
    await server.start()

    print("\n=== AI Web-Auto — Orchestrated Mode ===")
    print(f"Auth token: {server.auth_token}")
    print(f"Command bridge root: {args.root}/  (inbox -> outbox)")
    print("Waiting for the Chrome extension to connect...")
    print("The brain can now write orchestrator/inbox/<n>.json commands.\n")

    if args.wait_timeout > 0:
        try:
            await server.wait_for_connection(timeout=args.wait_timeout)
        except TimeoutError:
            logger.warning("Extension not connected yet — bridge will run and wait.")

    bridge = OrchestratorBridge(server, root=args.root)

    try:
        await bridge.run()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        bridge.stop()
        await server.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
