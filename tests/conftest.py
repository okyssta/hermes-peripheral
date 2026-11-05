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
    for path in (str(REPO_ROOT.parent), str(HERMES_SRC), str(REPO_ROOT / "tests")):
        if path not in sys.path:
            sys.path.insert(0, path)
    # A checkout can be named anything (`hermes-peripheral`, `verify-922b`, …); the
    # package must still import as `peripheral`, because that is the name the plugin
    # host, the tests and the relative imports all use. A one-entry symlink directory
    # is used instead of a hand-built module spec so pytest's own import machinery
    # behaves exactly as it does in a normally named checkout.
    if REPO_ROOT.name != "peripheral" and "peripheral" not in sys.modules:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "peripheral", REPO_ROOT / "__init__.py",
            submodule_search_locations=[str(REPO_ROOT)],
        )
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            sys.modules["peripheral"] = module
            spec.loader.exec_module(module)


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


class FakeHost:
    """The plugin-host surface `register()` uses, without the gateway in the way."""

    def __init__(self, config: dict | None = None) -> None:
        self.config = dict(config or {})
        self.tools: dict[str, object] = {}
        self.commands: dict[str, object] = {}
        self.hooks: dict[str, list] = {}
        self.skills: list[str] = []

    def register_tool(self, name, toolset=None, schema=None, handler=None, **kwargs):
        self.tools[name] = handler

    def register_command(self, name, handler, description="", **kwargs):
        self.commands[name] = handler

    def register_hook(self, name, fn, **kwargs):
        self.hooks.setdefault(name, []).append(fn)

    def register_skill(self, name, path, **kwargs):
        self.skills.append(name)

    def get_config(self, key, default=None, **kwargs):
        return self.config.get(key, default)

    def set_config(self, key, value):
        self.config[key] = value


@pytest.fixture()
def plugin_host(hermes_home, tmp_path):
    """A registered plugin with one empty screenshot folder, torn down afterwards.

    Global module state (the ctx, the two background threads, the settings cache)
    is reset on both sides so a test that starts the watcher cannot leak a thread
    into the next one.
    """
    import peripheral
    import peripheral.db as db

    peripheral._CTX = None
    peripheral.invalidate_settings()
    screens = tmp_path / "screens"
    screens.mkdir()
    host = FakeHost({"watch_dirs": [str(screens)], "auto_scan_minutes": 0})
    peripheral.register(host)
    try:
        yield host, screens
    finally:
        peripheral.set_enabled(False)
        peripheral.set_background_capture(False)
        peripheral._CTX = None
        peripheral.invalidate_settings()
        db.close()
