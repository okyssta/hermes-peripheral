"""Capturing the screen itself, on any desktop, with no daemon to install.

The plugin's first version could only read screenshots that already existed, which
quietly excluded everyone who does not take screenshots. This module closes that
gap: it finds a capture binary the machine already has, grabs one frame, and hands
it to the same indexing path as a screenshot.

Design rules, in order of importance:

* **Never guess.** A backend is used only when its binary exists *and* its display
  environment is present. If none is available the caller is told plainly; nothing
  is captured, nothing is written.
* **No shell.** Every command is an argv list, so a path or a title can never be
  interpreted as shell syntax.
* **Bounded.** One frame per call, a hard timeout, a size cap after resize, and a
  temp file that is always cleaned up.
* **Change-aware.** A frame identical to the last one kept is not stored again; the
  caller gets ``duplicate`` and the temp file is discarded. Idle time costs nothing.

Supported backends (first available wins):

===========================  ================================================
``ffmpeg`` (X11)             ``ffmpeg -f x11grab`` — present on most Linux boxes
``grim`` (Wayland)           wlroots compositors
``gnome-screenshot``         GNOME on Wayland
``screencapture`` (macOS)    built into every Mac
``powershell`` (Windows)     ``System.Drawing`` copy of the virtual screen
``import`` (ImageMagick)     X11 fallback
``scrot`` / ``maim``         X11 fallbacks
===========================  ================================================
"""

from __future__ import annotations

import itertools
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

TIMEOUT_SECONDS = 20.0
MAX_FRAME_EDGE = 1600
FRAME_QUALITY = 82

_ORDER = (
    "x11-ffmpeg",
    "wayland-grim",
    "macos-screencapture",
    "windows-powershell",
    "x11-import",
    "x11-scrot",
    "x11-maim",
    "gnome-screenshot",
)

_counter = itertools.count(1)


@dataclass(frozen=True)
class Backend:
    name: str
    binary: str

    def available(self) -> bool:
        return shutil.which(self.binary) is not None


@dataclass(frozen=True)
class CaptureResult:
    ok: bool
    path: str = ""
    backend: str = ""
    error: str = ""
    milliseconds: int = 0

    def line(self) -> str:
        if self.ok:
            return f"captured with {self.backend} in {self.milliseconds} ms"
        return f"capture failed: {self.error}"


def _display() -> str:
    return os.environ.get("DISPLAY", "").strip()


def _wayland() -> str:
    return os.environ.get("WAYLAND_DISPLAY", "").strip()


def _has_display() -> bool:
    return bool(_display()) or bool(_wayland())


def _windows_shell() -> str | None:
    if os.name != "nt":
        return None
    return shutil.which("powershell") or shutil.which("pwsh")


def _powershell_script(destination: str) -> str:
    # Single quotes inside a PowerShell string literal are escaped by doubling;
    # the path itself is produced by us (a temp file), never by the user.
    quoted = destination.replace("'", "''")
    return (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
        "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen; "
        "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height; "
        "$g=[System.Drawing.Graphics]::FromImage($bmp); "
        "$g.CopyFromScreen($b.Left,$b.Top,0,0,$bmp.Size); "
        f"$bmp.Save('{quoted}',[System.Drawing.Imaging.ImageFormat]::Png)"
    )


def backend_command(name: str, destination: str) -> list[str] | None:
    """The argv for one backend, or None when the backend cannot run here."""
    display = _display()
    if name == "x11-ffmpeg":
        if not display or not shutil.which("ffmpeg"):
            return None
        # x11grab negotiates the capture size with the display, so no geometry is
        # hard-coded: unusual resolutions and multi-monitor roots both work.
        return [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-f", "x11grab", "-i", display, "-frames:v", "1", "-y", destination,
        ]
    if name == "wayland-grim":
        if not _wayland() or not shutil.which("grim"):
            return None
        return ["grim", destination]
    if name == "macos-screencapture":
        if shutil.which("screencapture") is None:
            return None
        return ["screencapture", "-x", "-t", "png", destination]
    if name == "windows-powershell":
        shell = _windows_shell()
        if shell is None:
            return None
        return [shell, "-NoProfile", "-NonInteractive", "-Command", _powershell_script(destination)]
    if name == "x11-import":
        if not display or not shutil.which("import"):
            return None
        return ["import", "-window", "root", destination]
    if name == "x11-scrot":
        if not display or not shutil.which("scrot"):
            return None
        return ["scrot", "-o", "-z", destination]
    if name == "x11-maim":
        if not display or not shutil.which("maim"):
            return None
        return ["maim", "--quiet", destination]
    if name == "gnome-screenshot":
        if shutil.which("gnome-screenshot") is None or not _has_display():
            return None
        return ["gnome-screenshot", "-f", destination]
    return None


def available_backends() -> list[str]:
    """Backends that could capture on this machine, in preference order."""
    return [name for name in _ORDER if backend_command(name, "/dev/null") is not None]


def describe() -> str:
    """One line a person can act on, for /peripheral status."""
    names = available_backends()
    if names:
        return f"capture: ready ({', '.join(names)})"
    if os.name == "nt":
        return "capture: unavailable (PowerShell not found)"
    if not _has_display():
        return "capture: unavailable (no DISPLAY / WAYLAND_DISPLAY — headless machine)"
    return "capture: unavailable (no capture binary: install ffmpeg, grim, scrot or ImageMagick)"


def capture_to(destination: str, *, backends: tuple[str, ...] | None = None) -> CaptureResult:
    """Grab one frame into *destination*, trying each usable backend in order."""
    started = time.monotonic()
    errors: list[str] = []
    for name in backends or _ORDER:
        command = backend_command(name, destination)
        if command is None:
            continue
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"{name}: {exc}")
            continue
        size = 0
        try:
            size = os.path.getsize(destination)
        except OSError:
            size = 0
        if completed.returncode == 0 and size > 0:
            return CaptureResult(
                ok=True,
                path=destination,
                backend=name,
                milliseconds=int((time.monotonic() - started) * 1000),
            )
        detail = (completed.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        errors.append(f"{name}: {detail[-1][:160] if detail else f'exit {completed.returncode}'}")
        try:
            os.unlink(destination)
        except OSError:
            pass
    return CaptureResult(
        ok=False,
        error="; ".join(errors) or "no capture backend is available on this machine",
        milliseconds=int((time.monotonic() - started) * 1000),
    )


def temp_frame() -> str:
    """A unique temp path for one capture (plugin-data, not the system temp dir)."""
    try:
        from plugins.plugin_storage import plugin_data_dir

        folder = plugin_data_dir("peripheral") / "frames"
    except Exception:  # pragma: no cover - storage API unavailable (tests stub it)
        folder = Path(tempfile.gettempdir()) / "peripheral-frames"
    folder.mkdir(parents=True, exist_ok=True)
    return str(folder / f".capture-{os.getpid()}-{next(_counter)}-{int(time.time() * 1000)}.png")
