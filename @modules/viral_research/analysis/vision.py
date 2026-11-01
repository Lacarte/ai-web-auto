"""Thumbnails and opening frames read by the user's local vision model (on-device: llama-server + mmproj).

Same server setup as on-device's vlm-test/tools/vlm_ocr.py. MiniCPM-V 4.7 1B is the default: it scored
best of the local vision models on that kit (mean F1 0.93 vs 0.86 for Bonsai 2 2B) and is faster on CPU.

Four short questions per image at 448 px, not one long form: asked everything at once, the 1B model dropped the
TEXT line, missed a visible face and miscounted people (checked against the images, 2026-10-25). Each question
re-encodes the image (cache_prompt did not reuse it), so an image takes ~25-30 s on this CPU.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

ASK_TEXT = "Transcribe all the text written in this image exactly, in reading order. If there is no text at all, answer NONE."
ASK_FACE = "Is a face visible in this image - a real, painted, drawn or rendered human face? Answer only yes or no."
# "text" is not a style choice: offered it, the model picked it for 3 of 9 photos/paintings with a big title.
ASK_STYLE = ("Ignore any written text. Which one word best describes how the picture itself is made: photo, "
             "illustration, anime, or 3d? Answer with one word.")
ASK_SUBJECT = "Describe the main subject of this image in at most 8 words."
QUESTIONS = {"text": (ASK_TEXT, 160), "face": (ASK_FACE, 5), "style": (ASK_STYLE, 5), "subject": (ASK_SUBJECT, 30)}
STYLES = {"photo": "photo", "photograph": "photo", "realistic": "photo", "illustration": "illustration",
          "painting": "illustration", "painted": "illustration", "drawing": "illustration", "anime": "anime",
          "cartoon": "anime", "3d": "3d", "render": "3d", "cgi": "3d"}


def parse_text(answer: str) -> str | None:
    t = re.sub(r"^\W*(text|transcription)\s*:\s*", "", (answer or "").strip(), flags=re.I).strip().strip('"')
    if not t or re.fullmatch(r"(none|no text|there is no text.*|n/?a|-|nothing)\.?", t, re.I | re.S):
        return None
    return t[:200]


def parse_yes_no(answer: str) -> bool | None:
    """yes / no - and the 1B model's habit of naming the kind of face instead ("painted", "drawn")."""
    a = (answer or "").strip().lower()
    if re.match(r"\W*(yes|painted|drawn|rendered|real|illustrated|animated)\b", a):
        return True
    if re.match(r"\W*(no|none)\b", a):
        return False
    return None


def parse_style(answer: str) -> str | None:
    return next((STYLES[w] for w in re.findall(r"[a-z0-9]+", (answer or "").lower()) if w in STYLES), None)


def labels(text_answer: str, face_answer: str, style_answer: str, subject_answer: str = "") -> dict:
    text = parse_text(text_answer)
    style, face = parse_style(style_answer), parse_yes_no(face_answer)
    subject = re.sub(r"\s+", " ", (subject_answer or "").strip().strip('"'))[:80] or None
    return {"text": text, "text_words": len(re.findall(r"\w+", text or "")), "face": face, "style": style,
            "subject": subject, "answered": sum(x is not None for x in (face, style)) + 1}


def reusable(previous: dict | None) -> dict:
    """Answers in an earlier label file whose question is still worded the same (they need not be asked again)."""
    asked, answers = (previous or {}).get("asked") or {}, (previous or {}).get("answers") or {}
    return {k: answers[k] for k, (q, _) in QUESTIONS.items() if k in answers and asked.get(k) == q}


def vision_features(thumb: dict | None, opening: dict | None) -> dict:
    """Countable features for the outlier/control comparison (only from answers that parsed)."""
    f = {}
    if thumb:
        f.update(thumb_text=thumb["text_words"] > 0, thumb_text_words=thumb["text_words"])
        if thumb.get("face") is not None:
            f["thumb_face"] = thumb["face"]
        if thumb.get("style"):
            for s in ("photo", "illustration", "anime", "3d"):
                f[f"thumb_{s}"] = thumb["style"] == s
    if opening:
        f["opening_text"] = opening["text_words"] > 0
        if opening.get("face") is not None:
            f["opening_face"] = opening["face"]
    return f


class LocalVision:
    """Starts llama-server with a vision model, asks the four questions about images, stops it."""

    def __init__(self, server: str, model: str, mmproj: str, ffmpeg: str, port: int = 8098, threads: int = 4,
                 log: Path | None = None):
        self.port, self.ffmpeg = port, ffmpeg
        for p in (server, model, mmproj):
            if not Path(p).exists():
                raise FileNotFoundError(p)
        self.log = open(log or Path(tempfile.gettempdir()) / "vr-vision-server.log", "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen([server, "-m", model, "--mmproj", mmproj, "-t", str(threads), "-c", "4096", "--jinja",
                                      "--reasoning", "off", "--host", "127.0.0.1", "--port", str(port), "--no-webui"],
                                     stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.time() + 300
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"llama-server exited (see {self.log.name})")
            try:
                if urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2).status == 200:
                    return
            except Exception:
                time.sleep(1)
        raise RuntimeError("llama-server did not become ready in 5 minutes")

    def _ask(self, image_b64: str, question: str, max_tokens: int) -> str:
        body = {"messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image_b64}},
                    {"type": "text", "text": question}]}],
                "temperature": 0, "repeat_penalty": 1.05, "max_tokens": max_tokens, "cache_prompt": True,
                "chat_template_kwargs": {"enable_thinking": False}}
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/chat/completions", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=900).read())["choices"][0]["message"].get("content") or ""

    def look(self, image: Path, previous: dict | None = None) -> dict:
        """Labels for one image; only the questions not already answered in `previous` (same wording) are asked."""
        answers = reusable(previous)
        t = time.perf_counter()
        if len(answers) < len(QUESTIONS):
            small = Path(tempfile.gettempdir()) / f"vr-vision-{image.parent.name}-{image.stem}.jpg"
            subprocess.run([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(image), "-vf",
                            "scale='min(448,iw)':-2", "-q:v", "4", str(small)], capture_output=True, timeout=60)
            b64 = base64.b64encode((small if small.exists() else image).read_bytes()).decode()
            small.unlink(missing_ok=True)
            for k, (q, max_tokens) in QUESTIONS.items():
                if k not in answers:
                    answers[k] = self._ask(b64, q, max_tokens)[:400]
        return {**labels(answers["text"], answers["face"], answers["style"], answers["subject"]),
                "asked": {k: q for k, (q, _) in QUESTIONS.items()}, "answers": answers,
                "seconds": round(time.perf_counter() - t, 1)}

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.log.close()
