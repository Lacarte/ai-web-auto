"""
Tiny client for the orchestrator bridge — send one command, wait for its result.

The brain uses this to drive the browser from the shell without touching the
bridge internals. It writes the next inbox/<seq>.json (seq = max existing + 1),
then polls outbox/<seq>.json until it appears and prints it.

Examples:
    python -m ai_web_auto_backend.orchestrate navigate url=https://example.com
    python -m ai_web_auto_backend.orchestrate screenshot
    python -m ai_web_auto_backend.orchestrate scrape selector="h1, h2" limit=20
    python -m ai_web_auto_backend.orchestrate mouse_move selector="text:Sign in"
    python -m ai_web_auto_backend.orchestrate drag from="#a" to="#b"
    python -m ai_web_auto_backend.orchestrate media op=play
    python -m ai_web_auto_backend.orchestrate network_dump resource_type=Media
    python -m ai_web_auto_backend.orchestrate --json '{"action":"type","params":{"selector":"#q","text":"hi"}}'

Value parsing: key=value pairs go into params. Values that look like ints/floats/
bools/JSON are coerced; everything else stays a string. Use --json for full control.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def _coerce(v: str):
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none"):
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    if v[:1] in ("{", "[", '"'):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            pass
    return v


# Keys that belong at the command top level, not inside params.
_TOP = {"target", "timeout"}
# Keys that some actions expect at params top level (not nested).
def _build_spec(action: str, kvs: list[str]) -> dict:
    params: dict = {}
    spec: dict = {"action": action, "params": params}
    for kv in kvs:
        if "=" not in kv:
            raise SystemExit(f"Bad arg (need key=value): {kv!r}")
        k, v = kv.split("=", 1)
        val = _coerce(v)
        if k in _TOP:
            spec[k] = val
        else:
            params[k] = val
    return spec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", nargs="?", help="Command action (navigate, click, ...)")
    ap.add_argument("kv", nargs="*", help="key=value params")
    ap.add_argument("--root", default="orchestrator")
    ap.add_argument("--json", dest="json_spec", help="Full command spec as JSON")
    ap.add_argument("--timeout", type=float, default=45.0, help="Seconds to wait for a result")
    args = ap.parse_args()

    root = Path(args.root)
    inbox = root / "inbox"
    outbox = root / "outbox"
    inbox.mkdir(parents=True, exist_ok=True)
    outbox.mkdir(parents=True, exist_ok=True)

    if args.json_spec:
        spec = json.loads(args.json_spec)
    elif args.action:
        spec = _build_spec(args.action, args.kv)
    else:
        ap.error("Provide an action or --json")

    # Next sequence number = 1 + max seq seen in inbox or outbox.
    seen = [0]
    for d in (inbox, outbox):
        for p in d.glob("*.json"):
            stem = p.stem
            if stem.isdigit():
                seen.append(int(stem))
    seq = max(seen) + 1

    (inbox / f"{seq}.json").write_text(json.dumps(spec), encoding="utf-8")
    result_path = outbox / f"{seq}.json"

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        if result_path.exists():
            try:
                out = json.loads(result_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                time.sleep(0.1)
                continue
            print(json.dumps(out, indent=2))
            sys.exit(0 if out.get("status") == "success" else 2)
        time.sleep(0.15)

    print(json.dumps({"seq": seq, "status": "timeout",
                      "error": f"No result within {args.timeout}s "
                               f"(is run_orchestrated.py running and the extension connected?)"}))
    sys.exit(3)


if __name__ == "__main__":
    main()
