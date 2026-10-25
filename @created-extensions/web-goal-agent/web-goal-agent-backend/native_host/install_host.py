"""Register (or remove) the native messaging host for the current Windows user.

    python install_host.py            register for Chrome, Chromium, Chrome for Testing, Edge, Brave
    python install_host.py --check    show what is registered
    python install_host.py --uninstall

Writes the host manifest next to host.bat and points
HKCU\\Software\\<browser>\\NativeMessagingHosts\\com.aiwebauto.web_goal_agent at it.
HKCU needs no administrator rights. Also creates config.json with a random
per-install token if it doesn't exist yet.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from goal_agent_backend.config import load_config  # noqa: E402

HOST_NAME = "com.aiwebauto.web_goal_agent"
MANIFEST = HERE / f"{HOST_NAME}.json"
BROWSER_KEYS = {
    "Chrome": r"Software\Google\Chrome\NativeMessagingHosts",
    "Chromium": r"Software\Chromium\NativeMessagingHosts",
    "Chrome for Testing": r"Software\Google\Chrome for Testing\NativeMessagingHosts",
    "Edge": r"Software\Microsoft\Edge\NativeMessagingHosts",
    "Brave": r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts",
}


def write_manifest(extension_ids: list[str]) -> Path:
    manifest = {
        "name": HOST_NAME,
        "description": "Web Goal Agent backend launcher (ai-web-auto)",
        "path": str(HERE / "host.bat"),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{x}/" for x in extension_ids],
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return MANIFEST


def register() -> list[str]:
    import winreg
    done = []
    for browser, base in BROWSER_KEYS.items():
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{base}\\{HOST_NAME}")
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(MANIFEST))
        winreg.CloseKey(key)
        done.append(browser)
    return done


def unregister() -> list[str]:
    import winreg
    removed = []
    for browser, base in BROWSER_KEYS.items():
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, f"{base}\\{HOST_NAME}")
            removed.append(browser)
        except FileNotFoundError:
            pass
    return removed


def check() -> dict[str, str | None]:
    import winreg
    out: dict[str, str | None] = {}
    for browser, base in BROWSER_KEYS.items():
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{base}\\{HOST_NAME}")
            out[browser] = winreg.QueryValueEx(key, "")[0]
            winreg.CloseKey(key)
        except FileNotFoundError:
            out[browser] = None
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32":
        print("This installer targets Windows (registry-based native messaging).")
        return 1
    if args.check:
        for browser, path in check().items():
            print(f"  {browser:<20} {path or '(not registered)'}")
        print(f"  manifest present:    {MANIFEST.exists()}")
        return 0
    if args.uninstall:
        removed = unregister()
        if MANIFEST.exists():
            MANIFEST.unlink()
        print(f"[OK] Removed native host registration for: {', '.join(removed) or 'nothing registered'}")
        return 0
    cfg = load_config()  # creates config.json with a fresh token when missing
    write_manifest(list(cfg["extension_ids"]))
    browsers = register()
    print(f"[OK] Native host manifest: {MANIFEST}")
    print(f"[OK] Registered for: {', '.join(browsers)}")
    print(f"[OK] Config: {cfg.path} (port {cfg.port})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
