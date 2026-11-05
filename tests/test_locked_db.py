"""A locked index must cost a skipped event, not a stalled turn.

``post_tool_call`` runs after every tool call, so it is the one write in this plugin
that a user would feel if SQLite made it wait. Hermes opens plugin databases with a
5 s busy timeout; the event log opts out of waiting entirely — the row is dropped and
the tool call proceeds.
"""

from __future__ import annotations

import sqlite3
import time

from peripheral import _on_post_tool_call, db


def _database(hermes_home):
    return hermes_home / "plugin-data" / "peripheral" / "data.db"


def _exclusive_lock(hermes_home):
    db.connect()
    db.close()
    path = _database(hermes_home)
    assert path.is_file()
    holder = sqlite3.connect(str(path), timeout=0.1, isolation_level=None)
    holder.execute("PRAGMA busy_timeout=100")
    holder.execute("BEGIN EXCLUSIVE")
    return holder


def _release(holder):
    try:
        holder.execute("ROLLBACK")
    finally:
        holder.close()
        db.close()


def test_the_connection_caps_its_own_busy_timeout(hermes_home):
    connection = db.connect()
    pragma = connection.execute("PRAGMA busy_timeout").fetchone()[0]
    assert pragma == db.BUSY_TIMEOUT_MS
    assert db.BUSY_TIMEOUT_MS <= 2000
    db.close()


def test_record_event_does_not_wait_for_a_lock(hermes_home):
    holder = _exclusive_lock(hermes_home)
    started = time.monotonic()
    try:
        wrote = db.record_event("tool", "terminal", "echo hi", "s1")
        elapsed = time.monotonic() - started
    finally:
        _release(holder)
    assert wrote is False, "a locked index should drop the event, not queue it"
    assert elapsed < 1.0, f"record_event waited {elapsed:.2f}s on a locked index"


def test_post_tool_call_under_an_exclusive_lock_is_quick_and_quiet(hermes_home):
    holder = _exclusive_lock(hermes_home)
    started = time.monotonic()
    try:
        for _ in range(5):
            # Must not raise: an observer that throws would take the turn with it.
            _on_post_tool_call(tool_name="terminal", args={"command": "echo hi"}, session_id="s1")
        elapsed = time.monotonic() - started
    finally:
        _release(holder)
    assert elapsed < 1.0, f"a locked index stalled {elapsed:.2f}s across five tool events"


def test_events_written_after_the_lock_clears(hermes_home):
    holder = _exclusive_lock(hermes_home)
    try:
        dropped = _on_post_tool_call(tool_name="terminal", args={"command": "echo during-lock"}, session_id="s1")
    finally:
        _release(holder)
    assert dropped is None  # the hook stays silent either way

    wrote = db.record_event("tool", "terminal", "echo after-lock", "s1")
    assert wrote is True
    rows = db.events_between(0, time.time() + 60, limit=10)
    detail = lambda row: row["detail"] if isinstance(row, (sqlite3.Row, dict)) else row.detail  # noqa: E731
    assert any("after-lock" in str(detail(row)) for row in rows)
    db.close()
