"""Image inspection: dimensions, perceptual hash, thumbnails, capture time.

Kept separate from OCR so the plugin still indexes a screenshot whose text
Tesseract cannot read, and separate from the database so every helper here is a
pure function of a file path (which makes it testable without a plugin host).
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

IMAGE_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".avif",
}
MAX_FILE_BYTES = 24 * 1024 * 1024
THUMB_EDGE = 360

# macOS: "Screenshot 2026-11-05 at 14.32.10.png" / "Screen Shot 2024-01-02 at 3.04.05 PM.png"
# Windows/other: "2026-11-05 14-32-10.png", "Screenshot_20261105-143210.png"
_PATTERNS = (
    re.compile(r"(?P<y>20\d{2})-(?P<m>\d{2})-(?P<d>\d{2})[ _]at[ _](?P<H>\d{1,2})[.:](?P<M>\d{2})[.:](?P<S>\d{2})(?:\s*(?P<ap>AM|PM))?", re.I),
    re.compile(r"(?P<y>20\d{2})-(?P<m>\d{2})-(?P<d>\d{2})[ _T](?P<H>\d{2})[-.:](?P<M>\d{2})[-.:](?P<S>\d{2})"),
    re.compile(r"(?P<y>20\d{2})(?P<m>\d{2})(?P<d>\d{2})[-_ ](?P<H>\d{2})(?P<M>\d{2})(?P<S>\d{2})"),
)


@dataclass(frozen=True)
class ImageInfo:
    path: str
    width: int
    height: int
    bytes: int
    sha256: str
    dhash: str
    captured_at: float


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def capture_time(path: str) -> float:
    """Timestamp from the filename when it carries one, else the file mtime.

    Screenshot tools stamp the moment of capture into the name; copying a file
    rewrites mtime, so the name is the better evidence when it parses.
    """
    stem = Path(path).stem
    for pattern in _PATTERNS:
        match = pattern.search(stem)
        if not match:
            continue
        parts = {key: int(value) for key, value in match.groupdict().items() if key != "ap" and value}
        hour = parts.get("H", 0)
        if (match.groupdict().get("ap") or "").upper() == "PM" and hour < 12:
            hour += 12
        if (match.groupdict().get("ap") or "").upper() == "AM" and hour == 12:
            hour = 0
        try:
            return time.mktime(
                (
                    parts.get("y", 1970), parts.get("m", 1), parts.get("d", 1),
                    hour, parts.get("M", 0), parts.get("S", 0), 0, 0, -1,
                )
            )
        except (OverflowError, ValueError):
            break
    try:
        return os.path.getmtime(path)
    except OSError:
        return time.time()


def _open(path: str):
    from PIL import Image, ImageOps

    image = Image.open(path)
    image.load()
    return ImageOps.exif_transpose(image)


def dhash(path: str, *, size: int = 8) -> str:
    """64-bit difference hash as 16 hex chars; empty when the file cannot be read."""
    try:
        image = _open(path).convert("L").resize((size + 1, size), resample=1)
    except Exception:
        return ""
    pixels = bytes(image.tobytes())
    bits = 0
    index = 0
    for row in range(size):
        offset = row * (size + 1)
        for column in range(size):
            left = pixels[offset + column]
            right = pixels[offset + column + 1]
            if left > right:
                bits |= 1 << index
            index += 1
    return f"{bits:016x}"


def hamming(left: str, right: str) -> int:
    try:
        return bin(int(left, 16) ^ int(right, 16)).count("1")
    except (TypeError, ValueError):
        return 64


def inspect(path: str) -> ImageInfo | None:
    """Read the facts we store for one file; None when it is not a usable image."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size <= 0 or size > MAX_FILE_BYTES:
        return None
    try:
        image = _open(path)
        width, height = image.size
    except Exception:
        return None
    try:
        digest = sha256_file(path)
    except OSError:
        return None
    return ImageInfo(
        path=str(path),
        width=int(width),
        height=int(height),
        bytes=int(size),
        sha256=digest,
        dhash=dhash(path),
        captured_at=capture_time(path),
    )


def save_thumbnail(source: str, destination: Path) -> str:
    """Write a small WebP next to the index so a deleted screenshot still resolves."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        image = _open(source).convert("RGB")
        image.thumbnail((THUMB_EDGE, THUMB_EDGE))
        image.save(destination, format="WEBP", quality=72, method=2)
    except Exception:
        return ""
    return str(destination)


def save_frame(source: str, destination: Path, *, max_edge: int = 1600, quality: int = 82) -> str:
    """Store a captured screen as a compact WebP the index owns.

    Captures are ours, not the user's: they are written under plugin-data and can
    be deleted outright on ``forget``. 1600 px on the long edge keeps small UI text
    legible to a vision model while staying around 100–250 KB per frame.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        image = _open(source).convert("RGB")
        if max(image.size) > max_edge:
            image.thumbnail((max_edge, max_edge))
        image.save(destination, format="WEBP", quality=quality, method=3)
    except Exception:
        return ""
    try:
        return str(destination) if destination.stat().st_size > 0 else ""
    except OSError:
        return ""


def data_url(path: str, *, max_edge: int = 1568, budget_chars: int = 900_000) -> str:
    """A JPEG data URL small enough to attach to a tool result, or ''.

    Downscales by steps and drops the JPEG quality until the base64 body fits the
    budget, so attaching a screen can never wedge a turn on a provider size limit.
    """
    import base64
    import io

    try:
        original = _open(path).convert("RGB")
    except Exception:
        return ""
    for edge in (max_edge, 1200, 900, 700, 520):
        candidate = original.copy()
        if max(candidate.size) > edge:
            candidate.thumbnail((edge, edge))
        for quality in (82, 68, 55, 42):
            buffer = io.BytesIO()
            try:
                candidate.save(buffer, format="JPEG", quality=quality, optimize=True)
            except Exception:
                return ""
            payload = base64.b64encode(buffer.getvalue()).decode("ascii")
            if len(payload) <= budget_chars:
                return f"data:image/jpeg;base64,{payload}"
    return ""


def is_text_image(text: str, width: int, height: int) -> bool:
    """Screenshots and documents carry text; photos and art do not.

    Two independent signals, because both have blind spots: a sparse UI dialog has
    few characters but they are real and dense in context, while a photo of a
    landscape can produce a handful of OCR artifacts. Twenty characters of text —
    or a high density of it — is the line.
    """
    try:
        from .ocr import text_density
    except ImportError:  # imported as a top-level module (tests, ad-hoc use)
        from ocr import text_density  # type: ignore[no-redef]
    import re

    alnum = len(re.findall(r"[^\W_]", text or "", re.UNICODE))
    return alnum >= 20 or text_density(text, width, height) >= 0.5
