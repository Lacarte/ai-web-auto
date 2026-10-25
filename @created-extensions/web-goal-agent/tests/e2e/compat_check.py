"""Drive the Web Goal Agent extension with the UNMODIFIED ai-web-auto core server.

Proves protocol compatibility: the platform's WebSocketServer convenience
methods (navigate, wait_for, click, type_text, execute_script, scroll,
screenshot, extract) work against this extension. Run by run-e2e.mjs:

    python compat_check.py <test-config.json> <report.json> <tab_id>
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import warnings
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))
warnings.filterwarnings("ignore", category=DeprecationWarning)
# The native host probes /health, which the core server doesn't serve; keep its handshake noise out of the output.
logging.getLogger("websockets").setLevel(logging.CRITICAL)

from ai_web_auto_backend.core.server import WebSocketServer  # noqa: E402


async def main(config_path: str, report_path: str) -> int:
    cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
    server = WebSocketServer(host="127.0.0.1", port=cfg["port"], auth_token=cfg["token"])
    await server.start()
    checks: dict[str, object] = {}
    ok = False
    try:
        await server.wait_for_connection(timeout=40)
        r = await server.navigate("http://127.0.0.1:8777/crm.html?consent=0")
        checks["navigate"] = r.status.value
        r = await server.wait_for("element_visible", "#customers tbody tr", timeout_ms=15000)
        checks["wait_for"] = r.status.value
        r = await server.execute_script("document.title")
        checks["execute_script"] = r.data.extracted_data.get("result")
        r = await server.click("#noteBtn")
        checks["click"] = r.status.value
        r = await server.type_text("#noteText", "hello from the core API")
        checks["type_text"] = r.status.value
        r = await server.execute_script("document.querySelector('#noteText').value")
        checks["typed_value"] = r.data.extracted_data.get("result")
        r = await server.scroll("down", 300)
        checks["scroll"] = r.status.value
        r = await server.screenshot()
        checks["screenshot_bytes"] = len(r.data.screenshot or "")
        r = await server.extract("text")
        checks["extract_has_customers"] = "Grace Hopper" in r.data.extracted_data.get("text", "")
        ok = (checks["navigate"] == "success" and checks["wait_for"] == "success"
              and checks["execute_script"] == "Acme CRM — Customers" and checks["click"] == "success"
              and checks["typed_value"] == "hello from the core API" and checks["scroll"] == "success"
              and checks["screenshot_bytes"] > 1000 and checks["extract_has_customers"] is True)
    except Exception as e:  # report, don't hide
        checks["exception"] = repr(e)
    finally:
        await server.stop()
    Path(report_path).write_text(json.dumps({"ok": ok, "checks": checks}, indent=2), encoding="utf-8")
    print(json.dumps({"ok": ok, "checks": checks}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1], sys.argv[2])))
