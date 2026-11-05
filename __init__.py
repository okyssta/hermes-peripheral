"""peripheral — Hermes gets a memory of what you've seen.

Hermes can read a file you give it, but it has no idea what passed across your
screen five minutes ago: the error you meant to ask about, the price you saw, the
message from a colleague, the page whose title you half-remember. This plugin
indexes your screenshots where they already are — no capture daemon, no cloud, no
account — reads the text on them with Tesseract, and gives the agent four tools to
search that history and quote it back.

What it adds:

* **``screen_recall`` / ``screen_timeline`` / ``screen_show`` / ``screen_index``** —
  search by the words that were on screen, reconstruct a period, open one screen,
  or index explicitly.
* **``/peripheral``** — status, scan, search, forget, pause.
* **A bundled skill** — teaches the agent when screen memory beats asking the user.
* **A timeline that interleaves you and the agent** — screenshots and the agent's
  own tool calls share one clock, so "what was on screen when Hermes changed that
  config?" has an answer.

Everything is local. The plugin reads image files and writes one SQLite database
plus thumbnails under the profile's plugin-data directory; it makes no network
calls, reads no credentials, and never modifies or deletes the user's images.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

# A checkout directory is often not a valid Python package name (`hermes-peripheral`),
# and pytest imports this file as a bare module when the plugin root is not itself
# importable. Re-home the module under its real name so the relative imports below
# resolve identically in the plugin host, in `pytest`, and in an ad-hoc script.
if __package__ in (None, ""):
    _HERE = os.path.dirname(os.path.abspath(__file__))
    _PARENT = os.path.dirname(_HERE)
    if _PARENT not in sys.path:
        sys.path.append(_PARENT)
    __path__ = [_HERE]  # type: ignore[assignment]
    __package__ = "peripheral"
    sys.modules.setdefault("peripheral", sys.modules[__name__])

from . import capture, commands, db, ingest, ocr, render, schemas, tools
from .settings import PLUGIN_ID, SCHEMA_KEYS, Settings

logger = logging.getLogger(__name__)

_CTX: Any = None
_SCANNER: ingest.AutoScanner | None = None
_WATCHER: ingest.ScreenWatcher | None = None
_CACHED: Settings | None = None
_CACHED_AT = 0.0
_CACHE_SECONDS = 2.0


def current_settings() -> Settings:
    """Resolved settings, memoised for a couple of seconds.

    ``ctx.get_config`` is cheap but a tool loop can call it dozens of times in a
    turn; the memo keeps the hot path free while still noticing a settings change
    within the same conversation.
    """
    global _CACHED, _CACHED_AT
    import time as _time

    now = _time.monotonic()
    if _CACHED is not None and now - _CACHED_AT < _CACHE_SECONDS:
        return _CACHED
    if _CTX is None:
        _CACHED = Settings()
    else:
        _CACHED = Settings.from_ctx(_CTX, _hermes_home())
    _CACHED_AT = now
    return _CACHED


def _hermes_home() -> Path | None:
    try:
        from hermes_constants import get_hermes_home

        return get_hermes_home()
    except Exception:  # pragma: no cover - older hosts
        return None


# ------------------------------------------------------------------ hooks

_DETAIL_KEYS = ("command", "path", "file_path", "url", "query", "pattern", "prompt", "name")


def _detail_from_args(args: Any) -> str:
    if not isinstance(args, dict):
        return ""
    for key in _DETAIL_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:160]
    for value in args.values():
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:160]
    return ""


def _on_post_tool_call(tool_name: str = "", args: Any = None, **kwargs: Any) -> None:
    """Record what the agent did, so the timeline can show it beside your screens."""
    try:
        settings = current_settings()
        if not settings.enabled or not settings.record_agent_events:
            return
        name = str(tool_name or "").strip()
        if not name or name.startswith("screen_"):
            return
        # Redact before truncating: a half-cut `--password=` value would slip
        # past the patterns if the cut happened first.
        detail = ocr.redact(_detail_from_args(args))[:160]
        db.record_event("tool", name, detail, str(kwargs.get("session_id") or ""))
    except Exception:  # pragma: no cover - an observer must never disturb a turn
        logger.debug("peripheral: could not record tool event", exc_info=True)


# ------------------------------------------------------------------ register

def register(ctx: Any) -> None:
    global _CTX, _SCANNER

    _CTX = ctx
    settings = current_settings()

    handlers = {
        "screen_recall": tools.screen_recall,
        "screen_timeline": tools.screen_timeline,
        "screen_show": tools.screen_show,
        "screen_index": tools.screen_index,
        "screen_capture": tools.screen_capture,
        "screen_forget": tools.screen_forget,
    }
    for schema in schemas.ALL:
        ctx.register_tool(
            name=schema["name"],
            toolset="peripheral",
            schema=schema,
            handler=handlers[schema["name"]],
        )

    ctx.register_command("peripheral", commands.handle, description="Memory of what you've seen on screen")

    ctx.register_hook("post_tool_call", _on_post_tool_call)

    skill_dir = Path(__file__).parent / "skills" / "peripheral"
    if (skill_dir / "SKILL.md").is_file():
        try:
            ctx.register_skill("peripheral", str(skill_dir))
        except Exception:  # pragma: no cover - optional surface
            logger.debug("peripheral: could not register bundled skill", exc_info=True)

    if settings.enabled and settings.auto_scan_minutes:
        _SCANNER = ingest.AutoScanner(current_settings)
        _SCANNER.start(settings.auto_scan_minutes)

    if settings.enabled and settings.capture_enabled:
        _WATCHER = ingest.ScreenWatcher(current_settings)
        _WATCHER.start(
            settings.capture_interval_seconds,
            settings.capture_min_gap_seconds,
            settings.capture_max_per_hour,
        )

    logger.info(
        "peripheral ready: %s folders, ocr=%s, capture=%s",
        len(settings.watch_dirs),
        "yes" if ocr.available() else "no",
        capture.describe(),
    )


__all__ = ["register", "current_settings", "PLUGIN_ID", "SCHEMA_KEYS", "db", "ingest", "render"]
