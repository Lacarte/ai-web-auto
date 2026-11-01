"""Watch eBay for products: poll saved queries on a timer, alert (sound + Windows notification) on new matching
listings and price drops, and keep each item's price / condition history. Alert only - never buys, bids or carts.

    python ebay_watch.py watches/gpus.json            # loop forever (Ctrl+C to stop)
    python ebay_watch.py watches/gpus.json --once     # one cycle (testing)
    python ebay_watch.py watches/gpus.json --test-alert

State lives in @downloads/watch-<name>/ : state.json (every item seen, with history), alerts.jsonl (every alert).
The first cycle of a new watch only records a baseline; alerts start with the second cycle.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(MODULE_DIR))
import ebay_rules as R  # noqa: E402

MIN_INTERVAL_MIN = 5  # be polite to eBay: never poll faster than this


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── decisions (pure) ─────────────────────────────────────────────────────────

def matches(q: dict, item: dict) -> str | None:
    """Why an item does NOT match this query (None = it matches)."""
    title = item.get("title") or ""
    if item.get("excluded"):
        return item["excluded"]
    if item.get("auction"):
        return "auction"
    for p in q.get("require", []):
        if not re.search(p, title, re.I):
            return f"title lacks /{p}/"
    for p in q.get("reject", []):
        if re.search(p, title, re.I):
            return f"title matches /{p}/"
    total = (item.get("price") or 0) + (item.get("shipping") or 0)
    if item.get("price") is None or item.get("shipping") is None:
        return "price or shipping to the ZIP unknown"
    if q.get("max_price") and total > q["max_price"]:
        return f"${total:.2f} > max ${q['max_price']}"
    conds = q.get("conditions")
    if conds and not any(c.lower() in (item.get("condition") or "").lower() for c in conds):
        return f"condition '{item.get('condition')}'"
    if q.get("min_feedback") and (item.get("feedback_count") or 0) < q["min_feedback"]:
        return f"seller has {item.get('feedback_count')} ratings"
    return None


def diff(state: dict, qname: str, q: dict, items: list[dict], baseline: bool, tax: float = 0.07) -> list[dict]:
    """Update the state with this query's results; return alert events (empty on a baseline cycle)."""
    events, seen_now = [], set()
    for it in items:
        if it.get("fewer_words"):
            continue
        iid = it["id"]
        seen_now.add(iid)
        total = None if it.get("price") is None or it.get("shipping") is None else round(it["price"] + it["shipping"], 2)
        rec = state["items"].get(iid)
        why_not = matches(q, it)
        snap = {"at": now(), "price": it.get("price"), "shipping": it.get("shipping"), "total": total, "condition": it.get("condition")}
        if rec is None:
            rec = state["items"][iid] = {"id": iid, "url": it["url"], "title": it["title"], "query": qname, "first_seen": now(),
                                         "history": [snap], "matches": why_not is None, "why_not": why_not}
            if why_not is None and not baseline:
                events.append({"type": "NEW", "query": qname, "id": iid, "url": it["url"], "title": it["title"], "total": total,
                               "landed_est": round(total * (1 + tax), 2) if total else None, "condition": it.get("condition"),
                               "seller": it.get("seller"), "feedback": f"{it.get('feedback_pct')}% ({it.get('feedback_count')})"})
        else:
            last = rec["history"][-1]
            if (last.get("total"), last.get("condition")) != (total, it.get("condition")):
                rec["history"].append(snap)
                if why_not is None and total and last.get("total") and total < last["total"] - 0.009 and not baseline:
                    events.append({"type": "PRICE DROP", "query": qname, "id": iid, "url": it["url"], "title": it["title"],
                                   "total": total, "was": last["total"], "landed_est": round(total * (1 + tax), 2),
                                   "condition": it.get("condition")})
            if rec.get("gone_since"):
                rec.pop("gone_since")
            rec.update(matches=why_not is None, why_not=why_not, last_seen=now())
    for iid, rec in state["items"].items():  # listed before, missing now (first page, newest first): sold, ended or pushed down
        if rec.get("query") == qname and iid not in seen_now and not rec.get("gone_since"):
            rec["gone_since"] = now()
    return events


# ── alerts ───────────────────────────────────────────────────────────────────

PS_AUMID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"


def alert(title: str, message: str) -> None:
    """Sound + Windows notification (falls back to a message box); never blocks the loop."""
    try:
        import winsound
        winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
    except Exception:
        print("\a", end="", flush=True)
    esc = lambda s: s.replace("'", "''").replace("&", "and").replace("<", "(").replace(">", ")")[:240]
    ps = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; "
          "$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
          f"$t = $x.GetElementsByTagName('text'); $t.Item(0).AppendChild($x.CreateTextNode('{esc(title)}')) > $null; "
          f"$t.Item(1).AppendChild($x.CreateTextNode('{esc(message)}')) > $null; "
          f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{PS_AUMID}').Show([Windows.UI.Notifications.ToastNotification]::new($x))")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, timeout=20)
        if r.returncode == 0:
            return
    except Exception:
        pass
    def box():  # fallback: a message box on its own thread
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, title, 0x40 | 0x40000)
        except Exception:
            pass
    threading.Thread(target=box, daemon=True).start()


def describe(ev: dict) -> tuple[str, str]:
    head = f"eBay {ev['type']}: {ev['query']}"
    price = f"${ev['total']:.2f}" + (f" (was ${ev['was']:.2f})" if ev.get("was") else "") + f" ~${ev['landed_est']:.2f} with tax"
    return head, f"{price} | {ev.get('condition') or ''} | {ev['title'][:90]} | {ev['url']}"


# ── loop ─────────────────────────────────────────────────────────────────────

def load_watch(path: Path) -> dict:
    w = json.loads(path.read_text(encoding="utf-8"))
    w["interval_minutes"] = max(MIN_INTERVAL_MIN, float(w.get("interval_minutes", 10)))
    return w


def state_dir(w: dict) -> Path:
    d = MODULE_DIR / "@downloads" / f"watch-{w['name']}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_state(d: Path) -> dict:
    p = d / "state.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"items": {}, "cycles": 0, "created": now()}


async def cycle(sess, search, w: dict, state: dict, d: Path, sold_sort: int = 10) -> list[dict]:
    baseline = state["cycles"] == 0 and not w.get("alert_on_first_cycle")
    events = []
    for name, q in w["queries"].items():
        res = await search(sess, q["query"], q.get("min"), q.get("max"), max_pages=q.get("pages", 1), sort=sold_sort)
        events += diff(state, name, q, res["items"] + res["excluded"], baseline, w.get("tax_rate", 0.07))
    state["cycles"] += 1
    state["last_cycle"] = now()
    (d / "state.json").write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
    with (d / "alerts.jsonl").open("a", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps({"at": now(), **ev}, ensure_ascii=False) + "\n")
    for ev in events:
        head, msg = describe(ev)
        print(f"\n*** {head}\n    {msg}", flush=True)
        alert(head, msg)
        await sess.feedback(f"{ev['type']}: {ev['title'][:60]} ${ev['total']:.2f}")
    print(f"[{now()}] cycle {state['cycles']}{' (baseline - no alerts)' if baseline else ''}: "
          f"{len(events)} alert(s), {len(state['items'])} items tracked", flush=True)
    return events


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("watch", help="watches/<name>.json")
    ap.add_argument("--once", action="store_true", help="run one cycle and exit")
    ap.add_argument("--test-alert", action="store_true", help="play the alert and exit")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.test_alert:
        alert("eBay watch: test", "If you see and hear this, alerts work.")
        time.sleep(2)
        return 0
    path = Path(a.watch) if Path(a.watch).exists() else MODULE_DIR / a.watch
    w = load_watch(path)
    d = state_dir(w)
    import ebay_search as E
    state = load_state(d)

    async def run(sess):
        while True:
            try:
                await cycle(sess, E.search_products, w, state, d)
            except E.Blocked as ex:
                alert("eBay watch stopped", f"{ex} - solve it in Chrome, then restart the watch.")
                raise
            if a.once:
                return
            nxt = time.time() + w["interval_minutes"] * 60
            print(f"    next check at {datetime.fromtimestamp(nxt):%H:%M:%S} (every {w['interval_minutes']:g} min; Ctrl+C to stop)", flush=True)
            await asyncio.sleep(w["interval_minutes"] * 60)

    backoff = 60
    while True:
        try:
            asyncio.run(E.with_session("search_products", run, d))
            return 0
        except KeyboardInterrupt:
            print("stopped")
            return 0
        except E.Blocked:
            return 3
        except SystemExit as ex:  # preflight failed (e.g. extension not connected)
            if a.once:
                return int(ex.code or 1)
        except Exception as ex:
            print(f"! session error: {ex}", flush=True)
            if a.once:
                return 1
        print(f"    reconnecting in {backoff} s", flush=True)
        time.sleep(backoff)
        backoff = min(backoff * 2, 900)


if __name__ == "__main__":
    sys.exit(main())
