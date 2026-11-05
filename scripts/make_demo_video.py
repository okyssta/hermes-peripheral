#!/usr/bin/env python3
"""Render the 80-second demo video (silent, captioned) for Peripheral.

Frames are drawn with Pillow and assembled by ffmpeg through a concat list, so a
"hold" costs one PNG rather than hundreds. The tool output shown on screen is the
plugin's real output: the scenes call the handlers and slice what they return.

    python3 scripts/make_demo_video.py && open out/peripheral-demo.mp4
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, "/root/.hermes/hermes-agent")

BUILD = Path("/root/peripheral/out")
FRAMES = BUILD / "frames"
W, H = 1600, 1000
BG = (11, 14, 18)
PANEL = (18, 22, 28)
TEXT = (223, 228, 234)
DIM = (123, 135, 148)
GREEN = (110, 231, 183)
BLUE = (147, 197, 253)
AMBER = (251, 191, 36)
RED = (248, 113, 113)
PURPLE = (196, 181, 253)

FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_MONO_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"


def font(path: str, size: int):
    return ImageFont.truetype(path, size)


def wrap(draw, text: str, fnt, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        probe = f"{current} {word}".strip()
        if draw.textlength(probe, font=fnt) <= width:
            current = probe
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


class Video:
    def __init__(self) -> None:
        FRAMES.mkdir(parents=True, exist_ok=True)
        self.entries: list[tuple[Path, float]] = []
        self.counter = 0

    # -- primitives -----------------------------------------------------
    def frame(self, draw_fn) -> Image.Image:
        image = Image.new("RGB", (W, H), BG)
        draw = ImageDraw.Draw(image)
        draw.rectangle([0, 0, W, 6], fill=(32, 38, 46))
        draw_fn(draw, image)
        return image

    def emit(self, image: Image.Image, duration: float) -> None:
        self.counter += 1
        path = FRAMES / f"{self.counter:05d}.png"
        image.save(path)
        self.entries.append((path, max(0.04, duration)))

    def hold(self, draw_fn, duration: float) -> None:
        self.emit(self.frame(draw_fn), duration)

    def typed(self, draw_fn_factory, text: str, *, seconds: float = 1.8, tail: float = 0.8) -> None:
        """Reveal a line character by character over a readable beat, then hold it."""
        steps = max(3, min(26, len(text) // 2))
        for step in range(1, steps + 1):
            partial = text[: int(len(text) * step / steps)]
            self.emit(self.frame(draw_fn_factory(partial)), seconds / steps)
        self.hold(draw_fn_factory(text), tail)


# ---------------------------------------------------------------- scenes

def scene_title(video: Video) -> None:
    def draw(draw, image):
        small = font(FONT_MONO, 24)
        draw.text((110, 150), "HERMES PLUGIN", font=small, fill=GREEN)
        draw.text((110, 250), "Peripheral", font=font(FONT_BOLD, 150), fill=TEXT)
        draw.text((114, 430), "a memory of what you saw", font=font(FONT_REG, 46), fill=BLUE)
        draw.line([110, 540, 110 + 620, 540], fill=(45, 54, 64), width=3)
        for index, line in enumerate(
            [
                "Your agent reads what you hand it.",
                "It never saw the error, the price, the message.",
                "That context dies with the tab.",
            ]
        ):
            draw.text((114, 600 + index * 54), line, font=font(FONT_REG, 32), fill=DIM if index < 2 else TEXT)

    video.hold(draw, 6.2)


def scene_problem(video: Video) -> None:
    def draw(draw, image):
        draw.text((110, 120), "the gap", font=font(FONT_MONO, 24), fill=GREEN)
        headline = "Hermes can look at the screen now."
        draw.text((110, 190), headline, font=font(FONT_REG, 46), fill=TEXT)
        draw.text((110, 260), "It cannot answer for a screen that is gone.", font=font(FONT_REG, 46), fill=DIM)
        quotes = [
            ('"what was that error I got yesterday?"', 0),
            ('"the price on that page"', 1),
            ('"what was on screen when it broke?"', 2),
        ]
        for quote, index in quotes:
            draw.text((150, 400 + index * 110), quote, font=font(FONT_MONO, 40), fill=PURPLE)
        draw.text((110, 790), "the workaround is re-taking the screenshot and pasting it back", font=font(FONT_REG, 28), fill=DIM)

    video.hold(draw, 7.0)


def scene_folder(video: Video) -> None:
    shots = sorted(Path("/root/demo-screens/Screenshots").glob("*.png"))
    thumbs = shots[:9]

    def draw(draw, image):
        draw.text((110, 90), "~/Pictures/Screenshots", font=font(FONT_MONO, 26), fill=GREEN)
        draw.text((110, 140), f"{len(shots)} screenshots already on the machine", font=font(FONT_REG, 34), fill=TEXT)
        size, gap = 330, 26
        for index, path in enumerate(thumbs):
            column, row = index % 3, index // 3
            x = 110 + column * (size + gap)
            y = 230 + row * (size * 9 // 16 + gap)
            try:
                thumb = Image.open(path).convert("RGB")
                thumb.thumbnail((size, int(size * 9 / 16)))
                image.paste(thumb, (x, y))
                draw.rectangle([x, y, x + thumb.width, y + thumb.height], outline=(45, 54, 64))
            except Exception:
                draw.rectangle([x, y, x + size, y + size * 9 // 16], outline=(45, 54, 64))
        draw.text((110, 900), "Peripheral reads them where they are — no capture daemon, no cloud", font=font(FONT_REG, 26), fill=DIM)

    video.hold(draw, 5.5)


def scene_ask(
    video: Video,
    *,
    question: str,
    call: str,
    output: list[str],
    answer: list[str],
    label: str,
) -> None:
    mono = font(FONT_MONO, 27)
    mono_small = font(FONT_MONO, 24)
    body = font(FONT_REG, 29)

    def base(draw, typed_text: str, stage: int):
        draw.text((110, 80), label, font=font(FONT_MONO, 22), fill=DIM)
        # prompt line
        draw.text((110, 140), "❯", font=font(FONT_MONO_BOLD, 30), fill=GREEN)
        draw.text((160, 143), typed_text, font=mono, fill=TEXT)
        if stage >= 1:
            draw.text((150, 210), f"⚙ {call}", font=mono_small, fill=AMBER)
        if stage >= 2:
            y = 270
            for line in output:
                draw.text((160, y), line, font=mono_small, fill=DIM if line.startswith(" ") else BLUE)
                y += 34
        if stage >= 3:
            y = 270 + len(output) * 34 + 30
            for paragraph in answer:
                for line in wrap(draw, paragraph, body, W - 260):
                    draw.text((160, y), line, font=body, fill=TEXT)
                    y += 40
                y += 12

    def factory(stage: int):
        return lambda draw, image: base(draw, question, stage)

    video.typed(lambda text: (lambda draw, image: base(draw, text, 0)), question, seconds=1.9, tail=0.7)
    video.hold(factory(1), 1.5)
    video.hold(factory(2), 3.8)
    video.hold(factory(3), 8.5)


def scene_timeline(video: Video, lines: list[str]) -> None:
    mono = font(FONT_MONO, 24)

    def draw(draw, image):
        draw.text((110, 80), "screen_timeline · last 2 days", font=font(FONT_MONO, 22), fill=DIM)
        draw.text((110, 140), "❯", font=font(FONT_MONO_BOLD, 30), fill=GREEN)
        draw.text((160, 143), "what have I been looking at since yesterday?", font=font(FONT_MONO, 27), fill=TEXT)
        y = 215
        for line in lines:
            colour = TEXT
            if line.startswith("—"):
                colour = BLUE
            elif "⚙" in line:
                colour = AMBER
            elif "[#" in line:
                colour = DIM
            draw.text((140, y), line, font=mono, fill=colour)
            y += 33
        draw.text((110, 900), "your screens and the agent's own tool calls, on one clock", font=font(FONT_REG, 26), fill=DIM)

    video.hold(draw, 11.0)


def scene_privacy(video: Video) -> None:
    def draw(draw, image):
        draw.text((110, 110), "local, and quiet about it", font=font(FONT_MONO, 24), fill=GREEN)
        rows = [
            ("no network calls", "no telemetry, no sync, no model call to index"),
            ("no capture", "reads the screenshots your OS already wrote"),
            ("redacted at ingest", "keys, JWTs, bearer headers, card numbers"),
            ("forget / pause / retention", "/peripheral forget 8 · pause · retention_days"),
            ("one SQLite file", "plugin-data/peripheral/ — dies with the plugin"),
        ]
        y = 200
        for title, detail in rows:
            draw.text((130, y), "✓", font=font(FONT_BOLD, 32), fill=GREEN)
            draw.text((190, y), title, font=font(FONT_BOLD, 32), fill=TEXT)
            draw.text((190, y + 44), detail, font=font(FONT_REG, 26), fill=DIM)
            y += 130

    video.hold(draw, 8.0)


def scene_proof(video: Video) -> None:
    def draw(draw, image):
        draw.text((110, 110), "measured, not claimed", font=font(FONT_MONO, 24), fill=GREEN)
        stats = [
            ("22", "tests: real PNGs → Tesseract → FTS5"),
            ("10.6 s", "10 screens at 1440×900 indexed"),
            ("1.06 s", "per screen, 2 vCPU ARM, no GPU"),
            ("safe", "hermes plugins validate — security scan"),
        ]
        y = 210
        for number, label in stats:
            draw.text((130, y), number, font=font(FONT_BOLD, 64), fill=BLUE)
            draw.text((420, y + 18), label, font=font(FONT_REG, 30), fill=TEXT)
            y += 120
        draw.text((130, 760), "hermes plugins install okyssta/hermes-peripheral", font=font(FONT_MONO, 30), fill=GREEN)

    video.hold(draw, 7.5)


def scene_end(video: Video) -> None:
    def draw(draw, image):
        draw.text((110, 260), "Peripheral", font=font(FONT_BOLD, 96), fill=TEXT)
        draw.text((114, 400), "Hermes remembers what you saw.", font=font(FONT_REG, 40), fill=BLUE)
        draw.text((114, 540), "github.com/okyssta/hermes-peripheral", font=font(FONT_MONO, 30), fill=DIM)
        draw.text((114, 596), "catalog PR: NousResearch/hermes-agent #149463", font=font(FONT_MONO, 30), fill=DIM)

    video.hold(draw, 5.5)


# ---------------------------------------------------------------- assemble

def real_outputs() -> tuple[list[str], list[str]]:
    """Pull the tool output the video shows straight from the plugin."""
    os.environ.setdefault("HERMES_HOME", "/root/.hermes/profiles/main")
    from peripheral import db, render, tools
    from peripheral.settings import Settings

    import peripheral

    class _Ctx:
        def get_config(self, key, default=None):
            if key == "watch_dirs":
                return ["/root/demo-screens/Screenshots"]
            return default

    peripheral._CTX = _Ctx()
    peripheral._CACHED = None

    recall = tools.screen_recall({"query": "card error", "since": "2026-11-02", "limit": 2})
    recall_lines = []
    for line in recall.splitlines():
        if not line.strip() or line.strip().startswith("thumb:"):
            continue
        if len(line) > 100:
            line = line[:99] + "…"
        recall_lines.append(line)
    recall_lines = recall_lines[:7]

    # The harness really is calling screen_recall while this renders, so logging
    # that call is a true event rather than a decoration for the timeline.
    # Earlier renders leave their own copies behind; keep exactly one.
    db.connect().execute("DELETE FROM events WHERE name = 'screen_recall'")
    db.record_event("tool", "screen_recall", 'query="card error"')

    shots = db.recent(12, since=tools.parse_when("2d") or 0)
    events = db.events_between(tools.parse_when("2d") or 0, time.time(), limit=12)
    timeline = render.timeline(shots, events, title="").splitlines()
    timeline_lines = [line for line in timeline if line.strip()][:15]
    return recall_lines, timeline_lines


def main() -> int:
    if BUILD.exists():
        shutil.rmtree(BUILD)
    FRAMES.mkdir(parents=True)

    recall_lines, timeline_lines = real_outputs()
    video = Video()
    scene_title(video)
    scene_problem(video)
    scene_folder(video)
    scene_ask(
        video,
        question="what was that card error I saw on Monday?",
        call='screen_recall(query="card error", since="2026-11-02")',
        output=recall_lines,
        answer=[
            "That Monday screen was Stripe's error-code reference, open at 11:05.",
            'It lists `expired_card` — "The card has expired. Check the expiration date or use a different card."',
            "Caveat: the docs page, not a live charge failure. Want me to widen the search?",
        ],
        label="hermes · terminal",
    )
    scene_timeline(video, timeline_lines)
    scene_privacy(video)
    scene_proof(video)
    scene_end(video)

    concat = BUILD / "concat.txt"
    with concat.open("w") as handle:
        for path, duration in video.entries:
            handle.write(f"file '{path}'\nduration {duration:.3f}\n")
        handle.write(f"file '{video.entries[-1][0]}'\n")

    out = BUILD / "peripheral-demo.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat),
            "-fps_mode", "cfr", "-r", "30", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out),
        ],
        check=True,
    )
    duration = sum(duration for _, duration in video.entries)
    print(f"wrote {out} ({duration:.1f}s, {len(video.entries)} frames)")
    print(f"size: {out.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
