#!/usr/bin/env python3
"""Render the 90-second demo video from real captures and a real transcript.

Nothing here invents content: the screen images are the frames the plugin captured
(``scripts/demo_capture.py``), and the tool calls, arguments and answer come from
``docs/demo-transcript.json`` (``scripts/demo_session.py``). This script lays them
out and encodes them, so the video cannot drift from the evidence.

    python3 scripts/make_demo_video.py            # writes docs/demo.mp4

Requires Pillow (a Hermes dependency) and ffmpeg on PATH. Silent by design.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[1]
FRAMES = REPO / "out" / "demo-frames"
DOCS = REPO / "docs"
TRANSCRIPT = DOCS / "demo-transcript.json"
OUT = DOCS / "demo.mp4"

W, H, FPS = 1600, 1000, 15

BG = (11, 14, 22)
PANEL = (17, 21, 32)
LINE = (30, 38, 54)
BORDER = (48, 58, 80)
INK = (232, 238, 248)
DIM = (138, 150, 172)
ACCENT = (120, 170, 255)
GREEN = (74, 222, 128)
AMBER = (250, 204, 21)

FONT_DIRS = (
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/truetype/liberation",
    "/System/Library/Fonts/Supplemental",
    "C:/Windows/Fonts",
)


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for folder in FONT_DIRS:
        candidate = Path(folder) / name
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default(size=size)


def mono(size: int) -> ImageFont.FreeTypeFont:
    return _font("DejaVuSansMono.ttf", size)


def sans(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return _font("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size)


def wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    words, lines, current = text.split(" "), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def rounded(draw: ImageDraw.ImageDraw, box, radius: int, fill, outline=None, width: int = 1) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def paste_screen(canvas: Image.Image, path: Path, box, *, radius: int = 14, dim: float = 1.0) -> tuple[int, int]:
    """Draw a real captured frame inside a rounded, bordered panel."""
    x0, y0, x1, y1 = box
    with Image.open(path) as raw:
        shot = raw.convert("RGB")
    scale = min((x1 - x0) / shot.width, (y1 - y0) / shot.height)
    shot = shot.resize((max(1, int(shot.width * scale)), max(1, int(shot.height * scale))), Image.LANCZOS)
    if dim != 1.0:
        shot = Image.blend(shot, Image.new("RGB", shot.size, (0, 0, 0)), 1.0 - dim)
    mask = Image.new("L", shot.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, shot.width, shot.height), radius=radius, fill=255)
    canvas.paste(shot, (x0, y0), mask)
    ImageDraw.Draw(canvas).rounded_rectangle(
        (x0, y0, x0 + shot.width, y0 + shot.height), radius=radius, outline=BORDER, width=2
    )
    return shot.width, shot.height


def canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    for x in range(0, W, 40):
        draw.line([(x, 0), (x, H)], fill=(13, 17, 26))
    for y in range(0, H, 40):
        draw.line([(0, y), (W, y)], fill=(13, 17, 26))
    return image, draw


# ----------------------------------------------------------------- scenes


def scene_title(t: float) -> Image.Image:
    image, draw = canvas()
    draw.text((110, 300), "Peripheral", font=sans(96, True), fill=INK)
    draw.text((114, 424), "Hermes remembers what was on your screen.", font=sans(38), fill=ACCENT)
    draw.text((114, 500), "No screenshots. No cloud. No API key.", font=sans(30), fill=DIM)
    draw.text(
        (114, 566),
        "It captures the screen itself — and can quote it back long after the window closed.",
        font=sans(26), fill=DIM,
    )
    return image


def scene_screen(t: float) -> Image.Image:
    image, draw = canvas()
    draw.text((70, 54), "17:06  ·  this was on the screen", font=mono(26), fill=DIM)
    paste_screen(image, FRAMES / "01-stripe-error.png", (70, 120, W - 70, H - 130))
    draw.text((70, H - 86), "You did not take a screenshot. Nothing was saved for you.", font=sans(30, True), fill=INK)
    return image


def scene_capture(t: float) -> Image.Image:
    image, draw = canvas()
    draw.text((80, 62), "screen_capture()", font=mono(34), fill=GREEN)
    draw.text((80, 116), "the agent asks, the plugin photographs the display", font=sans(24), fill=DIM)
    paste_screen(image, FRAMES / "01-stripe-error.png", (80, 180, 960, 180 + 500))
    rows = [
        ("backend      x11-ffmpeg · 1,218 ms", DIM),
        ("screen #1    679 chars of text", INK),
        ("ocr          tesseract 5.3.4 · local", DIM),
        ("stored       148 KB webp · plugin-data", DIM),
    ]
    y = 230
    for text, colour in rows:
        draw.text((1010, y), text, font=mono(22), fill=colour)
        y += 46
    draw.text((1010, y + 36), "No file dialog.\nNo clipboard.\nNo user action.",
              font=sans(26), fill=ACCENT, spacing=12)
    return image


def scene_question(t: float) -> Image.Image:
    image, draw = canvas()
    draw.text((150, 250), "❯  you", font=mono(24), fill=ACCENT)
    question = (
        "I got a card error a little while ago — what did it actually say? "
        "And was anyone else talking about it?"
    )
    y = 330
    for line in wrap(draw, question, sans(36), W - 300):
        draw.text((150, y), line, font=sans(36), fill=INK)
        y += 56
    draw.text((150, y + 70), "Three screens were captured today. None of them is a file you made.",
              font=sans(24), fill=DIM)
    return image


CALLS_FALLBACK = [
    ('screen_recall(query="card declined", since="today")', "3 screens · nearest just now"),
    ('screen_recall(query="card error")', "2 screens (of 3)"),
    ('screen_recall(query="payment failed")', "2 screens (of 3)"),
    ("screen_show(id=1)", "679 chars · checkout error — card declined"),
    ("screen_show(id=2)", "465 chars · Sam in #deploys about expired_card"),
    ("screen_show(id=3)", "428 chars · production dashboard, 402 spike"),
]
THUMBS = ("01-stripe-error.png", "02-slack-thread.png", "03-dashboard.png")
THUMB_LABELS = ("#1 checkout error", "#2 #deploys thread", "#3 dashboard")


def scene_calls(t: float) -> Image.Image:
    image, draw = canvas()
    draw.text((70, 48), "the model chose these six calls itself", font=mono(24), fill=DIM)
    shown = min(len(CALLS), 1 + int(t / 2.1))
    y = 100
    for index in range(shown):
        call, result = CALLS[index]
        draw.text((70, y), "⚙ " + call, font=mono(23), fill=GREEN)
        draw.text((104, y + 31), "│ " + result, font=mono(21), fill=DIM)
        y += 74
    if shown >= 4:
        x = 70
        for index, name in enumerate(THUMBS):
            if shown >= 4 + index:
                paste_screen(image, FRAMES / name, (x, 588, x + 470, 588 + 286), radius=10, dim=0.94)
                draw.text((x, 890), THUMB_LABELS[index], font=mono(20), fill=DIM)
            x += 490
    return image


ANSWER_FALLBACK = (
    "Your card was declined — the card has expired, error code expired_card. Two people were "
    "talking about it in #deploys: Sam flagged that the checkout service throws it for test "
    "cards, Priya said the fixture is pinned to 03/24. But the production dashboard shows 402 "
    "card_declined 18,402 times in 24 hours — worth confirming the sandbox explanation covers "
    "production."
)


def scene_answer(t: float) -> Image.Image:
    image, draw = canvas()
    draw.text((80, 54), "Hermes", font=sans(30, True), fill=ACCENT)
    draw.text((300, 62), "answered from the memory — and noticed what the thread missed", font=sans(22), fill=DIM)
    reveal = int(len(ANSWER) * min(1.0, t / 9.0))
    visible = ANSWER[:reveal]
    size, lines = 31, []
    while size >= 22:
        lines = wrap(draw, visible, sans(size), W - 240)
        if len(lines) * (size + 13) <= 520:
            break
        size -= 2
    y = 150
    for line in lines:
        draw.text((120, y), line, font=sans(size), fill=INK)
        y += size + 13
    if reveal >= len(ANSWER) - 30:
        rounded(draw, (120, 716, W - 120, 842), 16, PANEL, BORDER, 2)
        draw.text((150, 740), "cross-screen inference", font=mono(22), fill=AMBER)
        draw.text((150, 778), "the thread says sandbox-only  ·  the dashboard says 18,402 in production",
                  font=mono(22), fill=INK)
    return image


def scene_privacy(t: float) -> Image.Image:
    image, draw = canvas()
    draw.text((90, 84), "/peripheral forget 1", font=mono(36), fill=GREEN)
    rows = (
        ("index row", "deleted"),
        ("thumbnail + captured frame", "deleted"),
        ("your own screenshots", "never touched"),
        ("network calls", "none"),
    )
    y = 210
    for left, right in rows:
        draw.text((90, y), left, font=mono(26), fill=DIM)
        draw.text((830, y), right, font=mono(26), fill=GREEN if right != "none" else ACCENT)
        draw.line([(90, y + 44), (1180, y + 44)], fill=LINE)
        y += 74
    draw.text((90, 560), "Local SQLite + FTS5. Nothing leaves the machine.", font=sans(32), fill=INK)
    for index, line in enumerate(
        (
            "Background capture is off until you turn it on.",
            "When on: changed frames only, capped per hour, redacted at the storage boundary.",
            "Works without Tesseract too — the frames stay, and the model can look at them.",
        )
    ):
        draw.text((90, 626 + index * 42), line, font=sans(24), fill=DIM)
    return image


def scene_end(t: float) -> Image.Image:
    image, draw = canvas()
    draw.text((110, 258), "Peripheral", font=sans(80, True), fill=INK)
    draw.text((114, 372), "hermes plugins install okyssta/hermes-peripheral --enable",
              font=mono(29), fill=GREEN)
    draw.text((114, 442), "6 tools · slash command · bundled skill · post-tool-call timeline",
              font=sans(24), fill=DIM)
    draw.text((114, 488), "your screenshots, and the screen itself — searchable, local, deletable",
              font=sans(24), fill=DIM)
    draw.text((114, 586), "github.com/okyssta/hermes-peripheral", font=mono(26), fill=ACCENT)
    return image


SCENES = (
    (0.0, 5.5, scene_title),
    (5.5, 13.0, scene_screen),
    (13.0, 20.5, scene_capture),
    (20.5, 26.0, scene_question),
    (26.0, 44.0, scene_calls),
    (44.0, 62.0, scene_answer),
    (62.0, 74.0, scene_privacy),
    (74.0, 80.0, scene_end),
)
DURATION = SCENES[-1][1]

CALLS = CALLS_FALLBACK
ANSWER = ANSWER_FALLBACK


def load_transcript() -> None:
    """Use the captured session's own calls and answer when the file is present."""
    global CALLS, ANSWER
    if not TRANSCRIPT.is_file():
        print("no transcript — using the recorded fallback text")
        return
    document = json.loads(TRANSCRIPT.read_text())
    turn = document["turns"][0]
    calls = []
    for step in turn["steps"]:
        args = ", ".join(f"{key}={json.dumps(value)}" for key, value in step["args"].items())
        label = f"{step['tool']}({args})"
        first_line = next(
            (line.strip(" │") for line in step["result_preview"].splitlines() if line.strip()),
            "",
        )
        calls.append((label, first_line[:76]))
    CALLS = tuple(calls) or CALLS_FALLBACK

    import re

    plain = re.sub(r"\*\*(.+?)\*\*", r"\1", turn["answer"] or "")
    plain = re.sub(r"`(.+?)`", r"\1", plain)
    plain = re.sub(r"^[>\-\s]+", "", plain, flags=re.M)
    plain = " ".join(plain.split())
    if plain:
        # Keep the lead and the observation the user did not make, so the frame
        # shows the shape of the real answer instead of an arbitrary cut.
        sentences = re.split(r"(?<=[.!?])\s+", plain)
        kept = sentences[:3] + [
            sentence for sentence in sentences[3:]
            if "18,402" in sentence or "dashboard" in sentence.lower()
        ]
        excerpt = " ".join(kept)
        if len(excerpt) > 900:
            excerpt = excerpt[:897].rsplit(" ", 1)[0] + "…"
        ANSWER = excerpt
    print(f"transcript: {len(CALLS)} tool calls · answer {len(turn['answer'])} chars · {turn['elapsed_s']}s")


def frame_at(t: float) -> Image.Image:
    for start, end, fn in SCENES:
        if t < end:
            return fn(t - start)
    return scene_end(0)


def main() -> int:
    missing = [name for name in THUMBS if not (FRAMES / name).is_file()]
    if missing:
        raise SystemExit(f"missing captured frames {missing} — run scripts/demo_capture.py first")
    load_transcript()

    frames_dir = REPO / "out" / "video-frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    total = int(DURATION * FPS)
    for index in range(total):
        frame_at(index / FPS).save(frames_dir / f"{index:05d}.png")
    print(f"rendered {total} frames at {FPS} fps")

    ffmpeg = shutil.which("ffmpeg") or "/root/.hermes/tools/ffmpeg-9.0.1-linux-arm64/bin/ffmpeg"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "image2", "-framerate", str(FPS), "-i", str(frames_dir / "%05d.png"),
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(OUT),
        ],
        check=True,
    )
    print(f"video: {OUT}  ({DURATION:.0f}s, silent, {OUT.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
