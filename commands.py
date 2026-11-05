"""/peripheral — status, scan, search, forget.

A command is the surface a person reaches for, so it answers in sentences and
never in JSON. It runs in the gateway process (see the plugin guide): it returns
text instead of printing, and the only state it changes is this plugin's own
settings and index.
"""

from __future__ import annotations

import time
from typing import Any

from . import db, ingest, render
from .settings import Settings


def _settings() -> Settings:
    from . import current_settings

    return current_settings()


def _scan_report(report: ingest.ScanReport) -> str:
    return "\n".join(
        [
            render.scan_result(report, prefix="scan"),
            "",
            render.status_line(),
        ]
    )


def handle(raw_args: str = "", **kwargs: Any) -> str:
    text = (raw_args or "").strip()
    lowered = text.lower()
    settings = _settings()

    if lowered in {"", "status", "stats"}:
        notes = [
            render.status_line(),
            "",
            settings.describe(),
        ]
        if not settings.watch_dirs:
            notes.append("")
            notes.append("No folders to watch. Set watch_dirs to the folder your screenshots land in.")
        return "\n".join(notes)

    if lowered in {"config", "settings"}:
        return settings.describe()

    if lowered in {"scan", "index", "sync"}:
        return _scan_report(ingest.scan(settings, batch=120, force=False))

    if lowered in {"rescan", "reindex"}:
        return _scan_report(ingest.scan(settings, batch=120, force=True))

    if lowered.startswith("pause"):
        _set("enabled", False)
        return "peripheral paused: nothing new is indexed and the tools answer from the existing index."

    if lowered.startswith("resume"):
        _set("enabled", True)
        return "peripheral resumed."

    if lowered.startswith("forget"):
        target = text[6:].strip().lower()
        if target == "all":
            removed = db.forget(None)
            return (
                f"forgotten: {removed} entries and their thumbnails removed from the index. "
                "The image files themselves are untouched — delete those in the folder if "
                "you want them gone."
            )
        try:
            shot_id = int(target)
        except ValueError:
            return "Usage: /peripheral forget <id>   ·   /peripheral forget all"
        removed = db.forget(shot_id)
        if not removed:
            return f"No screen with id {shot_id}."
        return (
            f"forgotten: screen #{shot_id} and its thumbnail are out of the index "
            "(the original image file is untouched)."
        )

    if lowered.startswith("timeline"):
        rest = text[8:].strip()
        since = rest or "today"
        from .tools import parse_when

        start = parse_when(since) or parse_when("today")
        shots = db.recent(60, since=start)
        events = db.events_between(start, time.time(), limit=60) if settings.record_agent_events else []
        return render.timeline(shots, events, title=f"Screen timeline · {since}")

    if lowered.startswith("open"):
        from . import tools
        from . import render as _render

        try:
            shot_id = int(text[4:].strip())
        except ValueError:
            return "Usage: /peripheral open <id>"
        shot = db.get(shot_id)
        return _render.record(shot) if shot else f"No screen with id {shot_id}."

    if lowered in {"help", "?"}:
        return _HELP

    # Anything else is a search.
    query = text
    since = until = None
    for separator in (" since ", " after ", " before "):
        if separator in lowered:
            head, _, tail = text.partition(separator)
            query = head.strip()
            from .tools import parse_when

            bound = parse_when(tail.strip())
            # "since/after X" is a lower bound, "before X" an upper one: feeding
            # both into `since` made "before" search the complement of what was
            # asked for.
            if separator == " before ":
                until = bound
            else:
                since = bound
            break
    shots = db.search(query, since=since, until=until, limit=8)
    if not shots:
        return render.results(query, shots) + f"\n\n{render.status_line()}"
    return render.results(query, shots, total=db.stats()["count"])


def _set(key: str, value: Any) -> None:
    from . import _CTX

    setter = getattr(_CTX, "set_config", None)
    if callable(setter):
        setter(key, value)


_HELP = (
    "/peripheral              status: what is indexed, where it looks, how OCR is set up\n"
    "/peripheral scan         index new screenshots now\n"
    "/peripheral rescan       re-read every file (after changing OCR languages)\n"
    "/peripheral <words>      search the screen history\n"
    "/peripheral <words> since yesterday\n"
    "/peripheral timeline [since]\n"
    "/peripheral open <id>    one screen's text\n"
    "/peripheral forget <id>|all\n"
    "/peripheral pause|resume"
)
