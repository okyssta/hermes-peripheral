"""Reading the text out of an image.

Tesseract is the OCR engine when the machine has it; the plugin never downloads
anything and never calls a model to index a screenshot. Everything here is
deterministic, bounded and local: one subprocess per image, a hard timeout, a
single thread, and a cleaned-up text body that is safe to store.

If Tesseract is missing the plugin still works as a *visual* index: images are
recorded with their time, size and thumbnail, and the agent can open any of them
with ``screen_show`` (and look at it with its own vision tool). Text search simply
stays empty until OCR is available.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass

OCR_TIMEOUT_SECONDS = 25.0
_ALNUM = re.compile(r"[^\W_]", re.UNICODE)

# Secrets worth never writing to disk. Ordered most-specific first; the point is
# to make an indexed screenshot harmless, not to be a complete DLP product.
_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[private key]"),
    (re.compile(r"\b(sk|rk|pk)-[A-Za-z0-9_\-]{16,}\b"), "[api key]"),
    (re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"), "[github token]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[github token]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"), "[slack token]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[aws key]"),
    (re.compile(r"\bAIza[0-9A-Za-z\-_]{30,}\b"), "[google key]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"), "[jwt]"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-+/=]{16,}"), r"\1 [redacted]"),
    # Credentials embedded in a connection URL: postgres://user:pass@host/db.
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^/\s:@]+):([^@\s/]{1,})@"), r"\1:[redacted]@"),
    (re.compile(r"(?i)(\"?(password|passwd|secret|token|api[_-]?key)\"?\s*[:=]\s*\"?)[^\s\",;]{6,}"), r"\1[redacted]"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[card]"),
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "[hex]"),
)


@dataclass(frozen=True)
class OcrResult:
    text: str
    lang: str
    ms: int


_AVAILABLE: bool | None = None
_VERSION_HINT: str = ""
_VERSION_LOCK = threading.Lock()


def tesseract_path() -> str | None:
    return shutil.which("tesseract")


def available() -> bool:
    """Is a usable Tesseract on PATH? (Cached; safe to call per ingest.)"""
    global _AVAILABLE, _VERSION_HINT
    with _VERSION_LOCK:
        if _AVAILABLE is None:
            binary = tesseract_path()
            if not binary:
                _AVAILABLE = False
                _VERSION_HINT = "tesseract not found on PATH"
            else:
                try:
                    out = subprocess.run(
                        [binary, "--version"], capture_output=True, text=True, timeout=10
                    )
                    _AVAILABLE = out.returncode == 0
                    _VERSION_HINT = (out.stdout or out.stderr).splitlines()[0] if _AVAILABLE else "tesseract --version failed"
                except (OSError, subprocess.SubprocessError) as exc:
                    _AVAILABLE = False
                    _VERSION_HINT = f"tesseract failed: {exc}"
        return bool(_AVAILABLE)


def version_hint() -> str:
    available()
    return _VERSION_HINT


def installed_languages() -> set[str]:
    binary = tesseract_path()
    if not binary:
        return set()
    try:
        out = subprocess.run([binary, "--list-langs"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return set()
    return {
        line.strip()
        for line in (out.stdout or "").splitlines()
        if line.strip() and " " not in line.strip() and line.strip() != "List of available languages"
    }


def _run(binary: str, image: str, lang: str) -> tuple[str, str]:
    env = dict(os.environ)
    env["OMP_THREAD_LIMIT"] = "1"  # one screenshot must not eat every core
    completed = subprocess.run(
        [binary, image, "stdout", "-l", lang, "--psm", "3", "-c", "preserve_interword_spaces=1"],
        capture_output=True,
        text=True,
        timeout=OCR_TIMEOUT_SECONDS,
        env=env,
        errors="replace",
    )
    return completed.stdout or "", completed.stderr or ""


def read_text(path: str, languages: list[str]) -> OcrResult:
    """OCR one image. Returns cleaned text; raises nothing the caller must catch."""
    import time as _time

    binary = tesseract_path()
    if not binary:
        return OcrResult("", "", 0)
    wanted = [code for code in languages if code]
    attempts = ["+".join(wanted) if wanted else "eng"]
    if attempts[0] != "eng":
        attempts.append("eng")
    started = _time.monotonic()
    last_error = ""
    for lang in attempts:
        try:
            raw, err = _run(binary, path, lang)
        except subprocess.TimeoutExpired:
            return OcrResult("", lang, int((_time.monotonic() - started) * 1000))
        except OSError as exc:  # pragma: no cover - binary vanished mid-run
            return OcrResult("", lang, int((_time.monotonic() - started) * 1000))
        if raw.strip():
            return OcrResult(
                clean_text(raw), lang, int((_time.monotonic() - started) * 1000)
            )
        last_error = err.strip()
        # A missing language pack is the only reason to retry with another set.
        if "Failed loading language" not in last_error and "does not exist" not in last_error:
            break
    return OcrResult("", attempts[-1], int((_time.monotonic() - started) * 1000))


def clean_text(raw: str, *, max_chars: int = 20000) -> str:
    """Collapse OCR noise into something worth indexing and quoting."""
    lines: list[str] = []
    previous = ""
    for line in (raw or "").replace("\r", "\n").split("\n"):
        collapsed = " ".join(line.split())
        if not collapsed:
            continue
        if not _ALNUM.search(collapsed):
            continue
        if len(collapsed) <= 2 and not collapsed.isdigit():
            continue
        if collapsed == previous:  # UI repeats the same row; keep one
            continue
        lines.append(collapsed)
        previous = collapsed
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars]
    return text


def redact(text: str) -> str:
    cleaned = text
    for pattern, replacement in _REDACTIONS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


def guess_title(text: str) -> str:
    """Best-effort page/window title from the top of the screen.

    A real screenshot starts with the window or tab title, so early lines win;
    navigation clutter ("Sign in", "Docs", a row of links) loses on word count,
    alphabetic ratio and capitalisation. It is a label, not a truth claim — the
    snippet and the path are what the agent actually quotes.
    """
    best = ""
    best_score = 0.0
    for index, line in enumerate((text or "").split("\n")[:14]):
        candidate = line.strip(" |-–—•·>#*")
        if not 8 <= len(candidate) <= 88:
            continue
        words = candidate.split()
        if not 2 <= len(words) <= 12:
            continue
        alnum = len(_ALNUM.findall(candidate))
        ratio = alnum / max(1, len(candidate))
        if ratio < 0.72:
            continue
        capitalised = sum(1 for word in words if word[:1].isupper()) / len(words)
        score = (
            ratio
            + 0.6 * min(len(words) / 5.0, 1.0)
            + 0.5 * capitalised
            + 0.3 * (1.0 if 16 <= len(candidate) <= 64 else 0.0)
            - 0.12 * index
        )
        if score > best_score:
            best, best_score = candidate, score
    return best


def text_density(text: str, width: int, height: int) -> float:
    """Rough 'how much of this image is text' measure, in characters per 10k px."""
    pixels = max(1, int(width) * int(height))
    return len(_ALNUM.findall(text)) * 10000.0 / pixels
