"""SQLite storage for the peripheral index.

One file (``<HERMES_HOME>/plugin-data/peripheral/data.db``) holds three things:
the image rows, an FTS5 index over their OCR text, and a small event log of what
the agent did, so a timeline can interleave the user's screens with the agent's
actions. Nearest-neighbour dedupe uses a 64-bit difference hash stored per row.

Nothing here talks to the network, and nothing outside this file's own tables is
read or written.
"""

from __future__ import annotations

import math
import re
import sqlite3
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

PLUGIN_ID = "peripheral"
SCHEMA_VERSION = 1

# Hermes opens plugin databases with a 5 s busy timeout; that is right for a tool
# that writes once per call, wrong for the `post_tool_call` observer that writes
# after every tool call. 1.5 s is long enough for a competing writer to commit and
# short enough to disappear inside a turn.
BUSY_TIMEOUT_MS = 1500

_LOCK = threading.RLock()
_CONN: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shots (
    id           INTEGER PRIMARY KEY,
    path         TEXT NOT NULL,
    sha256       TEXT NOT NULL UNIQUE,
    dhash        TEXT,
    captured_at  REAL NOT NULL,
    ingested_at  REAL NOT NULL,
    width        INTEGER NOT NULL DEFAULT 0,
    height       INTEGER NOT NULL DEFAULT 0,
    bytes        INTEGER NOT NULL DEFAULT 0,
    kind         TEXT NOT NULL DEFAULT 'screenshot',
    source       TEXT NOT NULL DEFAULT '',
    thumb_path   TEXT,
    ocr_text     TEXT NOT NULL DEFAULT '',
    ocr_lang     TEXT NOT NULL DEFAULT '',
    ocr_ms       INTEGER NOT NULL DEFAULT 0,
    text_chars   INTEGER NOT NULL DEFAULT 0,
    title        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS shots_captured ON shots (captured_at DESC);
CREATE INDEX IF NOT EXISTS shots_dhash ON shots (dhash);

CREATE VIRTUAL TABLE IF NOT EXISTS shots_fts USING fts5(
    ocr_text,
    content='shots',
    content_rowid='id',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS shots_ai AFTER INSERT ON shots BEGIN
    INSERT INTO shots_fts (rowid, ocr_text) VALUES (new.id, new.ocr_text);
END;
CREATE TRIGGER IF NOT EXISTS shots_ad AFTER DELETE ON shots BEGIN
    INSERT INTO shots_fts (shots_fts, rowid, ocr_text) VALUES ('delete', old.id, old.ocr_text);
END;
CREATE TRIGGER IF NOT EXISTS shots_au AFTER UPDATE ON shots BEGIN
    INSERT INTO shots_fts (shots_fts, rowid, ocr_text) VALUES ('delete', old.id, old.ocr_text);
    INSERT INTO shots_fts (rowid, ocr_text) VALUES (new.id, new.ocr_text);
END;

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY,
    at         REAL NOT NULL,
    kind       TEXT NOT NULL,
    name       TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS events_at ON events (at DESC);

CREATE TABLE IF NOT EXISTS files (
    path    TEXT PRIMARY KEY,
    mtime   REAL NOT NULL DEFAULT 0,
    size    INTEGER NOT NULL DEFAULT 0,
    sha256  TEXT NOT NULL DEFAULT '',
    shot_id INTEGER,
    seen_at REAL NOT NULL DEFAULT 0
);
"""


@dataclass(frozen=True)
class Shot:
    id: int
    path: str
    captured_at: float
    kind: str
    source: str
    thumb_path: str | None
    text_chars: int
    title: str
    ocr_text: str = ""
    score: float = 0.0
    snippet: str = ""

    @property
    def when(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.captured_at))

    @property
    def ago(self) -> str:
        return human_age(self.captured_at)


def human_age(then: float, now: float | None = None) -> str:
    delta = max(0.0, (now or time.time()) - then)
    if delta < 90:
        return "just now"
    minutes = delta / 60
    if minutes < 60:
        return f"{int(minutes)}m ago"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)}h ago"
    days = hours / 24
    if days < 30:
        return f"{int(days)}d ago"
    months = days / 30
    if months < 12:
        return f"{int(months)}mo ago"
    return f"{int(months / 12)}y ago"


def connect() -> sqlite3.Connection:
    """Open (once per process) and migrate the index."""
    global _CONN
    with _LOCK:
        if _CONN is not None:
            return _CONN
        from plugins.plugin_storage import plugin_db

        conn = plugin_db(PLUGIN_ID)
        # `post_tool_call` writes here after every tool call, so the plugin caps its
        # own wait instead of inheriting the 5 s default: a second Hermes process on
        # the same profile holding the write lock must cost one skipped event, not a
        # five-second stall in the middle of a turn.
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
            )
        conn.commit()
        _CONN = conn
        return conn


def close() -> None:
    """Drop the cached connection (tests, profile switches, plugin reload)."""
    global _CONN
    with _LOCK:
        if _CONN is not None:
            try:
                _CONN.close()
            finally:
                _CONN = None


def _rows(sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
    conn = connect()
    with _LOCK:
        return conn.execute(sql, params).fetchall()


# ---------------------------------------------------------------- writes

EDITABLE = {"ocr_text", "text_chars", "title", "kind", "thumb_path"}


def insert_shot(record: dict[str, Any]) -> int | None:
    """Insert one image row. Returns its id, or None when the hash already exists."""
    columns = (
        "path", "sha256", "dhash", "captured_at", "ingested_at", "width", "height",
        "bytes", "kind", "source", "thumb_path", "ocr_text", "ocr_lang", "ocr_ms",
        "text_chars", "title",
    )
    values = [record.get(column) for column in columns]
    values[columns.index("ingested_at")] = record.get("ingested_at") or time.time()
    placeholders = ", ".join("?" for _ in columns)
    conn = connect()
    with _LOCK:
        cursor = conn.execute(
            f"INSERT OR IGNORE INTO shots ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )
        conn.commit()
        if cursor.rowcount == 0:
            return None
        return int(cursor.lastrowid or 0)


def known_hashes() -> set[str]:
    """Every content hash already indexed (used to skip re-reading files)."""
    return {row["sha256"] for row in _rows("SELECT sha256 FROM shots")}


def recent_dhashes(limit: int = 4000, *, source: str | None = None) -> list[str]:
    if source:
        rows = _rows(
            "SELECT dhash FROM shots WHERE dhash IS NOT NULL AND source = ? "
            "ORDER BY captured_at DESC LIMIT ?",
            (source, limit),
        )
    else:
        rows = _rows("SELECT dhash FROM shots WHERE dhash IS NOT NULL ORDER BY captured_at DESC LIMIT ?", (limit,))
    return [row["dhash"] for row in rows if row["dhash"]]


def shot_by_sha(sha256: str) -> Shot | None:
    """The row for an image already indexed, by content hash."""
    if not sha256:
        return None
    rows = _rows("SELECT * FROM shots WHERE sha256 = ? LIMIT 1", (sha256,))
    return _row_to_shot(rows[0]) if rows else None


_EVENT_WRITES = 0


def record_event(kind: str, name: str, detail: str = "", session_id: str = "", at: float | None = None) -> bool:
    """Append one agent event. The log is capped so a long-lived install cannot
    grow it without bound: every 500 writes, the oldest rows beyond 20k go.

    ``detail`` usually comes from a tool's arguments (a shell command, a URL, a
    query), so it is redacted here — the storage boundary — rather than trusting
    every caller to remember. The README and the catalog disclosure both promise
    that no secret is written to disk; this is what makes that true for events
    as well as for OCR text.

    Never waits for a lock: this is called from ``post_tool_call``, once per tool
    call, and a second Hermes process on the same profile must cost a skipped
    timeline row rather than a stall inside the turn. Returns False when the event
    was dropped.
    """
    global _EVENT_WRITES
    from .ocr import redact

    safe_detail = redact(str(detail or ""))[:400]
    conn = connect()
    with _LOCK:
        conn.execute("PRAGMA busy_timeout=0")
        try:
            conn.execute(
                "INSERT INTO events (at, kind, name, detail, session_id) VALUES (?, ?, ?, ?, ?)",
                (at or time.time(), kind, name[:120], safe_detail, session_id[:120]),
            )
            _EVENT_WRITES += 1
            if _EVENT_WRITES % 500 == 0:
                conn.execute(
                    "DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY at DESC LIMIT 20000)"
                )
            conn.commit()
            return True
        except sqlite3.OperationalError:
            # Locked or busy: the tool call this describes matters more than the row.
            with suppress(Exception):
                conn.rollback()
            return False
        finally:
            with suppress(Exception):
                conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")


def set_title(shot_id: int, title: str) -> None:
    """Give a screen a human label (the user's own note, or a correction)."""
    conn = connect()
    with _LOCK:
        conn.execute("UPDATE shots SET title = ? WHERE id = ?", (str(title)[:120], int(shot_id)))
        conn.commit()


def _unlink(path: str | None) -> int:
    """Remove one file the index owns. Never touches the user's own images."""
    if not path:
        return 0
    try:
        Path(path).unlink(missing_ok=True)
        return 1
    except OSError:  # pragma: no cover - a locked/removed file must not fail a forget
        return 0


def _sweep_thumbs() -> int:
    """Remove every thumbnail under plugin-data/peripheral/thumbs (orphans too)."""
    try:
        from plugins.plugin_storage import plugin_data_dir

        root = plugin_data_dir(PLUGIN_ID) / "thumbs"
    except Exception:  # pragma: no cover - storage API unavailable (tests stub it)
        return 0
    if not root.is_dir():
        return 0
    removed = 0
    for path in root.rglob("*"):
        if path.is_file() and _unlink(str(path)):
            removed += 1
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass
    return removed


def _sweep_frames() -> int:
    """Remove every frame Peripheral captured under plugin-data/frames."""
    try:
        from plugins.plugin_storage import plugin_data_dir

        root = plugin_data_dir(PLUGIN_ID) / "frames"
    except Exception:  # pragma: no cover - storage API unavailable (tests stub it)
        return 0
    if not root.is_dir():
        return 0
    removed = 0
    for path in root.rglob("*"):
        if path.is_file() and _unlink(str(path)):
            removed += 1
    return removed


def forget(shot_id: int | None = None) -> int:
    """Delete one entry, or the whole index — rows *and* the image files this
    plugin wrote. The user's original screenshots are never touched.

    Two kinds of file can belong to a row: a 360 px thumbnail, and — for screens
    Peripheral captured itself — the frame it wrote under ``plugin-data/frames``.
    Leaving either behind after "forget" would make the promise false, so both go
    with the rows. A path that lives outside plugin-data (the user's own
    screenshots) is never unlinked.
    """
    conn = connect()
    with _LOCK:
        if shot_id is None:
            thumbs = thumbnails()
            owned = owned_files()
            cursor = conn.execute("DELETE FROM shots")
            conn.execute("DELETE FROM files")
        else:
            rows = _rows("SELECT thumb_path, path, source FROM shots WHERE id = ?", (shot_id,))
            thumbs = [row["thumb_path"] for row in rows if row["thumb_path"]]
            owned = [row["path"] for row in rows if row["source"] == "capture" and row["path"]]
            cursor = conn.execute("DELETE FROM shots WHERE id = ?", (shot_id,))
            # Drop the bookkeeping too: "forget" must survive the next scan, and a
            # later /peripheral rescan should be able to bring the file back.
            conn.execute("DELETE FROM files WHERE shot_id = ?", (shot_id,))
        conn.commit()
    # Outside the lock: unlinking is I/O, and a failure here must not roll back
    # the row deletion the user asked for.
    for path in {*thumbs, *owned}:
        _unlink(path)
    if shot_id is None:
        _sweep_thumbs()
        _sweep_frames()
    return int(cursor.rowcount or 0)


def owned_files() -> list[str]:
    """Every file the plugin captured itself (deletable, unlike the user's own)."""
    return [
        row["path"]
        for row in _rows("SELECT path FROM shots WHERE source = 'capture' AND path != ''")
        if row["path"]
    ]


def prune(retention_days: int, now: float | None = None) -> int:
    """Drop entries older than the retention window — rows and the files the
    plugin wrote for them (thumbnails and captured frames)."""
    if retention_days <= 0:
        return 0
    cutoff = (now or time.time()) - retention_days * 86400
    rows = _rows(
        "SELECT thumb_path, path, source FROM shots WHERE captured_at < ?", (cutoff,)
    )
    files = [row["thumb_path"] for row in rows if row["thumb_path"]]
    files += [row["path"] for row in rows if row["source"] == "capture" and row["path"]]
    conn = connect()
    with _LOCK:
        cursor = conn.execute("DELETE FROM shots WHERE captured_at < ?", (cutoff,))
        conn.execute("DELETE FROM files WHERE shot_id IS NULL OR shot_id NOT IN (SELECT id FROM shots)")
        conn.commit()
        removed = int(cursor.rowcount or 0)
    for path in files:
        _unlink(path)
    return removed


def thumbnails() -> list[str]:
    return [row["thumb_path"] for row in _rows("SELECT thumb_path FROM shots WHERE thumb_path IS NOT NULL")]


# ---------------------------------------------------------------- file bookkeeping

def file_state(path: str) -> sqlite3.Row | None:
    rows = _rows("SELECT mtime, size, sha256, shot_id FROM files WHERE path = ?", (path,))
    return rows[0] if rows else None


def mark_file(path: str, mtime: float, size: int, sha256: str, shot_id: int | None, *, at: float | None = None) -> None:
    conn = connect()
    with _LOCK:
        conn.execute(
            "INSERT INTO files (path, mtime, size, sha256, shot_id, seen_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime, size=excluded.size, "
            "sha256=excluded.sha256, shot_id=excluded.shot_id, seen_at=excluded.seen_at",
            (path, mtime, size, sha256, shot_id, at or time.time()),
        )
        conn.commit()


def thumbs_for(cutoff: float) -> list[str]:
    rows = _rows(
        "SELECT thumb_path FROM shots WHERE captured_at < ? AND thumb_path IS NOT NULL", (cutoff,)
    )
    return [row["thumb_path"] for row in rows if row["thumb_path"]]


def drop_missing_files(known_paths: set[str]) -> int:
    """Forget bookkeeping for files that no longer exist (index rows stay put)."""
    rows = _rows("SELECT path FROM files")
    gone = [row["path"] for row in rows if row["path"] not in known_paths]
    if not gone:
        return 0
    conn = connect()
    with _LOCK:
        conn.executemany("DELETE FROM files WHERE path = ?", [(path,) for path in gone])
        conn.commit()
    return len(gone)


# ---------------------------------------------------------------- reads

_WORD_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_'./:@+-]*")
_SPLIT_LINES = re.compile(r"\n+")


def best_snippet(text: str, terms: list[str], *, width: int = 200) -> str:
    """The line of the screen that best answers the query.

    FTS5's own snippet window is chosen by BM25 over tokens, which on a dense page
    can land on a neighbouring paragraph. The full text is stored, so the window is
    re-chosen here: the line holding the most query terms wins, preferring the one
    that packs them closest together.
    """
    if not text or not terms:
        return ""
    lowered = [term.lower() for term in terms if term]
    if not lowered:
        return ""
    best, best_score = "", 0.0
    for line in _SPLIT_LINES.split(text):
        stripped = " ".join(line.split())
        if len(stripped) < 12:
            continue
        haystack = stripped.lower()
        occurrences = sum(haystack.count(term) for term in lowered)
        if not occurrences:
            continue
        # Coverage first, then how often the words appear, then a mild preference
        # for a line long enough to be quoted as evidence rather than a heading.
        coverage = sum(1 for term in lowered if term in haystack) / len(lowered)
        score = (
            occurrences * 2.0
            + coverage * 2.0
            + min(len(haystack), 160) / 160.0
        )
        if score > best_score:
            best, best_score = stripped, score
    if not best:
        return ""
    if len(best) <= width:
        return best
    # Window a long line around the first query term so the quote starts with
    # evidence rather than with whatever navigation text sits to its left.
    lowered_line = best.lower()
    positions = [lowered_line.find(term) for term in lowered if term in lowered_line]
    start = max(0, min(positions or [0]) - 30)
    window = best[start : start + width].strip()
    if start:
        window = "…" + window
    if start + width < len(best):
        window = window.rstrip() + "…"
    return window


def split_terms(text: str, *, max_terms: int = 12) -> list[str]:
    """The words a query is made of, in order, deduped case-insensitively."""
    terms: list[str] = []
    for match in _WORD_RE.finditer(text or ""):
        word = match.group(0).strip(".'/:@+-").replace('"', "")
        if len(word) < 2 or word.upper() in {"AND", "OR", "NOT", "NEAR"}:
            continue
        if word.lower() not in (term.lower() for term in terms):
            terms.append(word)
        if len(terms) >= max_terms:
            break
    return terms


def fts_query(text: str, *, max_terms: int = 12) -> str:
    """Turn free text into a safe FTS5 query.

    Each term becomes a quoted prefix token; operators in the user's wording
    (AND, OR, NEAR, *, ^, :) cannot escape into FTS syntax that raises. Terms are
    implicitly AND-ed so "stripe expired" is a conjunction rather than a flood.
    """
    return " ".join(f'"{term}"*' for term in split_terms(text, max_terms=max_terms))


def _row_to_shot(row: sqlite3.Row, snippet: str = "") -> Shot:
    return Shot(
        id=row["id"],
        path=row["path"],
        captured_at=row["captured_at"],
        kind=row["kind"],
        source=row["source"],
        thumb_path=row["thumb_path"],
        text_chars=row["text_chars"],
        title=row["title"],
        snippet=snippet.strip(),
    )


def search(
    text: str,
    *,
    since: float | None = None,
    until: float | None = None,
    kind: str | None = None,
    limit: int = 8,
) -> list[Shot]:
    """Rank images by BM25, then re-rank with recency and text richness.

    When the strict token search finds nothing, a prefix fallback runs: OCR reads
    ``expired_card`` as ``expired card`` and mangles suffixes (``payme!`` for
    ``payment``), so a query that is one character off must not look like an empty
    memory. The fallback is still a SQL query over the same index — no model call,
    no scan of the user's files.
    """
    shots = _search_exact(text, since=since, until=until, kind=kind, limit=limit)
    if shots:
        return shots
    return _search_prefix(text, since=since, until=until, kind=kind, limit=limit)


def _search_exact(
    text: str,
    *,
    since: float | None = None,
    until: float | None = None,
    kind: str | None = None,
    limit: int = 8,
) -> list[Shot]:
    query = fts_query(text)
    if not query:
        return []
    clauses = ["shots_fts MATCH ?"]
    params: list[Any] = [query]
    if since is not None:
        clauses.append("s.captured_at >= ?")
        params.append(since)
    if until is not None:
        clauses.append("s.captured_at <= ?")
        params.append(until)
    if kind:
        clauses.append("s.kind = ?")
        params.append(kind)
    sql = (
        "SELECT s.*, bm25(shots_fts) AS bm, "
        "snippet(shots_fts, 0, '<<', '>>', ' … ', 14) AS snip "
        "FROM shots_fts JOIN shots s ON s.id = shots_fts.rowid "
        f"WHERE {' AND '.join(clauses)} "
        "ORDER BY bm LIMIT ?"
    )
    params.append(max(limit * 6, 24))
    try:
        rows = _rows(sql, params)
    except sqlite3.OperationalError:
        return []

    now = time.time()
    terms = split_terms(text)
    scored: list[Shot] = []
    for row in rows:
        bm = float(row["bm"] or 0.0)  # negative; more negative == better
        age_days = max(0.0, (now - float(row["captured_at"])) / 86400.0)
        recency = 1.0 / (1.0 + math.log1p(age_days) / 2.5)
        richness = min(1.0, float(row["text_chars"] or 0) / 2500.0)
        relevance = -bm
        score = relevance * (0.55 + 0.45 * recency) + 0.35 * richness
        snippet = best_snippet(row["ocr_text"] or "", terms) or (row["snip"] or "")
        shot = _row_to_shot(row, snippet)
        scored.append(Shot(**{**shot.__dict__, "score": score}))

    scored.sort(key=lambda shot: shot.score, reverse=True)
    return scored[:limit]


def _search_prefix(
    text: str,
    *,
    since: float | None = None,
    until: float | None = None,
    kind: str | None = None,
    limit: int = 8,
) -> list[Shot]:
    """Last-resort search: each term matched by its first four characters.

    This exists for OCR noise, not for semantics: ``declinded`` finds ``declined``
    because both start ``decl``. Terms shorter than four characters are skipped —
    a three-letter prefix matches half the corpus and would turn an empty result
    into a wrong one, which is worse than an honest miss.
    """
    terms = [term for term in split_terms(text) if len(term) >= 4]
    if not terms:
        return []
    lowered = {term.lower(): term.lower()[:4] for term in terms}
    clauses = ["lower(ocr_text) LIKE ?" for _ in lowered]
    params: list[Any] = [f"%{prefix}%" for prefix in lowered.values()]
    if since is not None:
        clauses.append("captured_at >= ?")
        params.append(since)
    if until is not None:
        clauses.append("captured_at <= ?")
        params.append(until)
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    params.append(max(limit * 4, 20))
    try:
        rows = _rows(
            f"SELECT * FROM shots WHERE {' AND '.join(clauses)} "
            "ORDER BY captured_at DESC LIMIT ?",
            params,
        )
    except sqlite3.OperationalError:  # pragma: no cover - defensive
        return []
    scored: list[Shot] = []
    for row in rows:
        body = (row["ocr_text"] or "").lower()
        coverage = sum(1 for prefix in lowered.values() if prefix in body) / len(lowered)
        if coverage < 0.5:
            continue
        shot = _row_to_shot(row, best_snippet(row["ocr_text"] or "", terms))
        scored.append(Shot(**{**shot.__dict__, "score": coverage}))
    scored.sort(key=lambda shot: (shot.score, shot.captured_at), reverse=True)
    return scored[:limit]


def get(shot_id: int) -> Shot | None:
    rows = _rows("SELECT * FROM shots WHERE id = ?", (shot_id,))
    if not rows:
        return None
    row = rows[0]
    shot = _row_to_shot(row)
    return Shot(**{**shot.__dict__, "ocr_text": row["ocr_text"] or ""})


def recent(limit: int = 40, *, since: float | None = None, until: float | None = None) -> list[Shot]:
    clauses = []
    params: list[Any] = []
    if since is not None:
        clauses.append("captured_at >= ?")
        params.append(since)
    if until is not None:
        clauses.append("captured_at <= ?")
        params.append(until)
    where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
    params.append(limit)
    rows = _rows(
        f"SELECT * FROM shots {where}ORDER BY captured_at DESC LIMIT ?", params
    )
    return [_row_to_shot(row) for row in rows]


def events_between(start: float, end: float, limit: int = 200) -> list[dict[str, Any]]:
    rows = _rows(
        "SELECT at, kind, name, detail FROM events WHERE at >= ? AND at <= ? ORDER BY at ASC LIMIT ?",
        (start, end, limit),
    )
    return [dict(row) for row in rows]


def stats() -> dict[str, Any]:
    row = _rows(
        "SELECT COUNT(*) AS n, MIN(captured_at) AS first, MAX(captured_at) AS last, "
        "SUM(text_chars) AS chars, SUM(bytes) AS bytes, "
        "SUM(CASE WHEN source = 'capture' THEN 1 ELSE 0 END) AS captured FROM shots"
    )[0]
    return {
        "count": int(row["n"] or 0),
        "first": row["first"],
        "last": row["last"],
        "chars": int(row["chars"] or 0),
        "bytes": int(row["bytes"] or 0),
        "captured": int(row["captured"] or 0),
        "events": int((_rows("SELECT COUNT(*) AS n FROM events")[0])["n"] or 0),
    }


def titles(limit: int = 6) -> Iterable[str]:
    for row in _rows(
        "SELECT title FROM shots WHERE title != '' GROUP BY title ORDER BY COUNT(*) DESC LIMIT ?", (limit,)
    ):
        yield row["title"]
