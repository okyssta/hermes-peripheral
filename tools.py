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

from . import db, images, ingest, ocr, render
from .settings import Settings

_LAZY_LOCK = threading.Lock()
_LAST_LAZY = 0.0
_LAZY_GAP_SECONDS = 20.0


def get_settings() -> Settings:
    from . import current_settings

    return current_settings()


def _capability_flag(block: Any) -> bool | None:
    """``supports_vision``/``vision`` from a config block, or None when unset."""
    if not isinstance(block, dict):
        return None
    value = block.get("supports_vision", block.get("vision"))
    return value if isinstance(value, bool) else None


def _model_identity(config: dict[str, Any]) -> tuple[str, str]:
    """The (provider, model) a tool result would be sent with, from config.yaml."""
    block = config.get("model")
    if not isinstance(block, dict):
        return "", ""
    model = str(block.get("default") or block.get("model") or block.get("name") or "").strip()
    provider = str(block.get("provider") or "").strip()
    return provider, model


def vision_attached() -> bool:
    """True when the configured model is declared able to see images.

    Only documented ``config.yaml`` keys are consulted, in the order Hermes reads
    them for its own image routing — no private core API, so nothing here can break
    on a Hermes release that is not also a documented config change:

    1. ``model.supports_vision`` (or its ``vision`` alias) — the explicit override;
    2. ``agent.image_input_mode`` — ``text`` vetoes, ``native`` opts in;
    3. ``providers.<provider>.models.<model>.supports_vision`` — the per-model
       override for a custom or relayed provider.

    When none of them says anything the answer is False: a text-only result is always
    valid, a stray image block is not. Two ways to get the image anyway — declare
    vision support in the config above, or call the tool with ``image: true``, which
    a vision model can decide for itself. A model that can see but is told "text"
    here is never stuck: the schema tells it to pass ``image: true``.
    """
    try:
        from hermes_cli.config import load_config_readonly

        config = load_config_readonly() or {}
    except Exception:
        return False
    block = config.get("model")
    if not isinstance(block, dict):
        return False

    declared = _capability_flag(block)
    if declared is not None:
        return declared

    agent_block = config.get("agent")
    mode = str((agent_block or {}).get("image_input_mode") or "").strip().lower() if isinstance(agent_block, dict) else ""
    if mode == "text":
        return False
    if mode == "native":
        return True

    provider, model = _model_identity(config)
    if not model:
        return False
    providers = config.get("providers")
    maps: list[Any] = []
    if isinstance(providers, dict):
        if provider and isinstance(providers.get(provider), dict):
            maps.append(providers[provider].get("models"))
        # ``model.default`` is often the catalog id (``anthropic/claude-opus-4.6``)
        # with no provider key set: the override then lives under that provider.
        if not provider and "/" in model:
            head = model.split("/", 1)[0]
            if isinstance(providers.get(head), dict):
                maps.append(providers[head].get("models"))
    keys = [model]
    if "/" in model:
        keys.append(model.split("/", 1)[1])
    for entry_map in maps:
        if not isinstance(entry_map, dict):
            continue
        for key in keys:
            flag = _capability_flag(entry_map.get(key))
            if flag is not None:
                return flag
    return False


def _image_part(shot, text: str, *, want: bool | None) -> str | dict:
    """Attach the stored screen to the text result when it is useful and safe.

    ``want`` is tri-state: True (attach), False (text only), None (ask the config).
    """
    settings = get_settings()
    if not settings.show_images:
        return text
    if want is None:
        want = vision_attached()
    if not want:
        return text
    candidates = [shot.path, shot.thumb_path]
    for candidate in candidates:
        if not candidate:
            continue
        if not os.path.isfile(candidate):
            continue
        payload = images.data_url(candidate)
        if not payload:
            continue
        note = (
            "\n\nThe screen itself is attached to this result — you can see it. "
            "Use the layout and any text the OCR missed; quote what matters."
        )
        return {
            "_multimodal": True,
            "text_summary": text + note,
            "meta": {"shot_id": shot.id, "image_path": candidate, "native_vision": True},
            "content": [
                {"type": "text", "text": text + note},
                {"type": "image_url", "image_url": {"url": payload}},
            ],
        }
    return text


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
    if not shots and not db.stats()["chars"]:
        if not ocr.available():
            return render.results(query, shots) + (
                "\n\nThe index holds no text because OCR is not installed on this machine. "
                "Screens are still recorded and can be looked at with screen_timeline / "
                "screen_show. Install tesseract to make text search work "
                "(macOS: brew install tesseract; Debian/Ubuntu: apt install tesseract-ocr)."
            )
        return render.results(query, shots) + (
            "\n\nThe index holds no text yet: nothing has been captured or OCR'd. "
            "Call screen_capture to remember what is on screen now, or check /peripheral "
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


def screen_show(args: dict | None = None, **kwargs: Any) -> str | dict:
    data = args or {}
    try:
        shot_id = int(data.get("id") or 0)
    except (TypeError, ValueError):
        return "Error: screen_show needs the numeric id shown as [#id]."
    shot = db.get(shot_id)
    if shot is None:
        return f"No screen with id {shot_id}. Use screen_recall or screen_timeline to find one."
    chars = _int(data.get("chars"), 6000, 200, 40000)
    want_image = data.get("image")
    return _image_part(
        shot, render.record(shot, body_chars=chars),
        want=None if want_image is None else bool(want_image),
    )


def screen_capture(args: dict | None = None, **kwargs: Any) -> str | dict:
    """Grab the screen now, index it, and hand it back."""
    data = args or {}
    settings = get_settings()
    if not settings.enabled:
        return (
            "Peripheral is paused, so this screen was not captured. "
            "Resume with /peripheral resume."
        )
    note = str(data.get("note") or "").strip()[:120]
    want_image = data.get("image")
    outcome = ingest.capture_and_ingest(settings, force=True)
    if not outcome.ok:
        return (
            f"No screen was captured. {outcome.detail or outcome.reason}.\n"
            "Peripheral still answers from the screenshots it has indexed; "
            "/peripheral status shows whether capture is available on this machine."
        )
    shot = db.get(outcome.shot_id) if outcome.shot_id else None
    header = outcome.line()
    if shot is None:
        return header
    if note:
        # The note is the user's own words for why this screen matters: store it
        # as the title so a later search or timeline row reads the way they said it.
        db.set_title(shot.id, note)
        shot = db.get(shot.id) or shot
        header = f"{header} · note: {note}"
    return _image_part(
        shot, f"{header}\n\n{render.record(shot)}",
        want=None if want_image is None else bool(want_image),
    )


def screen_forget(args: dict | None = None, **kwargs: Any) -> str:
    """Delete screens the user asked to forget (rows, thumbnails and owned frames)."""
    data = args or {}
    if data.get("all"):
        removed = db.forget(None)
        return (
            f"Forgotten: {removed} screen{'s' if removed != 1 else ''} removed from the index, "
            "with every thumbnail and captured frame this plugin wrote. Your own screenshot "
            "files were not touched."
        )
    if data.get("id") is None:
        return "Error: screen_forget needs an id, or all=true to clear the whole index."
    try:
        shot_id = int(data.get("id"))
    except (TypeError, ValueError):
        return "Error: screen_forget needs the numeric id shown as [#id]."
    shot = db.get(shot_id)
    if shot is None:
        return f"No screen with id {shot_id} — nothing to forget."
    removed = db.forget(shot_id)
    if not removed:
        return f"Screen #{shot_id} could not be removed."
    if shot.source == "capture":
        return f"Forgotten: screen #{shot_id} and the frame Peripheral captured for it are deleted."
    return (
        f"Forgotten: screen #{shot_id} is out of the index, with the thumbnail this plugin "
        "wrote for it. The original screenshot file is untouched."
    )


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
