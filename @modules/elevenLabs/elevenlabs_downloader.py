"""
ElevenLabs Audio Track Downloader & Workspace Cleaner
Target: ElevenLabs Music History / Generations (https://elevenlabs.io/app/music/history)

Workflow:
1. Connect to Chrome tab via AI Web-Auto WebSocket
2. Ensure ElevenLabs music history tab is active
3. Find all songs/tracks via their Download buttons and action cards
4. Download each track to @modules/elevenLabs/@downloads/
5. Verify file integrity (size + audio headers)
6. Clean up by deleting the track from the web UI (when --force-delete is specified)
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from loguru import logger

ROOT_DIR = Path(__file__).resolve().parents[2]
MODULES_DIR = Path(__file__).resolve().parents[1]
for _path in (ROOT_DIR, MODULES_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from ai_web_auto_backend.automation_controller import BrowserAgent
from ai_web_auto_backend.core.models import Command
import module_kit

# Selectors and UI texts come from knowledge/locators.json (@@<key>@@ / @@<key>.text@@).
KNOWLEDGE = module_kit.load(Path(__file__).parent)
js = KNOWLEDGE.js
HISTORY_URL = KNOWLEDGE.page("history")["url"]
BASE_DOWNLOAD_DIR = Path(__file__).parent / KNOWLEDGE.settings.get("downloads_dir", "@downloads")


def get_session_download_dir(base_dir: Path | None = None) -> Path:
    """Reserve the next daily batch folder, e.g. 2026-10-23-session3."""
    base = base_dir or BASE_DOWNLOAD_DIR
    base.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d")
    pattern = re.compile(rf"{re.escape(today_str)}-session([1-9]\d*)$")
    existing_numbers = (
        int(match.group(1))
        for path in base.iterdir()
        if path.is_dir() and (match := pattern.fullmatch(path.name))
    )
    number = max(existing_numbers, default=0) + 1
    while True:
        target_dir = base / f"{today_str}-session{number}"
        try:
            target_dir.mkdir(exist_ok=False)
            return target_dir
        except FileExistsError:
            number += 1


def clean_proper_filename(original_name: str) -> str:
    """Normalize Chrome file names like 'Blade_of_Dawn_2026-10-21T033949 (1).mp3' -> 'Blade_of_Dawn_2026-10-21T033949.mp3'."""
    name = re.sub(r'\s*\(\d+\)\.mp3$', '.mp3', original_name, flags=re.IGNORECASE)
    return name


def file_matches_title(file_path: Path, title: str) -> bool:
    """Require the downloaded file name to begin with the track title."""
    normalized_title = re.sub(r'[^a-z0-9]+', '_', title.lower()).strip('_')
    normalized_file = re.sub(r'[^a-z0-9]+', '_', file_path.stem.lower()).strip('_')
    return bool(normalized_title) and (
        normalized_file == normalized_title or normalized_file.startswith(normalized_title + '_')
    )


def verify_audio_file(
    file_path: Path, min_size_bytes: int = 10240, expected_duration: str = "",
) -> bool:
    """
    Verify audio file integrity:
    1. Exists
    2. Size >= min_size_bytes (default 10KB)
    3. Has valid audio header (MP3/WAV/Ogg)
    """
    if not file_path.exists():
        return False

    size = file_path.stat().st_size
    if size < min_size_bytes:
        logger.warning(f"File too small ({size} bytes): {file_path}")
        return False

    try:
        with open(file_path, "rb") as f:
            header = f.read(12)
            # MP3 with ID3 tag
            valid_header = header.startswith(b"ID3")
            # MP3 sync frame: 0xFF followed by 0xFB, 0xF3, 0xF2
            valid_header |= len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0
            # RIFF WAV
            valid_header |= header.startswith(b"RIFF") and header[8:12] == b"WAVE"
            # OGG
            valid_header |= header.startswith(b"OggS")
    except Exception as e:
        logger.error(f"Error reading file header: {e}")
        return False

    if not valid_header:
        return False
    if expected_duration:
        probe = shutil.which("ffprobe")
        if not probe:
            logger.warning("ffprobe is required for duration verification before deletion")
            return False
        try:
            result = subprocess.run(
                [probe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(file_path)],
                capture_output=True, text=True, timeout=15, check=True,
            )
            expected_seconds = sum(int(part) * 60 ** power for power, part in
                                   enumerate(reversed(expected_duration.split(":"))))
            actual_seconds = float(result.stdout.strip())
            if abs(actual_seconds - expected_seconds) > 2:
                logger.warning(
                    f"Duration mismatch for {file_path.name}: "
                    f"{actual_seconds:.1f}s local vs {expected_seconds}s online"
                )
                return False
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            logger.warning(f"Could not validate audio duration for {file_path.name}: {exc}")
            return False
    return True


def find_existing_local_file(title: str, base_dir: Path, duration: str = "") -> Path | None:
    """
    Search recursively across @downloads (and subfolders like current date)
    to check if an audio track matching this title already exists and is intact.
    """
    if len(re.sub(r'[^a-zA-Z0-9]', '', title)) < 3:
        return None

    for f in base_dir.rglob("*.mp3"):
        if file_matches_title(f, title):
            if verify_audio_file(f, expected_duration=duration):
                return f
    return None


class ElevenLabsCleaner:
    def __init__(self, agent: BrowserAgent, base_download_dir: Path | None = None, delete_after: bool = False, force_redownload: bool = True):
        self.agent = agent
        self.base_download_dir = base_download_dir or BASE_DOWNLOAD_DIR
        self.base_download_dir.mkdir(parents=True, exist_ok=True)
        self.session_download_dir = get_session_download_dir(self.base_download_dir)
        self.delete_after = delete_after
        self.force_redownload = force_redownload
        self._cdp_input_broken = False

    async def scan_tracks(self, ready_only: bool = True) -> list[dict[str, Any]]:
        """Find all track items via ElevenLabs Download buttons and cards."""
        js_scan = js("""
        (() => {
            const rows = [...document.querySelectorAll(@@track_row@@)];
            const items = rows.map((row, idx) => {
                const link = row.querySelector(@@track_link@@);
                const path = link && new URL(link.href).pathname;
                const projectId = path && path.split('/').filter(Boolean).at(-1);
                const title = row.querySelector(@@track_title@@)?.textContent?.trim();
                const duration = [...row.querySelectorAll(@@track_text@@)]
                    .map(el => el.textContent.trim()).find(text => /^\\d+:\\d+$/.test(text)) || '';
                return {
                    index: idx,
                    title: title || '',
                    duration,
                    project_id: projectId || '',
                    ready: !!row.querySelector(@@download_button@@) &&
                        !!row.querySelector(@@more_button@@) &&
                        !row.querySelector(@@track_busy@@)
                };
            }).filter(item => item.title && item.project_id);

            return JSON.stringify({
                found: items.length > 0,
                count: items.length,
                items: items,
                url: location.href
            });
        })()
        """)
        resp = await self.agent.server.send_command(Command(action="execute_script", params={"script": js_scan}))
        raw = resp.data.extracted_data.get("result", "{}")
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
            if not data.get("found"):
                logger.warning(f"No tracks found on {data.get('url')}")
                return []
            items = data.get("items", [])
            return [item for item in items if item.get("ready")] if ready_only else items
        except Exception as e:
            logger.error(f"Failed to parse track items: {e}")
            return []

    async def highlight_track_card(self, index: int, color: str = "#00ff88", label: str = "MP3 TRACK", project_id: str = "") -> bool:
        """Scroll track card smoothly into view and apply an animated highlight border."""
        highlight_js = js("""
        ((trackIdx, borderColor, badgeLabel, projectId) => {
            const rows = [...document.querySelectorAll(@@track_row@@)];
            const row = rows.find(li => [...li.querySelectorAll(@@track_link@@)]
                .some(a => new URL(a.href).pathname.endsWith('/' + projectId)));
            const dlBtn = row?.querySelector(@@download_button@@);
            if (!dlBtn) return false;

            let card = dlBtn.parentElement;
            for (let i = 0; i < 8 && card && card !== document.body; i++) {
                if (card.querySelectorAll('button').length >= 3) break;
                card = card.parentElement;
            }
            if (!card) return false;

            // Scroll card into view centered
            card.scrollIntoView({ behavior: 'smooth', block: 'center' });

            // Apply glowing border and badge
            card.style.transition = 'all 0.35s ease';
            card.style.outline = '3px solid ' + borderColor;
            card.style.outlineOffset = '2px';
            card.style.boxShadow = '0 0 20px ' + borderColor + '66';
            card.style.borderRadius = '8px';

            // Tag badge on top right of the card
            let badge = card.querySelector(@@track_badge@@);
            if (!badge) {
                badge = document.createElement('div');
                badge.className = 'awa-track-badge';
                badge.style.position = 'absolute';
                badge.style.top = '-10px';
                badge.style.right = '12px';
                badge.style.background = borderColor;
                badge.style.color = '#000';
                badge.style.fontWeight = 'bold';
                badge.style.fontSize = '11px';
                badge.style.fontFamily = 'monospace';
                badge.style.padding = '2px 8px';
                badge.style.borderRadius = '4px';
                badge.style.zIndex = '9999';
                badge.style.boxShadow = '0 2px 8px rgba(0,0,0,0.4)';
                if (window.getComputedStyle(card).position === 'static') {
                    card.style.position = 'relative';
                }
                card.appendChild(badge);
            }
            badge.style.background = borderColor;
            badge.textContent = badgeLabel + ' #' + (trackIdx + 1);
            return true;
        })(""") + f"{index}, {json.dumps(color)}, {json.dumps(label)}, {json.dumps(project_id)}" + """)"""
        r = await self.agent.server.send_command(Command(action="execute_script", params={"script": highlight_js}))
        return bool(r.data.extracted_data.get("result"))

    async def trigger_download_click(self, index: int, project_id: str) -> bool:
        """Download MP3 through the menu belonging to this project."""
        async def position(script: str, timeout: float = 5.0) -> dict | None:
            deadline = asyncio.get_running_loop().time() + timeout
            while asyncio.get_running_loop().time() < deadline:
                response = await self.agent.server.execute_script(script)
                result = response.data.extracted_data.get("result")
                if response.status != "success" or (isinstance(result, dict) and "_error" in result):
                    raise RuntimeError(response.error or str(result))
                if isinstance(result, dict) and "x" in result and "y" in result:
                    return result
                await asyncio.sleep(0.15)
            return None

        async def click(point: dict) -> None:
            response = await self.agent.server.click_cdp(point["x"], point["y"])
            if response.status != "success":
                raise RuntimeError(response.error or "CDP click failed")

        trigger_script = js("""
        ((projectId) => {
            const row = [...document.querySelectorAll(@@track_row@@)]
                .find(li => [...li.querySelectorAll(@@track_link@@)]
                    .some(a => new URL(a.href).pathname.endsWith('/' + projectId)));
            const button = row?.querySelector(@@download_button@@);
            if (!button || button.disabled) return null;
            button.scrollIntoView({behavior: 'instant', block: 'center'});
            const r = button.getBoundingClientRect();
            const x = Math.round(r.left + r.width / 2), y = Math.round(r.top + r.height / 2);
            return r.width && r.height && x >= 0 && y >= 0 && x < innerWidth && y < innerHeight
                ? {x, y, triggerId: button.id,
                   obscured: !button.contains(document.elementFromPoint(x, y))} : null;
        })(""") + json.dumps(project_id) + """)"""
        menu_script = js("""
        ((triggerId) => {
            const menu = [...document.querySelectorAll(@@open_menu@@)]
                .find(el => el.getAttribute('aria-labelledby') === triggerId);
            const item = menu && [...menu.querySelectorAll(@@mp3_menu_item@@)]
                .find(el => (el.textContent || '').trim() === @@mp3_menu_item.text@@ && !el.hasAttribute('data-disabled'));
            if (!item) return null;
            const r = item.getBoundingClientRect();
            const x = Math.round(r.left + r.width / 2), y = Math.round(r.top + r.height / 2);
            return r.width && r.height && x >= 0 && y >= 0 && x < innerWidth && y < innerHeight
                ? {x, y, obscured: !item.contains(document.elementFromPoint(x, y))} : null;
        })(__TRIGGER_ID__)""")
        react_trigger_script = js("""
        ((projectId) => {
            const rows = [...document.querySelectorAll(@@track_row@@)]
                .filter(row => [...row.querySelectorAll(@@track_link@@)]
                    .some(a => new URL(a.href).pathname.endsWith('/' + projectId)));
            if (rows.length !== 1) return false;
            const button = rows[0].querySelector(@@download_button@@);
            const key = button && Object.keys(button).find(k => k.startsWith('__reactProps$'));
            if (!key || button.disabled || typeof button[key].onPointerDown !== 'function') return false;
            const event = new PointerEvent('pointerdown',
                {bubbles:true, cancelable:true, pointerType:'mouse', button:0, isPrimary:true});
            Object.defineProperty(event, 'currentTarget', {value:button});
            Object.defineProperty(event, 'target', {value:button});
            button[key].onPointerDown(event);
            return true;
        })(""") + json.dumps(project_id) + """)"""
        react_mp3_script = js("""
        ((triggerId) => {
            const menus = [...document.querySelectorAll(@@open_menu@@)]
                .filter(el => el.getAttribute('aria-labelledby') === triggerId);
            if (menus.length !== 1) return false;
            const items = [...menus[0].querySelectorAll(@@mp3_menu_item@@)]
                .filter(el => (el.textContent || '').trim() === @@mp3_menu_item.text@@ &&
                    !el.hasAttribute('data-disabled'));
            if (items.length !== 1) return false;
            const item = items[0];
            const key = Object.keys(item).find(k => k.startsWith('__reactProps$'));
            if (!key || typeof item[key].onClick !== 'function') return false;
            const event = new MouseEvent('click', {bubbles:true, cancelable:true, button:0});
            Object.defineProperty(event, 'currentTarget', {value:item});
            Object.defineProperty(event, 'target', {value:item});
            item[key].onClick(event);
            return true;
        })(__TRIGGER_ID__)""")
        try:
            trigger = await position(trigger_script)
            if not trigger:
                logger.warning(f"Download button unavailable for project {project_id}")
                return False
            if self._cdp_input_broken or trigger.get("obscured"):
                response = await self.agent.server.execute_script(react_trigger_script)
                if response.data.extracted_data.get("result") is not True:
                    return False
                self._cdp_input_broken = True
            else:
                await click(trigger)
            scoped_menu = menu_script.replace("__TRIGGER_ID__", json.dumps(trigger["triggerId"]))
            mp3 = await position(scoped_menu, timeout=2.0)
            if not mp3 and not self._cdp_input_broken:
                response = await self.agent.server.execute_script(react_trigger_script)
                if response.data.extracted_data.get("result") is True:
                    self._cdp_input_broken = True
                    mp3 = await position(scoped_menu)
            if not mp3:
                logger.warning(f"MP3 menu item unavailable for project {project_id}")
                return False
            scoped_react_mp3 = react_mp3_script.replace("__TRIGGER_ID__", json.dumps(trigger["triggerId"]))
            if self._cdp_input_broken or mp3.get("obscured"):
                response = await self.agent.server.execute_script(scoped_react_mp3)
                if response.data.extracted_data.get("result") is not True:
                    return False
                self._cdp_input_broken = True
            else:
                await click(mp3)
                # The menu closes when Radix accepts the MP3 selection.
                if await position(scoped_menu, timeout=1.0):
                    response = await self.agent.server.execute_script(scoped_react_mp3)
                    if response.data.extracted_data.get("result") is not True:
                        return False
                    self._cdp_input_broken = True
            return True
        except (RuntimeError, TimeoutError, ConnectionError) as exc:
            logger.error(f"Could not start MP3 download for project {project_id}: {exc}")
            return False

    async def delete_track_item(
        self, index: int, target_file_path: Path,
        project_id: str = "", expected_title: str = "", expected_duration: str = "",
    ) -> bool:
        """Delete one verified track through the visible Radix menu and dialog."""
        if index < 0 or not verify_audio_file(target_file_path, expected_duration=expected_duration):
            logger.error(f"Aborting deletion: invalid index or unverified local file: {target_file_path}")
            return False
        if not project_id or not expected_title:
            logger.error("Aborting deletion: project ID and title are required to identify the track")
            return False
        if not file_matches_title(target_file_path, expected_title):
            logger.error(f"Aborting deletion: file name does not match project title '{expected_title}'")
            return False

        async def evaluate(script: str) -> Any:
            response = await self.agent.server.send_command(
                Command(action="execute_script", params={"script": script})
            )
            if response.status != "success":
                raise RuntimeError(response.error or "Browser script failed")
            result = response.data.extracted_data.get("result")
            if isinstance(result, dict) and "_error" in result:
                raise RuntimeError(result["_error"])
            return result

        async def wait_for_position(script: str, label: str, timeout: float = 12.0) -> dict | None:
            deadline = asyncio.get_running_loop().time() + timeout
            while asyncio.get_running_loop().time() < deadline:
                position = await evaluate(script)
                if isinstance(position, dict) and "x" in position and "y" in position:
                    return position
                await asyncio.sleep(0.15)
            logger.warning(f"Timed out waiting for {label} on track #{index + 1}")
            return None

        async def trusted_click(position: dict) -> None:
            response = await self.agent.server.send_command(
                Command(action="click_cdp", params={"x": position["x"], "y": position["y"]})
            )
            if response.status != "success":
                raise RuntimeError(response.error or "CDP click failed")

        # Project URLs remain stable when React rerenders or list indices shift.
        more_script = js("""
        ((projectId) => {
            const li = [...document.querySelectorAll(@@track_row@@)]
                .find(row => [...row.querySelectorAll(@@track_link@@)]
                    .some(a => new URL(a.href).pathname.endsWith('/' + projectId)));
            if (!li) return null;
            const button = li.querySelector(@@more_button@@);
            if (!button || button.disabled) return null;
            const title = li.querySelector(@@track_title@@)?.textContent?.trim();
            button.scrollIntoView({behavior: 'instant', block: 'center', inline: 'center'});
            const rect = button.getBoundingClientRect();
            const x = Math.round(rect.left + rect.width / 2);
            const y = Math.round(rect.top + rect.height / 2);
            if (!rect.width || !rect.height || x < 0 || y < 0 ||
                x >= innerWidth || y >= innerHeight) return null;
            return {x, y, title, triggerId: button.id,
                obscured: !button.contains(document.elementFromPoint(x, y)),
                count: document.querySelectorAll(@@track_row@@).length};
        })(""") + json.dumps(project_id) + """)"""

        menu_script = js("""
        ((triggerId) => {
            const menu = [...document.querySelectorAll(@@open_menu@@)]
                .find(el => el.getAttribute('aria-labelledby') === triggerId);
            const item = menu && [...menu.querySelectorAll(@@delete_menu_item@@)].find(el =>
                (el.textContent || '').trim().toLowerCase() === @@delete_menu_item.text@@.toLowerCase() &&
                !el.hasAttribute('data-disabled'));
            if (!item) return null;
            const rect = item.getBoundingClientRect();
            const x = Math.round(rect.left + rect.width / 2);
            const y = Math.round(rect.top + rect.height / 2);
            if (!rect.width || !rect.height) return null;
            return {x, y, obscured: !item.contains(document.elementFromPoint(x, y))};
        })(__TRIGGER_ID__)""")

        confirm_script = js("""
        ((expectedTitle) => {
            const dialogs = [...document.querySelectorAll(@@dialog@@)];
            const dialog = dialogs.find(el => {
                const r = el.getBoundingClientRect();
                return r.width && r.height && getComputedStyle(el).visibility !== 'hidden' &&
                    (el.innerText || '').includes('Delete "' + expectedTitle + '"') &&
                    [...el.querySelectorAll(@@dialog_button@@)].some(b =>
                        (b.textContent || '').trim().toLowerCase() === @@delete_confirm_button.text@@.toLowerCase() &&
                        b.matches(@@delete_confirm_button@@));
            });
            const button = dialog && [...dialog.querySelectorAll(@@dialog_button@@)].find(b =>
                (b.textContent || '').trim().toLowerCase() === @@delete_confirm_button.text@@.toLowerCase() &&
                b.matches(@@delete_confirm_button@@) && !b.disabled);
            if (!button) return null;
            const rect = button.getBoundingClientRect();
            const x = Math.round(rect.left + rect.width / 2);
            const y = Math.round(rect.top + rect.height / 2);
            if (!rect.width || !rect.height) return null;
            return {x, y, obscured: !button.contains(document.elementFromPoint(x, y))};
        })(""") + json.dumps(expected_title) + """)"""

        cancel_script = js("""
        (() => {
            const dialog = [...document.querySelectorAll(@@dialog@@)]
                .find(el => el.getBoundingClientRect().width &&
                    (el.innerText || '').includes(@@delete_dialog_question.text@@));
            const button = dialog && [...dialog.querySelectorAll(@@dialog_button@@)]
                .find(b => (b.textContent || '').trim() === @@cancel_button.text@@);
            if (!button) return null;
            const r = button.getBoundingClientRect();
            const x = Math.round(r.left + r.width / 2), y = Math.round(r.top + r.height / 2);
            return r.width && r.height && button.contains(document.elementFromPoint(x, y))
                ? {x, y} : null;
        })()
        """)

        # Some running extension builds acknowledge CDP input without delivering
        # a DOM event after navigation. Radix's live React handlers provide a
        # scoped fallback; all three targets are rechecked before invocation.
        react_more_script = js("""
        ((projectId, expectedTitle) => {
            const rows = [...document.querySelectorAll(@@track_row@@)]
                .filter(row => [...row.querySelectorAll(@@track_link@@)]
                    .some(a => new URL(a.href).pathname.endsWith('/' + projectId)));
            if (rows.length !== 1 ||
                rows[0].querySelector(@@track_title@@)?.textContent?.trim() !== expectedTitle)
                return false;
            const button = rows[0].querySelector(@@more_button@@);
            const key = button && Object.keys(button).find(k => k.startsWith('__reactProps$'));
            if (!key || button.disabled || typeof button[key].onPointerDown !== 'function') return false;
            const event = new PointerEvent('pointerdown',
                {bubbles:true, cancelable:true, pointerType:'mouse', button:0, isPrimary:true});
            Object.defineProperty(event, 'currentTarget', {value:button});
            Object.defineProperty(event, 'target', {value:button});
            button[key].onPointerDown(event);
            return true;
        })(""") + json.dumps(project_id) + ", " + json.dumps(expected_title) + """)"""
        react_menu_script = js("""
        ((triggerId) => {
            const menus = [...document.querySelectorAll(@@open_menu@@)]
                .filter(el => el.getAttribute('aria-labelledby') === triggerId);
            if (menus.length !== 1) return false;
            const items = [...menus[0].querySelectorAll(@@delete_menu_item@@)]
                .filter(el => (el.textContent || '').trim() === @@delete_menu_item.text@@ &&
                    !el.hasAttribute('data-disabled'));
            if (items.length !== 1) return false;
            const item = items[0];
            const key = Object.keys(item).find(k => k.startsWith('__reactProps$'));
            if (!key || typeof item[key].onClick !== 'function') return false;
            const event = new MouseEvent('click', {bubbles:true, cancelable:true, button:0});
            Object.defineProperty(event, 'currentTarget', {value:item});
            Object.defineProperty(event, 'target', {value:item});
            item[key].onClick(event);
            return true;
        })(__TRIGGER_ID__)""")
        react_confirm_script = js("""
        ((expectedTitle) => {
            const dialogs = [...document.querySelectorAll(@@dialog@@)]
                .filter(el => el.getBoundingClientRect().width &&
                    (el.innerText || '').includes('Delete "' + expectedTitle + '"'));
            if (dialogs.length !== 1) return false;
            const buttons = [...dialogs[0].querySelectorAll(@@dialog_button@@)]
                .filter(b => (b.textContent || '').trim() === @@delete_confirm_button.text@@ &&
                    b.matches(@@delete_confirm_button@@) && !b.disabled);
            if (buttons.length !== 1) return false;
            const button = buttons[0];
            const key = Object.keys(button).find(k => k.startsWith('__reactProps$'));
            if (!key || typeof button[key].onClick !== 'function') return false;
            const event = new MouseEvent('click', {bubbles:true, cancelable:true, button:0});
            Object.defineProperty(event, 'currentTarget', {value:button});
            Object.defineProperty(event, 'target', {value:button});
            button[key].onClick(event);
            return true;
        })(""") + json.dumps(expected_title) + """)"""

        opened_dialog = False
        finished = False
        reloaded = False

        async def reload_history() -> None:
            nonlocal reloaded
            response = await self.agent.server.send_command(Command(
                action="navigate",
                params={"url": HISTORY_URL, "dismiss_banners": False},
                timeout=60000,
            ))
            if response.status != "success":
                raise RuntimeError(response.error or "Could not reload history")
            reloaded = True

        try:
            more = await wait_for_position(more_script, "More button")
            if not more:
                return False
            if more.get("title") != expected_title:
                logger.error(f"Aborting deletion: project title changed from '{expected_title}' to '{more.get('title')}'")
                return False
            if self._cdp_input_broken or more.get("obscured"):
                if not await evaluate(react_more_script):
                    return False
                self._cdp_input_broken = True
            else:
                await trusted_click(more)

            scoped_menu_script = menu_script.replace("__TRIGGER_ID__", json.dumps(more["triggerId"]))
            menu_item = await wait_for_position(scoped_menu_script, "Delete project menu item", timeout=2.0)
            if not menu_item and not self._cdp_input_broken:
                logger.warning("CDP click did not open More menu; trying the live React handler")
                if await evaluate(react_more_script):
                    self._cdp_input_broken = True
                    menu_item = await wait_for_position(scoped_menu_script, "Delete project menu item")
            if not menu_item:
                return False
            if self._cdp_input_broken or menu_item.get("obscured"):
                if not await evaluate(react_menu_script.replace("__TRIGGER_ID__", json.dumps(more["triggerId"]))):
                    return False
                self._cdp_input_broken = True
            else:
                await trusted_click(menu_item)
            opened_dialog = True

            confirm = await wait_for_position(confirm_script, "Delete confirmation", timeout=2.0)
            if not confirm and not self._cdp_input_broken:
                logger.warning("CDP click did not open confirmation; trying the live React handler")
                if await evaluate(react_menu_script.replace("__TRIGGER_ID__", json.dumps(more["triggerId"]))):
                    self._cdp_input_broken = True
                    confirm = await wait_for_position(confirm_script, "Delete confirmation")
            if not confirm:
                return False
            if self._cdp_input_broken or confirm.get("obscured"):
                if not await evaluate(react_confirm_script):
                    return False
                self._cdp_input_broken = True
            else:
                await trusted_click(confirm)

            # A successful CDP command only proves delivery of the click.
            # Require this exact project URL to disappear from the history list.
            removed_script = js("""
            ((projectId) => location.pathname === '/app/music/history' &&
                ![...document.querySelectorAll(@@track_row@@)]
                    .some(row => [...row.querySelectorAll(@@track_link@@)]
                        .some(a => new URL(a.href).pathname.endsWith('/' + projectId))))(""") + json.dumps(project_id) + """)"""
            deadline = asyncio.get_running_loop().time() + 8.0
            while asyncio.get_running_loop().time() < deadline:
                if await evaluate(removed_script):
                    await reload_history()
                    # Navigation may finish before the history API repopulates the list.
                    await asyncio.sleep(4.0)
                    verify_script = js("""
                    ((projectId) => {
                        const rows = [...document.querySelectorAll(@@track_row@@)];
                        return {count: rows.length, emptyState: !!document.querySelector(@@main@@)?.innerText?.includes(@@history_empty_state.text@@),
                            targetPresent: rows.some(row =>
                            [...row.querySelectorAll(@@track_link@@)]
                                .some(a => new URL(a.href).pathname.endsWith('/' + projectId)))};
                    })(""") + json.dumps(project_id) + """)"""
                    verify_deadline = asyncio.get_running_loop().time() + 35.0
                    while asyncio.get_running_loop().time() < verify_deadline:
                        state = await evaluate(verify_script)
                        if (isinstance(state, dict) and
                                state.get("count", 0) >= more["count"] - 1 and
                                (state.get("count", 0) > 0 or state.get("emptyState"))):
                            finished = not state["targetPresent"]
                            if finished:
                                logger.info(f"Deleted verified track #{index + 1}; absent after reload")
                            return finished
                        await asyncio.sleep(0.3)
                    logger.warning(f"History did not finish loading after deleting track #{index + 1}")
                    return False
                await asyncio.sleep(0.25)
            logger.warning(f"Delete click sent, but track #{index + 1} was not removed")
            return False
        except (RuntimeError, TimeoutError, ConnectionError) as exc:
            logger.error(f"Could not delete track #{index + 1}: {exc}")
            return False
        finally:
            if opened_dialog and not finished:
                try:
                    cancel = await evaluate(cancel_script)
                    if isinstance(cancel, dict) and "x" in cancel:
                        await trusted_click(cancel)
                    if not reloaded:
                        await reload_history()
                except (RuntimeError, TimeoutError, ConnectionError) as exc:
                    logger.warning(f"Could not dismiss unfinished delete dialog: {exc}")

    async def setup_native_download_path(self):
        """Configure Chrome CDP so downloads land directly in the session @downloads directory."""
        try:
            target_str = str(self.session_download_dir.resolve())
            logger.info(f"Configuring direct download directory: {target_str}")
            await self.agent.server.send_command(Command(
                action="set_download_path",
                params={"download_path": target_str}
            ))
        except Exception as e:
            logger.debug(f"Direct download path setup note: {e}")

    async def process_all(self, max_items: int = 50, dry_run: bool = False):
        """Process all song tracks on ElevenLabs."""
        # Configure Chrome to direct downloads straight into session folder
        await self.setup_native_download_path()

        logger.info("Scanning ElevenLabs Music History for tracks...")
        if dry_run:
            logger.info("[SIMULATION MODE] -- Dry run enabled. Audio will be downloaded and verified. NO tracks will be deleted.")

        tracks = await self.scan_tracks()
        logger.info(f"Found {len(tracks)} tracks in ElevenLabs history.")

        processed = 0
        # Deletion changes list indices. Work from the end so earlier indices
        # still refer to the same tracks returned by the initial scan.
        selected_tracks = tracks[:max_items]
        if self.delete_after and not dry_run:
            selected_tracks = list(reversed(selected_tracks))
        title_counts = Counter(track["title"] for track in tracks)
        project_counts = Counter(track["project_id"] for track in tracks)
        outcomes = {
            track["project_id"]: {
                "title": track["title"], "project_id": track["project_id"],
                "status": "pending", "local_file": "",
            }
            for track in selected_tracks
        }
        for track in selected_tracks:
            idx = track["index"]
            title = track.get("title", f"Track_{idx+1}")
            project_id = track["project_id"]
            dur = track.get("duration") or ""

            logger.info(f"[{idx+1}/{len(tracks)}] Processing track #{idx+1}: '{title}' ({dur})")
            outcome = outcomes[project_id]
            if title_counts[title] != 1 or project_counts[project_id] != 1:
                logger.warning(f"Ambiguous project or title for '{title}'; leaving it online")
                outcome["status"] = "ambiguous_identity"
                continue

            # A navigation after the previous deletion can leave skeleton rows
            # in place for several seconds. Wait for this exact card to return.
            ready_deadline = asyncio.get_running_loop().time() + 45.0
            while asyncio.get_running_loop().time() < ready_deadline:
                current = next((item for item in await self.scan_tracks()
                                if item["project_id"] == project_id), None)
                if current:
                    idx = current["index"]
                    break
                await asyncio.sleep(1.0)
            else:
                outcome["status"] = "card_unavailable"
                logger.warning(f"Project card did not become ready: {title}")
                continue

            # Check if track already exists locally in @downloads or any date subfolder
            existing_local = find_existing_local_file(title, self.base_download_dir, dur)
            if existing_local:
                outcome["local_file"] = str(existing_local)
                logger.info(f"[ALREADY EXISTS] Found local file: {existing_local.name} ({existing_local.stat().st_size:,} bytes)")
                if not self.force_redownload:
                    logger.info(f"Skipping download since file exists locally.")
                    processed += 1
                    await self.highlight_track_card(idx, color="#00ff88", label="LOCAL COPY EXISTS", project_id=project_id)
                    if not dry_run and self.delete_after:
                        logger.info(f"Deleting verified track from ElevenLabs: {title}...")
                        deleted = await self.delete_track_item(idx, existing_local, project_id, title, dur)
                        outcome["status"] = "deleted" if deleted else "delete_failed"
                    else:
                        outcome["status"] = "verified_online"
                    continue
                else:
                    logger.info(f"Re-downloading fresh copy into the current session folder: {self.session_download_dir.name}/")

            # Highlight & Scroll track card into view with glowing cyan border
            await self.highlight_track_card(idx, color="#00e5ff", label="DOWNLOADING MP3", project_id=project_id)
            await asyncio.sleep(0.5)

            # Record files before download click (check both session folder and user downloads)
            user_dl_dir = Path.home() / "Downloads"
            pre_session_files = set(self.session_download_dir.glob("*.mp3"))
            pre_user_files = set(user_dl_dir.glob("*.mp3")) if user_dl_dir.exists() else set()

            # 1. Trigger Download via Native MP3 Menu
            clicked = await self.trigger_download_click(idx, project_id)
            if clicked:
                logger.info(f"Triggered native MP3 download for track #{idx+1} ('{title}')")
            else:
                logger.warning(f"Download did not start for '{title}'; leaving project online")
                outcome["status"] = "download_failed"
                continue

            # 2. Wait for the new MP3 file to arrive (prioritizing session folder directly)
            logger.info(f"Waiting for downloaded MP3 file...")
            downloaded_file = None
            for _ in range(30):
                await asyncio.sleep(0.5)
                # Check session directory first (direct CDP download)
                current_session = set(self.session_download_dir.glob("*.mp3"))
                new_session = current_session - pre_session_files
                completed_session = [f for f in new_session if f.stat().st_size > 10240 and file_matches_title(f, title)]
                if completed_session:
                    downloaded_file = completed_session[0]
                    break

                # Fallback check user downloads directory
                if user_dl_dir.exists():
                    current_user = set(user_dl_dir.glob("*.mp3"))
                    new_user = current_user - pre_user_files
                    completed_user = [f for f in new_user if f.stat().st_size > 10240 and file_matches_title(f, title)]
                    if completed_user:
                        downloaded_file = completed_user[0]
                        break

            # 3. Save into current batch folder (@downloads/YYYY-MM-DD-sessionN)
            if downloaded_file and downloaded_file.exists():
                proper_name = clean_proper_filename(downloaded_file.name)
                final_target = self.session_download_dir / proper_name
                outcome["local_file"] = str(final_target)

                # If file landed in user Downloads, move it into the session folder
                if downloaded_file.parent.resolve() != self.session_download_dir.resolve():
                    import shutil
                    shutil.move(str(downloaded_file), str(final_target))
                    logger.info(f"Moved directly into [{self.session_download_dir.name}]: {proper_name} ({final_target.stat().st_size:,} bytes)")
                elif downloaded_file.name != proper_name:
                    downloaded_file.rename(final_target)
                    logger.info(f"Renamed to clean proper name: {proper_name} ({final_target.stat().st_size:,} bytes)")
                else:
                    logger.info(f"Saved directly into [{self.session_download_dir.name}]: {proper_name} ({final_target.stat().st_size:,} bytes)")

                # Verify final file
                if verify_audio_file(final_target, expected_duration=dur):
                    logger.info(f"Verified in [{self.session_download_dir.name}]: {final_target.name} ({final_target.stat().st_size:,} bytes)")

                    processed += 1
                    await self.highlight_track_card(idx, color="#00ff88", label="SAVED & VERIFIED", project_id=project_id)

                    if not dry_run and self.delete_after:
                        logger.info(f"Deleting verified track from ElevenLabs: {proper_name}...")
                        del_ok = await self.delete_track_item(idx, final_target, project_id, title, dur)
                        if del_ok:
                            logger.info(f"Successfully deleted from web UI: {proper_name}")
                            outcome["status"] = "deleted"
                        else:
                            logger.warning(f"Could not delete {proper_name} from web UI")
                            outcome["status"] = "delete_failed"
                    else:
                        logger.info(f"[SAFE MODE] Track preserved online: {proper_name}")
                        outcome["status"] = "verified_online"
                else:
                    logger.warning(f"File verification failed for {proper_name}")
                    outcome["status"] = "verification_failed"
            else:
                logger.warning(f"Could not detect newly downloaded file for track #{idx+1}")
                outcome["status"] = "download_missing"

        # History briefly renders an empty list while its API response arrives.
        await asyncio.sleep(4.0)
        remaining_ids: set[str] = set()
        previous_ids: set[str] | None = None
        stable_reads = 0
        empty_state_confirmed = False
        deadline = asyncio.get_running_loop().time() + 45.0
        while asyncio.get_running_loop().time() < deadline:
            remaining_ids = {track["project_id"] for track in await self.scan_tracks(ready_only=False)}
            if not remaining_ids:
                response = await self.agent.server.send_command(Command(
                    action="execute_script",
                    params={"script": js("Boolean(document.querySelector(@@main@@)?.innerText?.includes(@@history_empty_state.text@@))")},
                ))
                empty_state_confirmed = (
                    response.status == "success" and
                    response.data.extracted_data.get("result") is True
                )
            if remaining_ids or empty_state_confirmed:
                stable_reads = stable_reads + 1 if remaining_ids == previous_ids else 1
            else:
                stable_reads = 0
            if stable_reads >= 4:
                break
            previous_ids = remaining_ids
            await asyncio.sleep(0.5)
        reconciliation_verified = stable_reads >= 4
        for project_id, outcome in outcomes.items():
            outcome["still_online"] = project_id in remaining_ids if reconciliation_verified else None
            if outcome["status"] == "deleted" and (outcome["still_online"] or not reconciliation_verified):
                outcome["status"] = "delete_unverified"
        report = {
            "checked_at": datetime.now().isoformat(),
            "session": self.session_download_dir.name,
            "processed": processed,
            "initial_online": len(tracks),
            "remaining_online": len(remaining_ids) if reconciliation_verified else None,
            "reconciliation_verified": reconciliation_verified,
            "history_empty_confirmed": empty_state_confirmed,
            "projects": list(outcomes.values()),
        }
        report_path = self.session_download_dir / "deletion_report.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        logger.info(f"Final reconciliation: {len(tracks)} initially online, "
                    f"{len(remaining_ids) if reconciliation_verified else 'unknown'} remain. "
                    f"Report: {report_path}")
        return report


async def main():
    parser = argparse.ArgumentParser(description="ElevenLabs Downloader & Workspace Cleaner")
    parser.add_argument("--keep", action="store_true", help="Do not delete tracks from website after download")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Simulate execution without deleting anything")
    parser.add_argument("--force-delete", action="store_true", help="Explicitly allow deleting verified tracks from the web UI")
    parser.add_argument("--downloads-dir", type=str, default="", help="Custom base download directory")
    parser.add_argument("--skip-existing", action="store_true", help="Skip download if local file already exists")
    parser.add_argument("--max-items", type=int, default=50, help="Max items to process in one run")
    args = parser.parse_args()

    is_dry_run = not args.force_delete
    delete_enabled = args.force_delete and not args.keep
    force_redownload = not args.skip_existing

    agent = BrowserAgent()
    logger.info("Connecting to AI Web-Auto WebSocket...")
    await agent.connect()
    logger.info("Connected to browser session!")

    try:
        # Preflight: app tab open and in front, history loaded and ready, signed in.
        preflight = await module_kit.preflight(
            KNOWLEDGE, agent.server, "delete_track" if delete_enabled else "download_track")
        logger.info(preflight.summary())
        if not preflight.ok:
            raise SystemExit(1)

        out_base_dir = Path(args.downloads_dir) if args.downloads_dir else BASE_DOWNLOAD_DIR
        cleaner = ElevenLabsCleaner(
            agent=agent,
            base_download_dir=out_base_dir,
            delete_after=delete_enabled,
            force_redownload=force_redownload
        )
        await cleaner.process_all(max_items=args.max_items, dry_run=is_dry_run)
    finally:
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())

