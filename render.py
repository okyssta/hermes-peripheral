"""Rendering index records as text for the chat, the CLI and the tool results.

The audience is two-sided: a person reading a terminal or a Telegram message, and
a model that pays for every character. So the default rendering is dense — one
header, one line per hit, one quoted line of evidence — and the full text body is
only returned when the agent explicitly asks for one screen.
"""

from __future__ import annotations

import time
from typing import Iterable, Sequence

from .db import Shot, human_age, stats

MAX_SNIPPET = 210
MAX_BODY = 6000


def _when(shot: Shot) -> str:
    today = time.strftime("%Y-%m-%d", time.localtime())
    yesterday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
    stamped = time.strftime("%Y-%m-%d", time.localtime(shot.captured_at))
    clock = time.strftime("%H:%M", time.localtime(shot.captured_at))
    if stamped == today:
        return f"today {clock}"
    if stamped == yesterday:
        return f"yesterday {clock}"
    return f"{time.strftime('%b %d', time.localtime(shot.captured_at))} {clock}"


def _clean_snippet(text: str) -> str:
    snippet = " ".join((text or "").split())
    if len(snippet) > MAX_SNIPPET:
        snippet = snippet[: MAX_SNIPPET - 1].rstrip() + "…"
    return snippet


def _label(shot: Shot) -> str:
    """A short label for a row: the OCR-derived title when it reads cleanly,
    otherwise the file name. Titles from OCR are a hint, never a claim, so junk
    is dropped rather than shown as if it meant something."""
    title = (shot.title or "").strip()
    junk = sum(1 for char in title if char in "#|<>{}[]\\^~©»«")
    words = title.split()
    if (
        title
        and 2 <= len(words) <= 9
        and junk <= 1
        and sum(1 for word in words if len(word) > 2) >= 2
    ):
        return title if len(title) <= 58 else title[:57] + "…"
    stem = shot.path.rsplit("/", 1)[-1]
    return stem if len(stem) <= 58 else stem[:57] + "…"


def results(query: str, shots: Sequence[Shot], *, total: int | None = None) -> str:
    if not shots:
        return (
            f"No screen matched “{query}”.\n"
            "Try a distinctive word from the page (an error code, a name, a price), "
            "or widen the window with since/until."
        )
    header = f"{len(shots)} screen{'s' if len(shots) != 1 else ''} for “{query}”"
    if total is not None and total > len(shots):
        header += f" (of {total})"
    header += f" · nearest {shots[0].ago}"
    lines = [header, ""]
    for index, shot in enumerate(shots, start=1):
        marker = ""
        if shot.source == "capture":
            marker = " · live"
        elif shot.text_chars == 0:
            marker = " · no text"
        lines.append(f"[{shot.id}] {_when(shot)} · {shot.text_chars:,} chars{marker} · {_label(shot)}")
        snippet = _clean_snippet(shot.snippet.replace("<<", "").replace(">>", "") or shot.title)
        if snippet:
            lines.append(f"     “{snippet}”")
        lines.append(f"     {shot.path}")
        if shot.thumb_path:
            lines.append(f"     thumb: {shot.thumb_path}")
        if index != len(shots):
            lines.append("")
    lines.append("")
    lines.append(
        "screen_show(id) opens one screen — full text, and the image itself when the model "
        "can see it. screen_capture() remembers whatever is on screen right now."
    )
    return "\n".join(lines)


def record(shot: Shot, *, body_chars: int = MAX_BODY) -> str:
    body = shot.ocr_text or ""
    truncated = len(body) > body_chars
    if truncated:
        body = body[:body_chars].rstrip() + "\n… (truncated; the file has the rest)"
    head = [
        f"screen #{shot.id} · {_when(shot)} ({shot.ago})",
        f"path: {shot.path}",
    ]
    if shot.source == "capture":
        head.append("origin: captured by Peripheral (the frame is owned by the plugin)")
    if shot.thumb_path:
        head.append(f"thumb: {shot.thumb_path}")
    head.append(f"read: {shot.text_chars:,} characters of text")
    if shot.title:
        head.append(f"title: {shot.title}")
    if not body.strip():
        body = (
            "(no text was readable in this image — the screen itself is attached when the "
            "model supports vision; otherwise open the path with a vision tool)"
        )
    return "\n".join(head) + "\n\n" + body


def timeline(shots: Sequence[Shot], events: Iterable[dict], *, title: str = "Screen timeline") -> str:
    entries: list[tuple[float, str]] = []
    for shot in shots:
        if shot.source == "capture":
            marker = " · live"
        elif shot.kind == "photo" or shot.text_chars == 0:
            marker = " · no text"
        else:
            marker = ""
        entries.append(
            (
                shot.captured_at,
                f"{time.strftime('%H:%M', time.localtime(shot.captured_at))}  "
                f"{_label(shot):<52.52}  [#{shot.id}]{marker}",
            )
        )
    for event in events:
        stamp = time.strftime("%H:%M", time.localtime(float(event["at"])))
        detail = (event.get("detail") or "").strip()
        detail = f" — {detail[:70]}" if detail else ""
        entries.append((float(event["at"]), f"{stamp}  ⚙ {event['name']}{detail}"))
    entries.sort(key=lambda item: item[0])
    if not entries:
        return f"{title}: nothing recorded in that window."
    lines = [f"{title} · {len(entries)} entries", ""]
    last_day = ""
    for stamp, text in entries:
        day = time.strftime("%a %b %d", time.localtime(stamp))
        if day != last_day:
            lines.append(f"— {day} —")
            last_day = day
        lines.append(f"  {text}")
    return "\n".join(lines)


def status_line() -> str:
    data = stats()
    if not data["count"]:
        return "peripheral: nothing indexed yet — /peripheral scan, or let the agent screen_capture"
    first = human_age(data["first"]) if data["first"] else "?"
    last = human_age(data["last"]) if data["last"] else "?"
    size = data["bytes"] / (1024 * 1024)
    captured = f" ({data['captured']:,} captured live)" if data.get("captured") else ""
    return (
        f"peripheral: {data['count']:,} screens indexed{captured} ({first} → {last}) · "
        f"{data['chars']:,} chars of text · {data['events']:,} agent events · "
        f"{size:.1f} MB of source images"
    )


def status(settings_text: str = "") -> str:
    lines = [status_line()]
    if settings_text:
        lines.append("")
        lines.append(settings_text)
    return "\n".join(lines)


def scan_result(report, *, prefix: str = "indexed") -> str:
    lines = [f"{prefix}: {report.line()}"]
    if not report.ocr_available:
        lines.append(
            "OCR is unavailable on this machine, so text search will stay empty. "
            "Install tesseract to index the words on screen (macOS: brew install tesseract; "
            "Debian/Ubuntu: apt install tesseract-ocr)."
        )
    elif report.ocr_hint:
        lines.append(f"ocr: {report.ocr_hint}")
    if report.folders and report.scanned == 0:
        lines.append("folders: " + ", ".join(report.folders))
    if report.added_ids:
        shown = ", ".join(str(shot_id) for shot_id in report.added_ids[:12])
        lines.append(f"new ids: {shown}")
    return "\n".join(lines)


def rewind_header(shots: Sequence[Shot]) -> str:
    if not shots:
        return "no screens in that window"
    return f"{len(shots)} screens, newest {shots[-1].ago}"
