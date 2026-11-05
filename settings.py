"""Settings resolution for the peripheral plugin.

Every key here mirrors a ``config_schema`` entry in ``plugin.yaml`` one-for-one:
the Desktop Settings form, ``hermes config set`` and this module must agree on a
single source of truth, and ``tests/test_manifest.py`` asserts the key sets are
identical. Values are coerced defensively — a typo in ``config.yaml`` must never
be able to stop the plugin from answering.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

PLUGIN_ID = "peripheral"

SCHEMA_KEYS = (
    "enabled",
    "watch_dirs",
    "ocr_languages",
    "auto_scan_minutes",
    "include_photos",
    "max_text_chars",
    "retention_days",
    "redact",
    "thumbnails",
    "record_agent_events",
    "capture_enabled",
    "capture_interval_seconds",
    "capture_min_gap_seconds",
    "capture_max_per_hour",
    "show_images",
)

# Directories that hold screenshots on each platform, most specific first. Only
# the ones that exist are used, and never recursively beyond one level for the
# broad ones (``~/Pictures`` can be enormous).
_SCREENSHOT_HINTS = {
    "posix": (
        "~/Pictures/Screenshots",
        "~/Pictures/ScreenShots",
        "~/Screenshots",
        "~/Desktop/Screenshots",
        "~/Desktop",
    ),
    "win32": (
        "~/Pictures/Screenshots",
        "~/Pictures/ScreenSketch",
        "~/OneDrive/Pictures/Screenshots",
        "~/Desktop",
    ),
}


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return default


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _expand(raw: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(raw).strip())))


def default_watch_dirs(hermes_home: Path | None = None) -> list[Path]:
    """The screenshot folders that exist on this machine, plus inbound media."""
    hints = _SCREENSHOT_HINTS.get("win32" if os.name == "nt" else "posix", ())
    found: list[Path] = []
    for hint in hints:
        path = _expand(hint)
        if path.is_dir() and path not in found:
            found.append(path)
    # Images the user sends to Hermes over a gateway land here; indexing them
    # means "what was that screenshot I sent you?" works without any setup.
    if hermes_home is not None:
        media = hermes_home / "media"
        if media.is_dir() and media not in found:
            found.append(media)
    return found


@dataclass(frozen=True)
class Settings:
    enabled: bool = True
    watch_dirs: tuple[Path, ...] = ()
    ocr_languages: str = "eng"
    auto_scan_minutes: int = 10
    include_photos: bool = False
    max_text_chars: int = 20000
    retention_days: int = 0
    redact: bool = True
    thumbnails: bool = True
    record_agent_events: bool = True
    capture_enabled: bool = False
    capture_interval_seconds: int = 30
    capture_min_gap_seconds: int = 15
    capture_max_per_hour: int = 120
    show_images: bool = True

    @property
    def languages(self) -> list[str]:
        seen: list[str] = []
        for part in str(self.ocr_languages).replace(",", "+").split("+"):
            code = part.strip()
            if code and code not in seen:
                seen.append(code)
        return seen or ["eng"]

    @classmethod
    def from_ctx(cls, ctx: Any, hermes_home: Path | None = None) -> "Settings":
        get = getattr(ctx, "get_config", None)
        if not callable(get):  # pragma: no cover - older hosts
            return cls(watch_dirs=tuple(default_watch_dirs(hermes_home)))

        def value(key: str, fallback: Any) -> Any:
            try:
                resolved = get(key, default=fallback)
            except TypeError:  # hosts whose get_config takes one argument
                resolved = get(key)
            return fallback if resolved is None else resolved

        raw_dirs = value("watch_dirs", [])
        if isinstance(raw_dirs, str):
            raw_dirs = [part for part in raw_dirs.replace(",", "\n").splitlines()]
        dirs: list[Path] = []
        for entry in raw_dirs or ():
            path = _expand(str(entry))
            if str(entry).strip() and path not in dirs:
                dirs.append(path)
        if not dirs:
            dirs = default_watch_dirs(hermes_home)

        return cls(
            enabled=_bool(value("enabled", True), True),
            watch_dirs=tuple(dirs),
            ocr_languages=str(value("ocr_languages", "eng") or "eng"),
            auto_scan_minutes=_int(value("auto_scan_minutes", 10), 10, 0, 1440),
            include_photos=_bool(value("include_photos", False), False),
            max_text_chars=_int(value("max_text_chars", 20000), 20000, 200, 200000),
            retention_days=_int(value("retention_days", 0), 0, 0, 36500),
            redact=_bool(value("redact", True), True),
            thumbnails=_bool(value("thumbnails", True), True),
            record_agent_events=_bool(value("record_agent_events", True), True),
            capture_enabled=_bool(value("capture_enabled", False), False),
            capture_interval_seconds=_int(value("capture_interval_seconds", 30), 30, 5, 3600),
            capture_min_gap_seconds=_int(value("capture_min_gap_seconds", 15), 15, 0, 3600),
            capture_max_per_hour=_int(value("capture_max_per_hour", 120), 120, 1, 3600),
            show_images=_bool(value("show_images", True), True),
        )

    def describe(self) -> str:
        dirs: Iterable[str] = (str(path) for path in self.watch_dirs)
        capture = (
            f"background capture: every {self.capture_interval_seconds}s "
            f"(max {self.capture_max_per_hour}/hour)"
            if self.capture_enabled
            else "background capture: off (the agent can still capture on request)"
        )
        return "\n".join(
            [
                f"enabled: {self.enabled}",
                f"folders: {', '.join(dirs) or '(none exist — set watch_dirs)'}",
                f"languages: {'+'.join(self.languages)}",
                f"background scan: every {self.auto_scan_minutes} min"
                if self.auto_scan_minutes
                else "background scan: off",
                capture,
                f"photos: {'indexed' if self.include_photos else 'skipped'}",
                f"redaction: {'on' if self.redact else 'off'}",
                f"images in results: {'attached' if self.show_images else 'text only'}",
                f"retention: {self.retention_days or 'forever'}",
            ]
        )
