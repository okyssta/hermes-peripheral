#!/usr/bin/env python3
"""Reproduce the demo's screens on a machine with a display, and capture them.

The demo is not a mock-up: this script opens real browser windows on a real X
display, and the plugin's own capture path photographs them. It needs:

* an X display (``Xvfb :99 &`` works; ``DISPLAY=:99``),
* any Chromium/Chrome binary (``CHROME=/path/to/chrome``), and
* Tesseract for the text side (optional — the frames are still captured).

    DISPLAY=:99 CHROME=/usr/bin/chromium python3 scripts/demo_capture.py

Each page is captured through ``ingest.capture_and_ingest`` — the same function the
``screen_capture`` tool calls — so what the demo shows is what a user gets.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO.parent))
sys.path.insert(0, "/root/.hermes/hermes-agent")

PAGES = (
    ("stripe-error.html", "checkout error — card declined"),
    ("slack-thread.html", "Sam in #deploys about expired_card"),
    ("dashboard.html", "production dashboard, 402 spike"),
)
CHROME_CANDIDATES = (
    os.environ.get("CHROME", ""),
    "/root/.hermes/tools/chromium-1208/chrome-linux/chrome",
    shutil.which("chromium") or "",
    shutil.which("chromium-browser") or "",
    shutil.which("google-chrome") or "",
)


def chrome_binary() -> str:
    for candidate in CHROME_CANDIDATES:
        if candidate and Path(candidate).is_file():
            return candidate
    raise SystemExit("no Chromium/Chrome binary found; set CHROME=/path/to/chrome")


def open_page(binary: str, page: Path, width: int, height: int, profile: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [
            binary, "--no-sandbox", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check", "--disable-dev-shm-usage",
            f"--window-size={width},{height}", "--window-position=0,0", "--kiosk",
            f"--user-data-dir={profile}",
            f"--app=file://{page}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> int:
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        raise SystemExit("no DISPLAY: start Xvfb (Xvfb :99 &) and export DISPLAY=:99")
    os.environ.setdefault("HERMES_HOME", "/tmp/peripheral-demo-home")

    from peripheral import capture, db, ingest, settings as settings_mod

    screens_dir = Path(os.environ.get("DEMO_SCREENS", REPO / "demo-screens"))
    frames_out = Path(os.environ.get("DEMO_FRAMES", REPO / "out" / "demo-frames"))
    frames_out.mkdir(parents=True, exist_ok=True)

    print(f"capture backend: {capture.describe()}")
    if not capture.available_backends():
        raise SystemExit("no capture backend on this machine")

    settings = settings_mod.Settings.from_ctx(None) if False else settings_mod.Settings(
        enabled=True, watch_dirs=(), auto_scan_minutes=0, capture_enabled=False,
    )
    binary = chrome_binary()
    width, height = 1600, 1000

    for index, (name, label) in enumerate(PAGES, start=1):
        page = screens_dir / name
        if not page.is_file():
            print(f"skip {name}: not found")
            continue
        profile = Path(f"/tmp/peripheral-chrome-{index}")
        browser = open_page(binary, page, width, height, profile)
        time.sleep(5.0)  # let the page paint and settle
        outcome = ingest.capture_and_ingest(settings, force=True)
        print(f"{index}. {label}: {outcome.line()}")
        if outcome.shot_id:
            db.set_title(outcome.shot_id, label)
            shot = db.get(outcome.shot_id)
            if shot and Path(shot.path).is_file():
                target = frames_out / f"{index:02d}-{page.stem}.png"
                shutil.copyfile(shot.path, target)
                print(f"   frame -> {target}")
        browser.terminate()
        try:
            browser.wait(timeout=10)
        except subprocess.TimeoutExpired:
            browser.kill()
        shutil.rmtree(profile, ignore_errors=True)

    data = db.stats()
    print(f"\nindex: {data['count']} screens, {data['chars']:,} chars, {data['captured']} captured live")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
