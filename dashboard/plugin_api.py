"""Backend routes for the Peripheral desktop pane.

Mounted by the gateway at ``/api/plugins/peripheral/...``. Every route reads the
same SQLite index the agent's tools write — no second store, no network, no
paths taken from the client. Thumbnails travel as data URLs so the pane needs no
extra auth surface to show them.

The router is the only export the loader looks for.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

_PLUGIN: Any = None
_TEXT_CAP = 20_000


def _plugin() -> Any:
    """The already-imported plugin package, whatever name the host gave it.

    In production the loader imports the package (``peripheral`` or
    ``plugins.peripheral``) before it imports this module, so the match by
    ``__file__`` finds it without running ``__init__`` a second time.
    """
    global _PLUGIN
    if _PLUGIN is not None:
        return _PLUGIN

    init = (Path(__file__).resolve().parents[1] / "__init__.py")
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if not path:
            continue
        try:
            if Path(path).resolve() == init:
                _PLUGIN = module
                return module
        except OSError:
            continue

    root = init.parent
    if str(root.parent) not in sys.path:
        sys.path.insert(0, str(root.parent))
    for name in ("peripheral", f"plugins.{root.name}"):
        try:
            _PLUGIN = importlib.import_module(name)
            return _PLUGIN
        except Exception:  # pragma: no cover - second name is the fallback
            continue
    raise HTTPException(503, "peripheral plugin package is not importable")


def _thumb(images: Any, path: str | None, *, edge: int = 320) -> str | None:
    if not path:
        return None
    try:
        value = images.data_url(path, max_edge=edge)
    except Exception:
        return None
    return value or None


def _item(images: Any, row: dict[str, Any]) -> dict[str, Any]:
    snippet = (row.get("snippet") or "").strip()
    title = (row.get("title") or "").strip() or snippet[:90]
    return {
        "id": int(row["id"]),
        "captured_at": float(row["captured_at"]),
        "when": _when(float(row["captured_at"])),
        "ago": _ago(float(row["captured_at"])),
        "title": title,
        "snippet": snippet,
        "kind": row.get("kind") or "screenshot",
        "source": row.get("source") or "",
        "chars": int(row.get("text_chars") or 0),
        "thumb": _thumb(images, row.get("thumb_path")),
    }


def _when(at: float) -> str:
    import time

    return time.strftime("%Y-%m-%d %H:%M", time.localtime(at))


def _ago(at: float) -> str:
    p = _plugin()
    return p.db.human_age(at)


# ------------------------------------------------------------------ reads


@router.get("/status")
def status() -> dict[str, Any]:
    p = _plugin()
    stats = p.db.stats()
    settings = p.current_settings()
    return {
        "count": stats["count"],
        "captured": stats["captured"],
        "first": stats["first"],
        "last": stats["last"],
        "chars": stats["chars"],
        "bytes": stats["bytes"],
        "events": stats["events"],
        "ocr": bool(p.ocr.available()),
        "ocr_hint": p.ocr.version_hint(),
        "capture_backends": list(p.capture.available_backends()),
        "capture_enabled": bool(settings.capture_enabled),
        "watch_dirs": [str(folder) for folder in settings.watch_dirs],
    }


@router.get("/shots")
def shots(
    limit: int = 24,
    q: str = "",
    since: float | None = None,
    until: float | None = None,
) -> dict[str, Any]:
    p = _plugin()
    limit = max(1, min(int(limit or 24), 120))
    text = (q or "").strip()

    if text:
        found = p.db.search(text, since=since, until=until, limit=limit)
    else:
        found = p.db.recent(limit, since=since, until=until)

    rows = [
        {
            "id": shot.id,
            "captured_at": shot.captured_at,
            "title": shot.title,
            "kind": shot.kind,
            "source": shot.source,
            "text_chars": shot.text_chars,
            "thumb_path": shot.thumb_path,
            "snippet": shot.snippet,
        }
        for shot in found
    ]
    return {
        "items": [_item(p.images, row) for row in rows],
        "query": text,
        "count": len(rows),
        "total": p.db.stats()["count"],
    }


@router.get("/shot/{shot_id}")
def shot(shot_id: int, image: bool = False, max_edge: int = 1280) -> dict[str, Any]:
    p = _plugin()
    rows = p.db._rows(
        "SELECT id, path, captured_at, kind, source, thumb_path, ocr_text, text_chars, "
        "title, width, height, bytes, ocr_lang, ocr_ms FROM shots WHERE id = ?",
        (int(shot_id),),
    )
    if not rows:
        raise HTTPException(404, "no such screen")
    row = dict(rows[0])
    item = _item(p.images, row)
    item.update(
        {
            "text": (row.get("ocr_text") or "")[:_TEXT_CAP],
            "width": int(row.get("width") or 0),
            "height": int(row.get("height") or 0),
            "bytes": int(row.get("bytes") or 0),
            "ocr_lang": row.get("ocr_lang") or "",
            "ocr_ms": int(row.get("ocr_ms") or 0),
        }
    )
    if image:
        item["image"] = _thumb(
            p.images, row.get("path"), edge=max(320, min(int(max_edge or 1280), 1600))
        )
    return item


@router.get("/events")
def events(since: float | None = None, until: float | None = None, limit: int = 200) -> dict[str, Any]:
    import time

    p = _plugin()
    end = float(until if until is not None else time.time())
    start = float(since if since is not None else end - 24 * 3600)
    rows = p.db.events_between(start, end, limit=max(1, min(int(limit or 200), 500)))
    return {"items": [dict(row) for row in rows], "since": start, "until": end}


# ----------------------------------------------------------------- writes


class ForgetBody(BaseModel):
    id: int | None = None
    all: bool = False


@router.post("/forget")
def forget(body: ForgetBody) -> dict[str, Any]:
    p = _plugin()
    if body.all:
        deleted = p.db.forget(None)
    elif body.id is None:
        raise HTTPException(400, "give a screen id, or all=true")
    else:
        deleted = p.db.forget(int(body.id))
    return {"deleted": int(deleted), "remaining": p.db.stats()["count"]}


@router.post("/capture")
def capture_now() -> dict[str, Any]:
    p = _plugin()
    outcome = p.ingest.capture_and_ingest(p.current_settings(), force=True)
    return {
        "ok": bool(outcome.ok),
        "reason": outcome.reason,
        "id": outcome.shot_id,
        "line": outcome.line(),
    }


@router.post("/scan")
def scan_now(batch: int = 60) -> dict[str, Any]:
    p = _plugin()
    report = p.ingest.scan(p.current_settings(), batch=max(1, min(int(batch or 60), 400)))
    return {
        "line": p.render.scan_result(report, prefix="indexed"),
        "added": int(report.added),
        "scanned": int(report.scanned),
        "seconds": float(report.seconds),
        "ocr_available": bool(report.ocr_available),
    }
