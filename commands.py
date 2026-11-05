"""/peripheral — status, scan, search, forget.

A command is the surface a person reaches for, so it answers in sentences and
never in JSON. It runs in the gateway process (see the plugin guide): it returns
text instead of printing, and the only state it changes is this plugin's own
settings and index.
"""

from __future__ import annotations

import time
from typing import Any

from . import capture, db, ingest, render
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


def _parse_bounds(text: str, lowered: str, query: str) -> tuple[float | None, float | None, str, str]:
    """Split "words since X" / "words before Y" into a query and its bounds."""
    from .tools import parse_when

    for separator in (" since ", " after ", " before "):
        if separator in lowered:
            head, _, tail = text.partition(separator)
            query = head.strip()
            raw = tail.strip()
            bound = parse_when(raw)
            # "since/after X" is a lower bound, "before X" an upper one: feeding
            # both into `since` made "before" search the complement of what was
            # asked for.
            if separator == " before ":
                return None, bound, query, "" if bound is not None else raw
            return bound, None, query, "" if bound is not None else raw
    return None, None, query, ""


def _suggest(word: str) -> str:
    """A near-miss subcommand for a one-word line that matched nothing."""
    import difflib

    known = ["status", "config", "scan", "rescan", "capture", "watch", "pause", "resume",
             "forget", "timeline", "open", "help"]
    if len(word) < 3 or " " in word:
        return ""
    matches = difflib.get_close_matches(word.lower(), known, n=1, cutoff=0.72)
    return matches[0] if matches else ""


def handle(raw_args: str = "", **kwargs: Any) -> str:
    text = (raw_args or "").strip()
    lowered = text.lower()
    settings = _settings()

    if lowered in {"", "status", "stats"}:
        data = db.stats()
        notes = [
            render.status_line(),
            "",
            settings.describe(),
            capture.describe(),
        ]
        if not settings.watch_dirs:
            notes.append("")
            notes.append("No folders to watch. Set watch_dirs to the folder your screenshots land in.")
        if not data["count"]:
            folders = [str(path) for path in settings.watch_dirs]
            try:
                found = len(ingest.candidates(list(settings.watch_dirs), limit=200)) if folders else 0
            except Exception:  # pragma: no cover - a status line must never fail
                found = 0
            notes.append("")
            notes.append(
                render.first_run_hint(
                    image_count=found,
                    folders=folders,
                    capture_available=bool(capture.available_backends()),
                )
            )
        return "\n".join(notes)

    if lowered in {"config", "settings"}:
        return settings.describe() + "\n" + capture.describe()

    if lowered in {"scan", "index", "sync"}:
        return _scan_report(ingest.scan(settings, batch=120, force=False))

    if lowered in {"rescan", "reindex"}:
        return _scan_report(ingest.scan(settings, batch=120, force=True))

    if lowered.startswith("capture"):
        note = text.split(" ", 1)[1].strip() if " " in text else ""
        outcome = ingest.capture_and_ingest(settings, force=True)
        lines = [outcome.line()]
        if outcome.shot_id:
            shot = db.get(outcome.shot_id)
            if shot is not None:
                if note:
                    db.set_title(shot.id, note)
                lines.append("")
                lines.append(render.record(shot, body_chars=1200))
        elif not outcome.ok:
            lines.append(capture.describe())
        lines.append("")
        lines.append(capture.describe())
        return "\n".join(lines)

    if lowered.startswith("watch"):
        rest = lowered[5:].strip()
        if rest in {"on", "start"}:
            stored = _set("capture_enabled", True)
            from . import set_background_capture

            running = set_background_capture(True)
            line = (
                "background capture on: a frame is kept only when the screen changes, "
                f"at most every {settings.capture_interval_seconds}s and "
                f"{settings.capture_max_per_hour}/hour. /peripheral watch off stops it."
            )
            if not stored:
                line = "background capture could not be stored in this profile's config; nothing changed."
            elif not running:
                line += "\nThe capture thread did not start in this process; restart Hermes to pick it up."
            elif not capture.available_backends():
                line += f"\nBut {capture.describe()} — no frames can be taken until that changes."
            return line
        if rest in {"off", "stop"}:
            _set("capture_enabled", False)
            from . import set_background_capture

            set_background_capture(False)
            return "background capture off. Screens captured so far stay until you forget them."
        return (
            f"{capture.describe()}\n"
            f"background capture: {'on' if settings.capture_enabled else 'off'}\n"
            "/peripheral watch on|off"
        )

    if lowered.startswith("pause"):
        _set("enabled", False)
        from . import set_enabled

        set_enabled(False)
        return (
            "peripheral paused: the tools answer from the existing index and nothing new is "
            "indexed or captured. /peripheral resume turns it back on."
        )

    if lowered.startswith("resume"):
        _set("enabled", True)
        from . import set_enabled

        set_enabled(True)
        return "peripheral resumed."

    if lowered.startswith("forget"):
        target = text[6:].strip().lower()
        if target == "all":
            removed = db.forget(None)
            return (
                f"forgotten: {removed} entries removed from the index, with every thumbnail "
                "and captured frame the plugin wrote. Screenshots you took yourself are untouched "
                "— delete those in the folder if you want them gone."
            )
        try:
            shot_id = int(target)
        except ValueError:
            return "Usage: /peripheral forget <id>   ·   /peripheral forget all"
        removed = db.forget(shot_id)
        if not removed:
            return f"No screen with id {shot_id}."
        return (
            f"forgotten: screen #{shot_id} is out of the index, with the file the plugin wrote "
            "for it. A screenshot you took yourself is never deleted."
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
    since, until, query, ignored = _parse_bounds(text, lowered, text)
    shots = db.search(query, since=since, until=until, limit=8)
    note = f"\n\n(“{ignored}” was not understood, so that bound was ignored.)" if ignored else ""
    if not shots:
        suggestion = _suggest(text)
        if suggestion:
            return (
                f"No such command: “{text}”. Did you mean /peripheral {suggestion}?\n"
                "/peripheral help lists every command."
            )
        from .tools import partial_search

        partial, terms_by_id = partial_search(query, since=since, until=until, limit=8)
        if partial:
            return render.partial_results(query, partial, terms_by_id, total=db.stats()["count"]) + note
        return render.results(query, shots) + f"\n\n{render.status_line()}{note}"
    return render.results(query, shots, total=db.stats()["count"]) + note


def _set(key: str, value: Any) -> bool:
    """Write one setting through the host, then drop the memoised copy.

    Returns whether the host accepted the write: a command that reports a change it
    could not store is worse than one that says so.
    """
    from . import _CTX

    setter = getattr(_CTX, "set_config", None)
    if callable(setter):
        setter(key, value)
        from . import invalidate_settings

        invalidate_settings()
        return True
    return False


_HELP = (
    "/peripheral              status: what is indexed, where it looks, capture and OCR readiness\n"
    "/peripheral config       the settings in force (folders, languages, retention, capture)\n"
    "/peripheral capture [note]  remember what is on screen right now\n"
    "/peripheral watch on|off    background capture on change (off by default)\n"
    "/peripheral scan         index new screenshots now\n"
    "/peripheral rescan       re-read every file (after changing OCR languages)\n"
    "/peripheral <words>      search the screen history\n"
    "/peripheral <words> since yesterday      · after 3pm · before 2026-11-04\n"
    "/peripheral timeline [since]\n"
    "/peripheral open <id>    one screen's text and its path\n"
    "/peripheral forget <id>|all   drop it from the index, thumbnails and captured frames\n"
    "/peripheral pause|resume"
)
