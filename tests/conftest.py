"""Shared test harness: a temp HERMES_HOME, real screenshots, no plugin host.

The pipeline is exercised the way it runs in production — real files on disk, real
Tesseract subprocesses, the real SQLite schema — but against a throwaway profile so
nothing touches the developer's own index.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HERMES_SRC = Path(os.environ.get("HERMES_SRC", "/root/.hermes/hermes-agent"))


def pytest_configure(config):
    for path in (str(REPO_ROOT.parent), str(HERMES_SRC)):
        if path not in sys.path:
            sys.path.insert(0, path)


@pytest.fixture()
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    import peripheral.db as db

    db.close()
    yield home
    db.close()


def _font(size: int):
    from PIL import ImageFont

    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ):
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size=size)


def make_screenshot(path: Path, lines: list[str], *, size=(1280, 800), background=(255, 255, 255)) -> Path:
    """Render legible text to a PNG that looks like a screen region."""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)
    font = _font(28)
    y = 38
    for line in lines:
        draw.text((40, y), line, fill=(20, 20, 25), font=font)
        y += 44
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


@pytest.fixture()
def ocr_ready():
    from peripheral import ocr

    if not ocr.available():
        pytest.skip("tesseract is not installed")
    return ocr
