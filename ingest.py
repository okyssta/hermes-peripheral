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
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import capture, db, images, ocr
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


def ingest_file(
    path: str,
    settings: Settings,
    *,
    recent: list[str] | None = None,
    source: str = "scan",
    kind_hint: str | None = None,
    thumb_override: str | None = None,
) -> tuple[int | None, str]:
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
    # Three reasons to keep an image:
    #  * it carries readable text (a normal screen),
    #  * the user asked for photos too,
    #  * or OCR is not installed at all — then the index is *visual*: the frame is
    #    kept so the agent can look at it, and text search simply has nothing to
    #    match until Tesseract appears. Without this the plugin silently indexed
    #    nothing on a machine without tesseract, which is the common first install.
    keep_visual = not has_text and (settings.include_photos or not ocr.available())
    if not has_text and not keep_visual:
        db.mark_file(path, stat.st_mtime, stat.st_size, info.sha256, None)
        return None, "no_text"

    if _near_duplicate(info.dhash, recent or []):
        db.mark_file(path, stat.st_mtime, stat.st_size, info.sha256, None)
        return None, "duplicate"

    thumb = thumb_override or ""
    if settings.thumbnails and not thumb:
        thumb = images.save_thumbnail(path, _thumb_path(info.sha256))

    kind = kind_hint or ("screenshot" if has_text else "photo")
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
            "kind": kind,
            "source": source,
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


# ------------------------------------------------------------------ capture


@dataclass
class CaptureOutcome:
    """What one screen grab produced, in words a tool result can carry."""

    ok: bool = False
    reason: str = ""          # added | duplicate | unchanged | unavailable | error
    shot_id: int | None = None
    backend: str = ""
    milliseconds: int = 0
    detail: str = ""
    path: str = ""

    def line(self) -> str:
        if not self.ok:
            return f"could not capture the screen: {self.detail or self.reason}"
        if self.reason == "added":
            return f"screen #{self.shot_id} captured ({self.backend}, {self.milliseconds} ms)"
        if self.reason == "duplicate":
            return f"that screen is already indexed as #{self.shot_id} — nothing new stored"
        if self.reason == "unchanged":
            return "the screen has not changed since the last capture — nothing stored"
        return self.reason


def _frames_dir() -> Path:
    try:
        from plugins.plugin_storage import plugin_data_dir

        return plugin_data_dir(db.PLUGIN_ID) / "frames"
    except Exception:  # pragma: no cover - storage API unavailable (tests stub it)
        return Path(tempfile.gettempdir()) / "peripheral-frames"


def capture_and_ingest(
    settings: Settings,
    *,
    force: bool = True,
    change_distance: int = NEAR_DUPLICATE_DISTANCE,
) -> CaptureOutcome:
    """Grab the screen, keep it if it is new, and index it like a screenshot.

    ``force`` is what a person asking "remember this screen" means: store it even
    if the previous frame was similar. The background watcher passes ``force=False``
    so a static desktop writes nothing at all.
    """
    started = time.monotonic()
    temp = capture.temp_frame()
    result = capture.capture_to(temp)
    if not result.ok:
        return CaptureOutcome(
            ok=False,
            reason="unavailable" if "no capture backend" in result.error else "error",
            detail=result.error,
            milliseconds=result.milliseconds,
        )
    try:
        info = images.inspect(temp)
        if info is None:
            return CaptureOutcome(ok=False, reason="error", detail="the capture was not a readable image")
        existing = db.shot_by_sha(info.sha256)
        if existing is not None:
            return CaptureOutcome(
                ok=True, reason="duplicate", shot_id=existing.id, backend=result.backend,
                milliseconds=int((time.monotonic() - started) * 1000),
            )
        if not force:
            last = db.recent_dhashes(1, source="capture")
            if last and info.dhash and images.hamming(info.dhash, last[0]) <= change_distance:
                return CaptureOutcome(
                    ok=True, reason="unchanged", backend=result.backend,
                    milliseconds=int((time.monotonic() - started) * 1000),
                )
        frame = _frames_dir() / f"{info.sha256}.webp"
        stored = images.save_frame(temp, frame)
        if not stored:
            return CaptureOutcome(ok=False, reason="error", detail="could not encode the captured frame")
        shot_id, reason = ingest_file(
            stored,
            settings,
            recent=[],
            source="capture",
            kind_hint="capture",
            thumb_override=stored,
        )
        if reason == "added":
            return CaptureOutcome(
                ok=True, reason="added", shot_id=shot_id, backend=result.backend,
                milliseconds=int((time.monotonic() - started) * 1000), path=stored,
            )
        if reason == "no_text":
            # An empty frame with OCR installed (a blank desktop, say) is not worth
            # keeping: the row was never written, so remove the frame too.
            try:
                Path(stored).unlink(missing_ok=True)
            except OSError:
                pass
            return CaptureOutcome(
                ok=True, reason="unchanged", backend=result.backend,
                milliseconds=int((time.monotonic() - started) * 1000),
            )
        return CaptureOutcome(
            ok=True, reason=reason, shot_id=shot_id, backend=result.backend,
            milliseconds=int((time.monotonic() - started) * 1000), path=stored,
        )
    finally:
        try:
            os.unlink(temp)
        except OSError:
            pass


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


class ScreenWatcher:
    """Optional background capture: the screen remembers itself.

    Off by default. When a user turns it on they get a rolling visual history
    without ever taking a screenshot. Three limiters keep it honest:

    * an interval (how often to look),
    * a change gate inside :func:`capture_and_ingest` (a static desktop stores
      nothing), and
    * a per-hour ceiling on how fast frames accumulate (a rate limit, not a size
      limit: retention is a separate setting and is off by default).

    Every failure is swallowed: a watcher that throws would take the gateway with
    it, and a missing capture binary is a normal state, not an error.
    """

    def __init__(self, settings_provider) -> None:
        self._settings_provider = settings_provider
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._kept: list[float] = []

    def start(self, interval_seconds: int, min_gap_seconds: int = 15, max_per_hour: int = 120) -> None:
        if interval_seconds <= 0 or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop,
            args=(max(5, interval_seconds), max(0, min_gap_seconds), max(1, max_per_hour)),
            name="peripheral-capture",
            daemon=True,
        )
        self._thread.start()

    def _allowed(self, max_per_hour: int) -> bool:
        now = time.time()
        self._kept = [stamp for stamp in self._kept if now - stamp < 3600]
        return len(self._kept) < max_per_hour

    def _loop(self, interval_seconds: int, min_gap_seconds: int, max_per_hour: int) -> None:
        last_kept = 0.0
        while not self._stop.wait(interval_seconds):
            try:
                settings = self._settings_provider()
                if not settings or not settings.enabled or not settings.capture_enabled:
                    continue
                if not self._allowed(max_per_hour):
                    continue
                if time.time() - last_kept < min_gap_seconds:
                    continue
                outcome = capture_and_ingest(settings, force=False)
                if outcome.ok and outcome.reason == "added":
                    last_kept = time.time()
                    self._kept.append(last_kept)
            except Exception:  # pragma: no cover - background capture never crashes the host
                pass

    def stop(self) -> None:
        self._stop.set()
        self._thread = None
