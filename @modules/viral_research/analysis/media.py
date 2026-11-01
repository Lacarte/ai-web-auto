"""Measurements of a downloaded video with ffmpeg / ffprobe (Seamless-app\\bin): pacing, loudness, opening.

Measured, not described: cuts come from scene-change detection, the opening from frames and audio levels.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


class Media:
    def __init__(self, ffmpeg_dir: str):
        d = Path(ffmpeg_dir)
        self.ffmpeg = str(d / "ffmpeg.exe") if (d / "ffmpeg.exe").exists() else "ffmpeg"
        self.ffprobe = str(d / "ffprobe.exe") if (d / "ffprobe.exe").exists() else "ffprobe"

    def _ff(self, args: list[str], timeout: int = 600) -> str:
        p = subprocess.run([self.ffmpeg, "-hide_banner", "-nostats", *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.stderr

    def probe(self, path: Path) -> dict:
        p = subprocess.run([self.ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,r_frame_rate",
                            "-of", "json", str(path)], capture_output=True, text=True, timeout=120)
        doc = json.loads(p.stdout or "{}")
        video = next((s for s in doc.get("streams", []) if s.get("codec_type") == "video"), {})
        num, _, den = (video.get("r_frame_rate") or "0/1").partition("/")
        return {"duration": float(doc.get("format", {}).get("duration") or 0), "width": video.get("width"),
                "height": video.get("height"), "fps": round(float(num) / float(den or 1), 2) if float(den or 1) else None,
                "has_audio": any(s.get("codec_type") == "audio" for s in doc.get("streams", []))}

    def scene_cuts(self, path: Path, threshold: float = 0.3) -> list[float]:
        err = self._ff(["-i", str(path), "-an", "-vf", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"])
        return [round(float(t), 2) for t in re.findall(r"pts_time:([\d.]+)", err)]

    def loudness(self, path: Path) -> dict:
        err = self._ff(["-i", str(path), "-vn", "-af", "ebur128=peak=none", "-f", "null", "-"])
        integ = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
        lra = re.findall(r"LRA:\s+([\d.]+) LU", err)
        return {"lufs": float(integ[-1]) if integ else None, "lra": float(lra[-1]) if lra else None}

    def mean_volume(self, path: Path, start: float = 0, length: float | None = None) -> float | None:
        args = ["-ss", str(start)] + (["-t", str(length)] if length else []) + ["-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"]
        m = re.search(r"mean_volume:\s+(-?[\d.]+) dB", self._ff(args))
        return float(m.group(1)) if m else None

    def silence_share(self, path: Path, duration: float) -> float | None:
        err = self._ff(["-i", str(path), "-vn", "-af", "silencedetect=noise=-35dB:d=0.4", "-f", "null", "-"])
        total = sum(float(x) for x in re.findall(r"silence_duration:\s+([\d.]+)", err))
        return round(total / duration, 3) if duration else None

    def frames(self, path: Path, times: list[float], out_dir: Path) -> list[str]:
        out_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for t in times:
            out = out_dir / f"frame_{t:05.1f}s.jpg"
            if not out.exists():
                self._ff(["-ss", str(t), "-i", str(path), "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", "-y", str(out)], timeout=120)
            if out.exists():
                saved.append(str(out))
        return saved

    def contact_sheet(self, path: Path, duration: float, out: Path, tiles: int = 9) -> str | None:
        if out.exists():
            return str(out)
        step = max(duration / tiles, 0.5)
        self._ff(["-i", str(path), "-vf", f"fps=1/{step:.3f},scale=320:-2,tile=3x3", "-frames:v", "1", "-q:v", "5", "-y", str(out)], timeout=300)
        return str(out) if out.exists() else None

    def measure(self, path: Path, out_dir: Path) -> dict:
        info = self.probe(path)
        duration = info["duration"]
        cuts = self.scene_cuts(path)
        result = {
            **info,
            "measured_seconds": round(duration, 1),
            "cuts": len(cuts),
            "cuts_per_min": round(len(cuts) / (duration / 60), 1) if duration else None,
            "first_cut_at": cuts[0] if cuts else None,
            "cut_times": cuts[:60],
            "frames": self.frames(path, [0.0, 1.0, 2.0, 3.0], out_dir / "frames"),
            "contact_sheet": self.contact_sheet(path, duration, out_dir / "contact_sheet.jpg"),
        }
        if info["has_audio"]:
            result.update(self.loudness(path))
            first = self.mean_volume(path, 0, 3)
            whole = self.mean_volume(path)
            result["first_3s_volume_db"] = first
            result["volume_db"] = whole
            result["starts_loud"] = first is not None and whole is not None and first >= whole - 3
            result["silence_share"] = self.silence_share(path, duration)
        return result
