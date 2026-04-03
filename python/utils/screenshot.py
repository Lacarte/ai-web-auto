"""Screenshot processing, OCR, and visual diff utilities."""

from __future__ import annotations

import base64
import io
from typing import Optional

from loguru import logger

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    import pytesseract
    HAS_OCR = True
except ImportError:
    HAS_OCR = False


class ScreenshotProcessor:
    @staticmethod
    def decode(b64: str) -> "Image.Image":
        return Image.open(io.BytesIO(base64.b64decode(b64)))

    @staticmethod
    def encode(img: "Image.Image", fmt: str = "PNG") -> str:
        buf = io.BytesIO()
        img.save(buf, format=fmt)
        return base64.b64encode(buf.getvalue()).decode()

    @staticmethod
    def crop_element(b64: str, rect: dict) -> str:
        """Crop a screenshot to a bounding rect {x, y, width, height}."""
        img = ScreenshotProcessor.decode(b64)
        x, y, w, h = rect["x"], rect["y"], rect["width"], rect["height"]
        cropped = img.crop((x, y, x + w, y + h))
        return ScreenshotProcessor.encode(cropped)

    @staticmethod
    def annotate(b64: str, elements: list[dict], highlight_index: int | None = None) -> str:
        """Draw bounding boxes on interactive elements. Highlight one if specified."""
        img = ScreenshotProcessor.decode(b64)
        draw = ImageDraw.Draw(img)
        for i, el in enumerate(elements):
            rect = el.get("rect")
            if not rect:
                continue
            x, y, w, h = rect["x"], rect["y"], rect["width"], rect["height"]
            color = "red" if i == highlight_index else "blue"
            draw.rectangle([x, y, x + w, y + h], outline=color, width=2)
            draw.text((x + 2, y + 2), str(i), fill=color)
        return ScreenshotProcessor.encode(img)

    @staticmethod
    def ocr(b64: str) -> str:
        """Extract text from screenshot via OCR."""
        if not HAS_OCR:
            logger.warning("pytesseract not installed, OCR unavailable")
            return ""
        img = ScreenshotProcessor.decode(b64)
        return pytesseract.image_to_string(img)

    @staticmethod
    def visual_diff(b64_a: str, b64_b: str, threshold: float = 0.01) -> dict:
        """Compare two screenshots and return diff info."""
        if not HAS_CV2:
            logger.warning("opencv not installed, visual diff unavailable")
            return {"changed": False, "similarity": 1.0}

        img_a = np.array(ScreenshotProcessor.decode(b64_a).convert("RGB"))
        img_b = np.array(ScreenshotProcessor.decode(b64_b).convert("RGB"))

        # Resize to match if needed
        if img_a.shape != img_b.shape:
            img_b = cv2.resize(img_b, (img_a.shape[1], img_a.shape[0]))

        gray_a = cv2.cvtColor(img_a, cv2.COLOR_RGB2GRAY)
        gray_b = cv2.cvtColor(img_b, cv2.COLOR_RGB2GRAY)

        diff = cv2.absdiff(gray_a, gray_b)
        non_zero = np.count_nonzero(diff > 25)
        total = diff.size
        change_ratio = non_zero / total
        similarity = 1.0 - change_ratio

        return {
            "changed": change_ratio > threshold,
            "similarity": round(similarity, 4),
            "change_ratio": round(change_ratio, 4),
        }

    @staticmethod
    def stitch_vertical(images_b64: list[str]) -> str:
        """Stitch multiple screenshots vertically for full-page capture."""
        images = [ScreenshotProcessor.decode(b) for b in images_b64]
        if not images:
            return ""
        width = max(img.width for img in images)
        total_height = sum(img.height for img in images)
        result = Image.new("RGB", (width, total_height))
        y_offset = 0
        for img in images:
            result.paste(img, (0, y_offset))
            y_offset += img.height
        return ScreenshotProcessor.encode(result)
