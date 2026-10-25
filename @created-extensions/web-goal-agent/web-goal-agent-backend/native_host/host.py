"""Chrome Native Messaging host: the extension's only way to start the backend.

A Chrome extension cannot launch local programs. Chrome can, for a host that is
registered in the Windows registry and lists the extension in allowed_origins
(install.bat does both). Chrome starts this script per message; it answers
over stdin/stdout using Chrome's framing (4-byte little-endian length + JSON).

Commands (request {"cmd": ...} -> response {"ok": bool, ...}):
  ping                   host is installed and runnable
  status                 is the backend up? (GET /health)
  ensure                 start the backend unless it is already running, then wait
                         until /health answers; returns port + token for the WebSocket
  stop   [force]         graceful /shutdown, then taskkill if it doesn't exit; refuses
                         to stop a manually started backend unless force=true
  restart                stop (if this host started it) + ensure

The backend runs detached: it outlives this short-lived host process, and exits by
itself after `idle_shutdown_minutes` without an extension connected.
Nothing but protocol frames may be written to stdout; diagnostics go to
state/native-host.log.
"""

from __future__ import annotations

import ctypes
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND_DIR = HERE.parent
sys.path.insert(0, str(BACKEND_DIR))

from goal_agent_backend import __version__  # noqa: E402  (stdlib-only imports)
from goal_agent_backend.config import load_config  # noqa: E402

APP = "web-goal-agent"
MAX_MESSAGE = 1024 * 1024
READY_TIMEOUT_S = 20.0
STOP_TIMEOUT_S = 6.0
LOCK_STALE_S = 30.0

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000


# ── Logging (never stdout) ───────────────────────────────────────────────────

_log_path: Path | None = None


def log(msg: str):
    if _log_path is None:
        return
    try:
        with _log_path.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{os.getpid()}] {msg}\n")
    except OSError:
        pass


# ── Native messaging framing ─────────────────────────────────────────────────

def read_message():
    header = sys.stdin.buffer.read(4)
    if len(header) < 4:
        return None
    (length,) = struct.unpack("<I", header)
    if length > MAX_MESSAGE:
        raise ValueError(f"message too large ({length} bytes)")
    return json.loads(sys.stdin.buffer.read(length).decode("utf-8"))


def send_message(obj: dict):
    data = json.dumps(obj).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(data)))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


# ── Process helpers ──────────────────────────────────────────────────────────

def pid_alive(pid: int) -> bool:
    """Liveness without side effects (os.kill(pid, 0) TERMINATES the process on Windows)."""
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def kill_tree(pid: int):
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10)


def port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    # Windows reports "refused" on a closed loopback port only after ~2 s of SYN
    # retries; a listening loopback port accepts in microseconds, so a short
    # timeout answers "closed" quickly without false negatives.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            return s.connect_ex((host, port)) == 0
        except OSError:
            return False


# Loopback only: never route through a system proxy.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_get(url: str, headers: dict | None = None, timeout: float = 1.5):
    req = urllib.request.Request(url, headers=headers or {})
    with _opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def health(cfg):
    """Returns (status, body): 'up' | 'down' | 'foreign' (port taken by something else) | 'unresponsive'."""
    if not port_open(cfg.host, cfg.port):
        return "down", None
    try:
        body = http_get(f"http://{cfg.host}:{cfg.port}/health")
    except urllib.error.HTTPError:
        return "foreign", None
    except (urllib.error.URLError, ConnectionError, socket.timeout, TimeoutError, json.JSONDecodeError, OSError):
        return "unresponsive", None
    if isinstance(body, dict) and body.get("app") == APP:
        return "up", body
    return "foreign", body


def tail(path: Path, lines: int = 25) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


# ── Start lock (prevents two concurrent `ensure` calls starting two backends) ─

class StartLock:
    def __init__(self, path: Path):
        self.path = path
        self.held = False

    def acquire(self) -> bool:
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, json.dumps({"pid": os.getpid(), "ts": time.time()}).encode())
                os.close(fd)
                self.held = True
                return True
            except FileExistsError:
                if self._stale():
                    log("removing stale start lock")
                    try:
                        self.path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                return False
        return False

    def _stale(self) -> bool:
        try:
            info = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return True
        return time.time() - float(info.get("ts", 0)) > LOCK_STALE_S or not pid_alive(int(info.get("pid", 0)))

    def release(self):
        if self.held:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            self.held = False


# ── Commands ─────────────────────────────────────────────────────────────────

def connection_info(cfg, body: dict) -> dict:
    return {"host": cfg.host, "port": cfg.port, "token": cfg.token, "backend": body}


def wait_ready(cfg, proc=None, timeout: float = READY_TIMEOUT_S):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, body = health(cfg)
        if status == "up":
            return body, None
        if status == "foreign":
            return None, f"port {cfg.port} is used by another program"
        if proc is not None and proc.poll() is not None:
            return None, f"backend exited during startup with code {proc.returncode}"
        time.sleep(0.1)
    return None, f"backend did not become ready within {timeout:g}s"


def spawn_backend(cfg):
    out = (cfg.state_dir / "backend-stdout.log").open("ab")
    env = dict(os.environ, WGA_CONFIG=str(cfg.path), PYTHONIOENCODING="utf-8")
    cmd = [sys.executable, "-m", "goal_agent_backend", "--started-by", "native-host"]
    base = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    # Breaking away from Chrome's job object lets the backend outlive this host.
    # Not every job allows it; fall back to a plain detached start.
    for flags in (base | CREATE_BREAKAWAY_FROM_JOB, base):
        try:
            proc = subprocess.Popen(cmd, cwd=str(BACKEND_DIR), env=env, stdin=subprocess.DEVNULL,
                                    stdout=out, stderr=subprocess.STDOUT, creationflags=flags, close_fds=True)
            log(f"spawned backend pid={proc.pid} flags={flags:#x}")
            return proc
        except OSError as e:
            log(f"spawn with flags {flags:#x} failed: {e}")
    raise RuntimeError("could not start the backend process")


def cmd_status(cfg) -> dict:
    status, body = health(cfg)
    return {"ok": True, "running": status == "up", "status": status, "port": cfg.port, "backend": body}


def cmd_ensure(cfg) -> dict:
    status, body = health(cfg)
    if status == "up":
        return {"ok": True, "status": "already_running", **connection_info(cfg, body)}
    if status == "foreign":
        return {"ok": False, "status": "port_conflict",
                "error": f"port {cfg.port} is in use by another program; change 'port' in {cfg.path}"}
    if status == "unresponsive":
        body, err = wait_ready(cfg, timeout=5)
        if body:
            return {"ok": True, "status": "already_running", **connection_info(cfg, body)}
        return {"ok": False, "status": "unresponsive",
                "error": f"something is listening on port {cfg.port} but /health does not answer; "
                         "use Restart backend or end the process"}

    lock = StartLock(cfg.state_dir / "start.lock")
    if not lock.acquire():
        log("another host is starting the backend; waiting for it")
        body, err = wait_ready(cfg)
        if body:
            return {"ok": True, "status": "already_running", **connection_info(cfg, body)}
        return {"ok": False, "status": "start_failed", "error": err,
                "log_tail": tail(cfg.state_dir / "backend-stdout.log")}
    try:
        status, body = health(cfg)  # re-check under the lock
        if status == "up":
            return {"ok": True, "status": "already_running", **connection_info(cfg, body)}
        started = time.monotonic()
        proc = spawn_backend(cfg)
        body, err = wait_ready(cfg, proc)
        if body:
            log(f"backend ready pid={body.get('pid')} in {time.monotonic() - started:.2f}s")
            return {"ok": True, "status": "started", "startup_ms": int((time.monotonic() - started) * 1000),
                    **connection_info(cfg, body)}
        log(f"backend failed to start: {err}")
        if proc.poll() is None:
            kill_tree(proc.pid)
        return {"ok": False, "status": "start_failed", "error": err,
                "log_tail": tail(cfg.state_dir / "backend-stdout.log")}
    finally:
        lock.release()


def cmd_stop(cfg, force: bool = False) -> dict:
    status, body = health(cfg)
    pid = int((body or {}).get("pid") or 0)
    if status == "down":
        return {"ok": True, "status": "not_running"}
    if status == "foreign":
        return {"ok": False, "status": "port_conflict", "error": f"port {cfg.port} belongs to another program"}
    if status == "unresponsive":
        pid = _pid_from_state(cfg)
        if not pid:
            return {"ok": False, "status": "unresponsive", "error": "backend is unresponsive and its PID is unknown"}
    elif body and body.get("started_by") != "native-host" and not force:
        return {"ok": False, "status": "not_owned",
                "error": f"the backend (pid {pid}) was started manually; stop it from its console"}
    try:
        http_get(f"http://{cfg.host}:{cfg.port}/shutdown", headers={"X-WGA-Token": cfg.token}, timeout=2)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        log(f"graceful shutdown request failed: {e}")
    deadline = time.monotonic() + STOP_TIMEOUT_S
    while time.monotonic() < deadline:
        if not pid_alive(pid) and health(cfg)[0] == "down":
            return {"ok": True, "status": "stopped", "pid": pid, "forced": False}
        time.sleep(0.1)
    if pid:
        log(f"backend pid={pid} did not exit; killing")
        kill_tree(pid)
        time.sleep(0.3)
    stopped = health(cfg)[0] == "down"
    return {"ok": stopped, "status": "stopped" if stopped else "stop_failed", "pid": pid, "forced": True}


def _pid_from_state(cfg) -> int:
    try:
        return int(json.loads((cfg.state_dir / "backend.json").read_text(encoding="utf-8")).get("pid") or 0)
    except (OSError, ValueError, json.JSONDecodeError):
        return 0


def handle(cfg, msg: dict) -> dict:
    cmd = msg.get("cmd")
    if cmd == "ping":
        return {"ok": True, "host_version": __version__}
    if cmd == "status":
        return cmd_status(cfg)
    if cmd == "ensure":
        return cmd_ensure(cfg)
    if cmd == "stop":
        return cmd_stop(cfg, force=bool(msg.get("force")))
    if cmd == "restart":
        stopped = cmd_stop(cfg, force=bool(msg.get("force")))
        if not stopped.get("ok"):
            return {**stopped, "status": "restart_refused"}
        result = cmd_ensure(cfg)
        return {**result, "restarted": True, "previous_pid": stopped.get("pid")}
    return {"ok": False, "error": f"unknown command {cmd!r}"}


def main() -> int:
    global _log_path
    try:
        cfg = load_config()
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        _log_path = cfg.state_dir / "native-host.log"
    except Exception as e:  # report config problems to the extension instead of dying silently
        msg = read_message()
        send_message({"ok": False, "error": f"native host config error: {e}"})
        return 1 if msg is not None else 2

    caller = sys.argv[1] if len(sys.argv) > 1 else ""
    allowed = {f"chrome-extension://{x}/" for x in cfg["extension_ids"]}
    if caller.startswith("chrome-extension://") and caller not in allowed:
        log(f"refusing caller {caller}")
        read_message()
        send_message({"ok": False, "error": "caller is not an allowed extension"})
        return 1

    while True:
        try:
            msg = read_message()
        except (ValueError, json.JSONDecodeError) as e:
            send_message({"ok": False, "error": f"bad message: {e}"})
            return 1
        if msg is None:
            return 0
        log(f"request {msg.get('cmd')!r}")
        try:
            reply = handle(cfg, msg)
        except Exception as e:
            log(f"command failed: {e!r}")
            reply = {"ok": False, "error": str(e)[:500]}
        reply.setdefault("host_version", __version__)
        send_message(reply)


if __name__ == "__main__":
    sys.exit(main())
