"""The opportunity chime: one WAV (generated once, stdlib only) played by the desktop alert and the deal monitor page."""
from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

CHIME = Path(__file__).resolve().parent / "sounds" / "opportunity.wav"
NOTES = [(880.0, 0.16), (1174.66, 0.16), (1567.98, 0.42)]  # A5, D6, G6: rising, unlike the system "exclamation"
RATE = 22050


def make_chime(path: Path = CHIME, repeats: int = 2) -> Path:
    """Write the chime (16-bit mono PCM) if it doesn't exist yet: the three notes twice, soft attack and decay."""
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = bytearray()
    for r in range(repeats):
        for freq, dur in NOTES:
            n = int(RATE * dur)
            for i in range(n):
                t = i / RATE
                env = min(1.0, i / (RATE * 0.01)) * math.exp(-3.2 * t / dur)  # 10 ms attack, exponential decay
                s = 0.42 * env * (math.sin(2 * math.pi * freq * t) + 0.25 * math.sin(4 * math.pi * freq * t))
                frames += struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767))
        if r < repeats - 1:
            frames += b"\x00\x00" * int(RATE * 0.18)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(frames))
    return path


def play_opportunity() -> bool:
    """Play the chime to the end (synchronous: a --once run exits right after its alerts). False if it couldn't play."""
    try:
        import winsound
        winsound.PlaySound(str(make_chime()), winsound.SND_FILENAME)
        return True
    except Exception:
        return False
