"""Indexing: find images, read them, store them, keep the index tidy.

The scan is incremental and idempotent. A file is only re-read when its mtime or
size changed, a content hash that is already indexed is skipped, and a screenshot
that is nearly identical to one already stored (dHash distance <= 3) is dropped —
scrolling a page must not fill the index with forty near-copies of the same screen.

The scan is also *bounded*: a first run over a large folder OCRs at most
``batch`` new images, so asking a question never blocks the turn for minutes.
The next scan continues where this one stopped.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import db, images, ocr
from .settings import Settings

_INGEST_LOCK = threading.Lock()
NEAR_DUPLICATE_DISTANCE = 3
MAX_DEPTH = 2


@dataclass
class ScanReport:
    scanned: int = 0        # files considered
    added: int = 0          # new entries
    duplicates: int = 0     # same pixels (sha) or near-identical (dHash)
    skipped: int = 0        # unchanged since last scan
    no_text: int = 0        # photo/art skipped by policy
    errors: int = 0
    pruned: int = 0
    seconds: float = 0.0
    ocr_available: bool = True
    ocr_hint: str = ""
    folders: list[str] = field(default_factory=list)
    added_ids: list[int] = field(default_factory=list)

    def line(self) -> str:
        if self.scanned == 0:
            return "no images found in the watched folders"
        bits = [f"{self.scanned} file{'s' if self.scanned != 1 else ''} considered"]
        if self.added:
            bits.append(f"{self.added} added")
        if self.duplicates:
            bits.append(f"{self.duplicates} duplicate")
        if self.no_text:
            bits.append(f"{self.no_text} without text")
        if self.skipped:
            bits.append(f"{self.skipped} unchanged")
        if self.errors:
            bits.append(f"{self.errors} unreadable")
        if self.pruned:
            bits.append(f"{self.pruned} expired")
        return f"{', '.join(bits)} in {self.seconds:.1f}s"


def candidates(folders: list[Path], *, limit: int, since: float | None = None) -> list[str]:
    """Newest-first image files under the watched folders."""
    found: list[tuple[float, str]] = []
    for folder in folders:
        if not folder.is_dir():
            continue
        base_depth = len(folder.parts)
        for root, dirnames, filenames in os.walk(folder):
            depth = len(Path(root).parts) - base_depth
            if depth >= MAX_DEPTH:
                dirnames[:] = []
            dirnames[:] = [name for name in dirnames if not name.startswith(".")]
            for name in filenames:
                if Path(name).suffix.lower() not in images.IMAGE_SUFFIXES:
                    continue
                path = os.path.join(root, name)
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    continue
                if since is not None and mtime < since:
                    continue
                found.append((mtime, path))
    found.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in found[: max(limit, 1)]]


def _thumb_path(sha: str) -> Path:
    from plugins.plugin_storage import plugin_data_dir

    return plugin_data_dir(db.PLUGIN_ID) / "thumbs" / sha[:2] / f"{sha}.webp"


def _near_duplicate(dhash: str, recent: list[str]) -> bool:
    if not dhash:
        return False
    return any(images.hamming(dhash, other) <= NEAR_DUPLICATE_DISTANCE for other in recent)


def ingest_file(path: str, settings: Settings, *, recent: list[str] | None = None) -> tuple[int | None, str]:
    """Index one image. Returns (shot id or None, reason)."""
    try:
        stat = os.stat(path)
    except OSError:
        return None, "missing"
    info = images.inspect(path)
    if info is None:
        return None, "unreadable"

    previous = db.file_state(path)
    if previous is not None and previous["sha256"] == info.sha256:
        db.mark_file(path, stat.st_mtime, stat.st_size, info.sha256, previous["shot_id"])
        return (int(previous["shot_id"]) if previous["shot_id"] else None), "duplicate"

    result = ocr.read_text(path, settings.languages)
    text = ocr.clean_text(result.text, max_chars=settings.max_text_chars)
    if settings.redact:
        text = ocr.redact(text)

    has_text = images.is_text_image(text, info.width, info.height)
    if not has_text and not settings.include_photos:
        db.mark_file(path, stat.st_mtime, stat.st_size, info.sha256, None)
        return None, "no_text"

    if _near_duplicate(info.dhash, recent or []):
        db.mark_file(path, stat.st_mtime, stat.st_size, info.sha256, None)
        return None, "duplicate"

    thumb = ""
    if settings.thumbnails:
        thumb = images.save_thumbnail(path, _thumb_path(info.sha256))

    shot_id = db.insert_shot(
        {
            "path": path,
            "sha256": info.sha256,
            "dhash": info.dhash,
            "captured_at": info.captured_at,
            "ingested_at": time.time(),
            "width": info.width,
            "height": info.height,
            "bytes": info.bytes,
            "kind": "screenshot" if has_text else "photo",
            "source": "scan",
            "thumb_path": thumb,
            "ocr_text": text,
            "ocr_lang": result.lang,
            "ocr_ms": result.ms,
            "text_chars": len(text),
            "title": ocr.guess_title(text),
        }
    )
    db.mark_file(path, stat.st_mtime, stat.st_size, info.sha256, shot_id)
    return shot_id, "added" if shot_id else "duplicate"


def scan(settings: Settings, *, batch: int = 60, force: bool = False) -> ScanReport:
    """Index new images from the watched folders. Never raises."""
    started = time.monotonic()
    report = ScanReport(
        folders=[str(folder) for folder in settings.watch_dirs],
        ocr_available=ocr.available(),
        ocr_hint=ocr.version_hint(),
    )
    if not _INGEST_LOCK.acquire(blocking=False):
        report.seconds = 0.0
        report.errors = 0
        return report
    try:
        cutoff = time.time() - settings.retention_days * 86400 if settings.retention_days else None
        paths = candidates(
            list(settings.watch_dirs), limit=max(batch * 8, 400), since=cutoff
        )
        recent = db.recent_dhashes(3000)
        budget = max(1, batch)
        for path in paths:
            report.scanned += 1
            if not force:
                previous = db.file_state(path)
                try:
                    stat = os.stat(path)
                except OSError:
                    report.errors += 1
                    continue
                if previous is not None and previous["mtime"] == stat.st_mtime and previous["size"] == stat.st_size:
                    report.skipped += 1
                    continue
            shot_id, reason = ingest_file(path, settings, recent=recent)
            if reason == "added":
                report.added += 1
                if shot_id:
                    report.added_ids.append(shot_id)
                budget -= 1
            elif reason == "duplicate":
                report.duplicates += 1
            elif reason == "no_text":
                report.no_text += 1
            elif reason == "unreadable":
                report.errors += 1
            if budget <= 0:
                break

        if settings.retention_days:
            cutoff = time.time() - settings.retention_days * 86400
            for thumb in db.thumbs_for(cutoff):
                try:
                    Path(thumb).unlink(missing_ok=True)
                except OSError:
                    pass
            report.pruned = db.prune(settings.retention_days)
        known = {path for path in paths}
        db.drop_missing_files(known)
    finally:
        _INGEST_LOCK.release()
        report.seconds = time.monotonic() - started
    return report


def ingest_paths(paths: list[str], settings: Settings) -> ScanReport:
    """Index specific files (a screenshot the user just pointed at)."""
    started = time.monotonic()
    report = ScanReport(ocr_available=ocr.available(), ocr_hint=ocr.version_hint())
    recent = db.recent_dhashes(3000)
    for path in paths:
        expanded = str(Path(os.path.expanduser(path)))
        if os.path.isdir(expanded):
            continue
        report.scanned += 1
        shot_id, reason = ingest_file(expanded, settings, recent=recent)
        if reason == "added":
            report.added += 1
            if shot_id:
                report.added_ids.append(shot_id)
        elif reason == "duplicate":
            report.duplicates += 1
        elif reason == "no_text":
            report.no_text += 1
        elif reason == "unreadable":
            report.errors += 1
    report.seconds = time.monotonic() - started
    return report


class AutoScanner:
    """Background rescan on a timer, one thread, silent failures.

    Screenshots are produced by other apps, so nothing tells the plugin a new one
    exists. This thread closes that gap without a filesystem-watch dependency: it
    wakes on an interval, indexes a small batch, and goes back to sleep.
    """

    def __init__(self, settings_provider) -> None:
        self._settings_provider = settings_provider
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, interval_minutes: int) -> None:
        if interval_minutes <= 0 or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, args=(interval_minutes,), name="peripheral-scan", daemon=True)
        self._thread.start()

    def _loop(self, interval_minutes: int) -> None:
        # First pass shortly after boot: a user who restarts Hermes expects
        # yesterday's screenshots to be findable without running a command.
        delay = 20.0
        while not self._stop.wait(delay):
            delay = max(60.0, interval_minutes * 60.0)
            try:
                settings = self._settings_provider()
                if settings and settings.enabled:
                    scan(settings, batch=40)
            except Exception:  # pragma: no cover - a background job never crashes the gateway
                pass

    def stop(self) -> None:
        self._stop.set()
        self._thread = None
