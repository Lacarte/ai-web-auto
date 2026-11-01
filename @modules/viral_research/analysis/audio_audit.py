"""Audit a set of music tracks before publication: loudness, true peak, dynamics, tempo, brightness, silences,
EQ change against an original, repeated passages inside a track, shared passages between tracks, and how each
track starts and ends. numpy + ffmpeg only.

Similarity scores here are engineering signals for a human listening check: they can show that two tracks
share a passage, never that a track is legally original.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np

SR = 22050
NFFT = 4096
HOP = 2048
BLOCK = 5          # STFT frames per similarity block (~0.46 s each -> ~2.3 s)
WIN = 6            # blocks per compared passage (~14 s)
TRIM = (5, 15)     # seconds left out at the start / end (fades look alike in every track)


def decode(ffmpeg: str, path: Path, sr: int = SR) -> np.ndarray:
    out = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                         capture_output=True).stdout
    return np.frombuffer(out, dtype=np.float32)


def loudness(ffmpeg: str, path: Path) -> dict:
    """EBU R128 integrated loudness, loudness range and true peak."""
    err = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
                         capture_output=True, text=True, errors="replace").stderr
    tail = err[err.rfind("Summary:"):]

    def grab(key):
        m = re.search(key + r":\s+(-?[\d.]+|-inf)", tail)
        return float(m.group(1)) if m and m.group(1) != "-inf" else None
    return {"integrated_lufs": grab("I"), "lra_lu": grab("LRA"), "true_peak_dbtp": grab("Peak")}


def silences(ffmpeg: str, path: Path, floor_db: int = -50, min_s: float = 1.5) -> list[float]:
    err = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path), "-af", f"silencedetect=n={floor_db}dB:d={min_s}", "-f", "null", "-"],
                         capture_output=True, text=True, errors="replace").stderr
    return [round(float(x), 1) for x in re.findall(r"silence_duration:\s*([\d.]+)", err)]


def spectrum(y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    frames = np.lib.stride_tricks.sliding_window_view(y, NFFT)[::HOP] * np.hanning(NFFT)
    return np.abs(np.fft.rfft(frames, axis=1)), np.fft.rfftfreq(NFFT, 1 / SR)


def band_db(mag: np.ndarray, freqs: np.ndarray) -> dict:
    edges = [20, 150, 500, 2000, 6000, 11000]
    tot = (mag ** 2).sum() + 1e-12
    return {f"{lo}-{hi}Hz": round(float(10 * np.log10((mag[:, (freqs >= lo) & (freqs < hi)] ** 2).sum() / tot + 1e-12)), 2)
            for lo, hi in zip(edges, edges[1:])}


def tempo(mag: np.ndarray) -> tuple[float, float]:
    """(BPM estimate, beat clarity). Clarity ~0.1-0.35 = free time / no steady beat, > 0.6 = a clear steady beat.
    The BPM can be off by a factor of two; it means little when clarity is low."""
    flux = np.maximum(np.diff(np.log1p(mag), axis=0), 0).sum(axis=1)
    flux = (flux - flux.mean()) / (flux.std() + 1e-9)
    fps = SR / HOP
    ac = np.correlate(flux, flux, "full")[len(flux) - 1:]
    ac /= ac[0] + 1e-9
    lags = np.arange(len(ac)) / fps
    ok = (lags >= 60 / 160) & (lags <= 60 / 50)
    i = int(np.argmax(np.where(ok, ac, -1)))
    return round(float(60 / lags[i]), 1), round(float(ac[i]), 3)


def texture(mag: np.ndarray, freqs: np.ndarray) -> np.ndarray:
    """Per ~2.3-s block: 12 pitch classes + 16 log bands, each z-scored over the track (so a constant key or timbre
    doesn't read as repetition), rows unit-normalised."""
    ok = (freqs > 60) & (freqs < 5000)
    pc = (np.round(12 * np.log2(freqs[ok] / 440.0)) % 12).astype(int)
    chroma = np.stack([(mag[:, ok][:, pc == k] ** 2).sum(1) for k in range(12)], 1)
    chroma = np.log1p(chroma / (chroma.sum(1, keepdims=True) + 1e-9) * 100)
    edges = np.geomspace(60, 10000, 17)
    bands = np.stack([np.log1p((mag[:, (freqs >= lo) & (freqs < hi)] ** 2).sum(1)) for lo, hi in zip(edges, edges[1:])], 1)
    f = np.concatenate([chroma, bands], 1)
    m = len(f) // BLOCK
    f = f[: m * BLOCK].reshape(m, BLOCK, -1).mean(1)
    f = (f - f.mean(0)) / (f.std(0) + 1e-9)
    return f / (np.linalg.norm(f, axis=1, keepdims=True) + 1e-9)


def best_passage(a: np.ndarray, b: np.ndarray, same_track: bool = False) -> dict | None:
    """Highest mean similarity along any diagonal over WIN consecutive blocks = the most alike ~14-s passages."""
    s = a @ b.T
    best, where = -1.0, None
    for off in range(-(len(a) - WIN), len(b) - WIN + 1):
        if same_track and off < WIN:
            continue
        d = np.diagonal(s, offset=off)
        if len(d) < WIN:
            continue
        r = np.convolve(d, np.ones(WIN) / WIN, "valid")
        i = int(r.argmax())
        if r[i] > best:
            best, where = float(r[i]), (off, i)
    if where is None:
        return None
    off, i = where
    blk = BLOCK * HOP / SR
    return {"score": round(best, 3), "a_at_s": round(TRIM[0] + (i + max(0, -off)) * blk, 1),
            "b_at_s": round(TRIM[0] + (i + max(0, off)) * blk, 1)}


def edges(y: np.ndarray) -> dict:
    def db(x):
        return float(20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9))
    body = float(np.median([db(y[i:i + SR]) for i in range(0, len(y) - SR, SR)]))
    first, last = db(y[: SR // 2]), db(y[-2 * SR:])
    return {"body_db": round(body, 1), "first_half_second_db": round(first, 1), "last_2s_db": round(last, 1),
            "hard_start": first > body - 6, "fades_out": last < body - 12}


def audit(ffmpeg: str, tracks: list[Path], originals: dict[str, Path] | None = None) -> dict:
    """tracks in playlist order; originals {name: path} for the EQ comparison. Returns per-track measures, pair and
    self similarity with the unrelated-pair baseline, and the level change at each join."""
    originals = originals or {}
    out, tex, ends = {}, {}, {}
    for p in tracks:
        y = decode(ffmpeg, p)
        mag, freqs = spectrum(y)
        bpm, clarity = tempo(mag)
        centroid = (mag * freqs).sum(axis=1) / (mag.sum(axis=1) + 1e-9)
        r = {"duration_s": round(len(y) / SR, 2), **loudness(ffmpeg, p), "tempo_bpm_est": bpm, "beat_clarity": clarity,
             "centroid_hz": int(np.median(centroid)), "band_db": band_db(mag, freqs), "silences_s": silences(ffmpeg, p),
             "edges": edges(y)}
        o = originals.get(p.name)
        if o and o.exists():
            mo, _ = spectrum(decode(ffmpeg, o))
            r["original"] = loudness(ffmpeg, o)
            r["eq_change_db"] = {k: round(r["band_db"][k] - v, 2) for k, v in band_db(mo, freqs).items()}
        body = y[TRIM[0] * SR: len(y) - TRIM[1] * SR]
        tex[p.name] = texture(*spectrum(body))
        ends[p.name] = (y[-3 * SR:], y[: 3 * SR])
        out[p.name] = r
    names = [p.name for p in tracks]
    stamp = lambda n: n.rsplit("_", 1)[-1]   # files named <title>_<generation timestamp>.mp3 come in pairs
    pairs = [{"a": a, "b": b, "same_generation": stamp(a) == stamp(b), **(best_passage(tex[a], tex[b]) or {"score": None})}
             for i, a in enumerate(names) for b in names[i + 1:]]
    unrelated = sorted(p["score"] for p in pairs if not p["same_generation"] and p["score"] is not None)
    p95 = unrelated[int(0.95 * (len(unrelated) - 1))] if unrelated else None
    for p in pairs:
        p["above_unrelated_p95"] = bool(p95 is not None and p["score"] is not None and p["score"] > p95)
    joins = []
    for a, b in zip(names, names[1:]):
        def db(x):
            return float(20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9))
        joins.append({"from": a, "to": b, "end_of_a_db": round(db(ends[a][0]), 1), "start_of_b_db": round(db(ends[b][1]), 1),
                      "jump_db": round(db(ends[b][1]) - db(ends[a][0]), 1), "b_hard_start": out[b]["edges"]["hard_start"]})
    return {"tracks": out, "pairs": pairs, "unrelated_pair_p95": p95,
            "unrelated_pair_median": float(np.median(unrelated)) if unrelated else None,
            "self_repeat": {n: best_passage(tex[n], tex[n], same_track=True) for n in names}, "joins": joins,
            "method": {"sample_rate": SR, "block_seconds": round(BLOCK * HOP / SR, 2), "window_blocks": WIN, "trim_seconds": TRIM}}
