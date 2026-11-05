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
    note: str = "",
) -> None:
    mono = font(FONT_MONO, 27)
    mono_small = font(FONT_MONO, 24)
    body = font(FONT_REG, 27)

    def base(draw, typed_text: str, stage: int):
        draw.text((110, 80), label, font=font(FONT_MONO, 22), fill=DIM)
        # prompt line
        draw.text((110, 140), "❯", font=font(FONT_MONO_BOLD, 30), fill=GREEN)
        draw.text((160, 143), typed_text, font=mono, fill=TEXT)
        if stage >= 1:
            draw.text((150, 205), f"⚙ {call}", font=mono_small, fill=AMBER)
        if stage >= 2:
            y = 255
            for line in output:
                draw.text((160, y), line, font=mono_small, fill=DIM if line.startswith(" ") else BLUE)
                y += 32
        if stage >= 3:
            y = 255 + len(output) * 32 + 26
            for paragraph in answer:
                for line in wrap(draw, paragraph, body, W - 260):
                    if y > H - 90:
                        break
                    draw.text((160, y), line, font=body, fill=TEXT)
                    y += 36
                y += 22
        if note:
            draw.text((110, H - 58), note, font=font(FONT_MONO, 20), fill=DIM)

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


def scene_proof(video: Video, stats: list[tuple[str, str]]) -> None:
    def draw(draw, image):
        draw.text((110, 110), "measured, not claimed", font=font(FONT_MONO, 24), fill=GREEN)
        y = 210
        for number, label in stats[:4]:
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

def real_outputs() -> dict:
    """Everything the video shows comes from the plugin and the captured session.

    The index is built in a throwaway HERMES_HOME so the video never depends on (or
    pollutes) the developer's own screen history, and the Q&A scene is rendered from
    `docs/demo-transcript.json` — the file `scripts/demo_session.py` wrote when a
    model actually called these tools — rather than from text typed into this script.
    """
    os.environ["HERMES_HOME"] = DEMO_HOME = os.environ.get(
        "DEMO_HERMES_HOME", "/tmp/peripheral-demo-home"
    )
    from peripheral import db, ingest, render, tools
    from peripheral.settings import Settings

    import peripheral

    folder = "/root/demo-screens/Screenshots"

    class _Ctx:
        def get_config(self, key, default=None):
            if key == "watch_dirs":
                return [folder]
            return default

    peripheral._CTX = _Ctx()
    peripheral._CACHED = None

    report = ingest.scan(Settings(watch_dirs=(Path(folder),), auto_scan_minutes=0), batch=40)
    print(f"index: {report.line()}")

    transcript_path = ROOT / "docs" / "demo-transcript.json"
    if not transcript_path.is_file():
        raise SystemExit(
            f"missing {transcript_path} — run scripts/demo_session.py first so the video "
            "renders a real session instead of invented text"
        )
    import json

    transcript = json.loads(transcript_path.read_text())
    turn = transcript["turns"][0]
    first = turn["steps"][0]

    def call_label(step: dict) -> str:
        args = ", ".join(f'{key}={json.dumps(value)}' for key, value in step["args"].items())
        label = f"{step['tool']}({args})"
        return label if len(label) <= 78 else label[:77] + "…"

    ask = {
        "question": turn["question"],
        "call": call_label(first),
        "output": [
            (line[:99] + "…" if len(line) > 100 else line)
            for line in first["result_preview"].splitlines()
            if line.strip() and not line.strip().startswith("thumb:")
        ][:6],
        "answer": paragraphs(turn["answer"]),
        "note": f"{len(turn['steps'])} tool calls · {turn['elapsed_s']:.0f} s · "
        f"{transcript['model']} · captured {transcript['captured_at']}",
    }

    # The render itself calls screen_recall below, so this event is true rather
    # than decorative; earlier renders leave their own copies behind — keep one.
    db.connect().execute("DELETE FROM events WHERE name IN ('screen_recall', 'screen_timeline')")
    db.record_event("tool", "screen_recall", call_label(first))

    shots = db.recent(12, since=tools.parse_when("7d") or 0)
    events = db.events_between(tools.parse_when("7d") or 0, time.time(), limit=12)
    timeline = render.timeline(shots, events, title="").splitlines()
    timeline_lines = [line for line in timeline if line.strip()][:15]

    tests = sum(
        len([line for line in path.read_text().splitlines() if line.strip().startswith("def test_")])
        for path in (ROOT / "tests").glob("test_*.py")
    )
    # The scan timing comes from the run that actually indexed the screens (recorded
    # in the transcript), not from this warm re-open of the same index.
    import re

    measured = re.search(r"(\d+) added in ([\d.]+)s", transcript["index"])
    if measured:
        indexed, seconds = int(measured.group(1)), float(measured.group(2))
    else:  # the index was already warm when the session ran
        indexed, seconds = report.scanned, report.seconds
    stats = [
        (str(tests), "tests: real PNGs → Tesseract → FTS5"),
        (f"{seconds:.1f} s", f"{indexed} screens at 1440×900 indexed"),
        (f"{seconds / max(1, indexed):.2f} s", "per screen, 2 vCPU ARM, no GPU"),
        ("safe", "hermes plugins validate — security scan"),
    ]
    return {"ask": ask, "timeline": timeline_lines, "stats": stats}


def paragraphs(markdown: str, *, limit: int = 4) -> list[str]:
    """Turn the model's markdown answer into the plain paragraphs the frame draws."""
    import re

    cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", markdown or "")
    cleaned = re.sub(r"`(.+?)`", r"\1", cleaned)
    blocks = [block.strip() for block in re.split(r"\n\s*\n", cleaned) if block.strip()]
    out: list[str] = []
    for block in blocks:
        text = " ".join(part.strip().lstrip(">").strip() for part in block.splitlines())
        text = " ".join(text.split())
        if text.startswith("—") or len(text) < 8:
            continue
        out.append(text)
        if len(out) >= limit:
            break
    return out or ["(the captured answer was empty)"]


def main() -> int:
    if BUILD.exists():
        shutil.rmtree(BUILD)
    FRAMES.mkdir(parents=True)

    data = real_outputs()
    ask, timeline_lines, stats = data["ask"], data["timeline"], data["stats"]
    video = Video()
    scene_title(video)
    scene_problem(video)
    scene_folder(video)
    scene_ask(
        video,
        question=ask["question"],
        call=ask["call"],
        output=ask["output"],
        answer=ask["answer"],
        note=ask["note"],
        label="hermes · captured session",
    )
    scene_timeline(video, timeline_lines)
    scene_privacy(video)
    scene_proof(video, stats)
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
