"""Tool handlers.

Contract, applied literally: ``def handler(args: dict, **kwargs) -> str``, never
raises, always returns text the model can act on. Each handler is cheap when the
index is warm; the first one in a quiet period triggers a small incremental scan
so a screenshot taken ten seconds ago is already findable.
"""

from __future__ import annotations

import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import db, ingest, render
from .settings import Settings

_LAZY_LOCK = threading.Lock()
_LAST_LAZY = 0.0
_LAZY_GAP_SECONDS = 20.0


def get_settings() -> Settings:
    from . import current_settings

    return current_settings()


def lazy_scan(settings: Settings, *, batch: int = 12) -> None:
    """Index a few new files before answering, at most once per gap."""
    global _LAST_LAZY
    if not settings.enabled:
        return
    now = time.time()
    with _LAZY_LOCK:
        if now - _LAST_LAZY < _LAZY_GAP_SECONDS:
            return
        _LAST_LAZY = now
    try:
        ingest.scan(settings, batch=batch)
    except Exception:
        pass


_RELATIVE = re.compile(r"^(?P<n>\d+)\s*(?P<unit>m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|week|weeks)$", re.I)


def parse_when(text: str | None, *, end: bool = False) -> float | None:
    """Turn the loose time expressions a model or a person writes into an epoch.

    Accepts '', 'now', 'today', 'yesterday', 'this morning', '2h', '3 days',
    'last week', '2026-11-04', '2026-11-04 14:30'. Unparseable input returns None
    (the caller then simply does not filter) rather than guessing a wrong window.
    """
    if not text:
        return None
    value = str(text).strip().lower()
    if not value or value in {"any", "all", "anytime", "ever"}:
        return None
    now = time.time()
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if value in {"now", "just now"}:
        return now
    if value == "today":
        return today.timestamp() if not end else now
    if value == "yesterday":
        start = today - timedelta(days=1)
        return start.timestamp() if not end else today.timestamp() - 1
    if value in {"this morning"}:
        return (today + timedelta(hours=6)).timestamp()
    if value in {"this afternoon"}:
        return (today + timedelta(hours=12)).timestamp()
    if value in {"this evening", "tonight"}:
        return (today + timedelta(hours=18)).timestamp()
    if value == "last week":
        return (today - timedelta(days=7)).timestamp()
    if value == "last month":
        return (today - timedelta(days=30)).timestamp()
    relative = _RELATIVE.match(value.replace("ago", "").strip())
    if relative:
        count = int(relative.group("n"))
        unit = relative.group("unit").lower()
        if unit.startswith("m"):
            delta = timedelta(minutes=count)
        elif unit.startswith("h"):
            delta = timedelta(hours=count)
        elif unit.startswith("d"):
            delta = timedelta(days=count)
        else:
            delta = timedelta(weeks=count)
        return (now - delta.total_seconds()) if not end else now
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        if fmt in {"%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"}:
            start = parsed.replace(hour=0, minute=0, second=0)
            return start.timestamp() if not end else (start + timedelta(days=1)).timestamp() - 1
        return parsed.timestamp()
    return None


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def screen_recall(args: dict | None = None, **kwargs: Any) -> str:
    data = args or {}
    query = str(data.get("query") or "").strip()
    if not query:
        return "Error: screen_recall needs a query — the words that were on the screen."
    settings = get_settings()
    lazy_scan(settings)
    since = parse_when(data.get("since"))
    until = parse_when(data.get("until"), end=True)
    limit = _int(data.get("limit"), 8, 1, 25)
    try:
        shots = db.search(query, since=since, until=until, limit=limit)
    except Exception as exc:  # pragma: no cover - defensive: a tool must answer
        return f"Error: screen search failed ({exc}). Try /peripheral scan, then retry."
    total = db.stats()["count"]
    if not shots and settings.ocr_languages and not db.stats()["chars"]:
        return render.results(query, shots) + (
            "\n\nThe index holds no text yet: nothing has been OCR'd. Check /peripheral "
            "for the OCR engine status."
        )
    return render.results(query, shots, total=total)


def screen_timeline(args: dict | None = None, **kwargs: Any) -> str:
    data = args or {}
    settings = get_settings()
    lazy_scan(settings)
    since = parse_when(data.get("since")) or parse_when("today")
    until = parse_when(data.get("until"), end=True) or time.time()
    limit = _int(data.get("limit"), 40, 1, 120)
    shots = db.recent(limit, since=since, until=until)
    events = db.events_between(since, until, limit=limit) if settings.record_agent_events else []
    label = "Screen timeline"
    if data.get("since"):
        label = f"Screen timeline · since {data.get('since')}"
    return render.timeline(shots, events, title=label)


def screen_show(args: dict | None = None, **kwargs: Any) -> str:
    data = args or {}
    try:
        shot_id = int(data.get("id"))
    except (TypeError, ValueError):
        return "Error: screen_show needs the numeric id shown as [#id]."
    shot = db.get(shot_id)
    if shot is None:
        return f"No screen with id {shot_id}. Use screen_recall or screen_timeline to find one."
    chars = _int(data.get("chars"), 6000, 200, 40000)
    return render.record(shot, body_chars=chars)


def screen_index(args: dict | None = None, **kwargs: Any) -> str:
    data = args or {}
    settings = get_settings()
    batch = _int(data.get("batch"), 40, 1, 500)
    paths = data.get("paths") or []
    if isinstance(paths, str):
        paths = [part for part in paths.splitlines() if part.strip()]
    try:
        if paths:
            expanded: list[str] = []
            for entry in paths:
                candidate = os.path.expanduser(str(entry))
                if os.path.isdir(candidate):
                    expanded.extend(ingest.candidates([Path(candidate)], limit=batch))
                else:
                    expanded.append(candidate)
            report = ingest.ingest_paths(expanded, settings)
            return render.scan_result(report, prefix="indexed paths")
        report = ingest.scan(settings, batch=batch)
        return render.scan_result(report, prefix="indexed")
    except Exception as exc:  # pragma: no cover
        return f"Error: indexing failed ({exc})."
