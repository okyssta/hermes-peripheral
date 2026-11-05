"""The pipeline end to end: files on disk → OCR → SQLite → ranked results."""

from __future__ import annotations

import shutil
import time

from conftest import make_screenshot

from peripheral import db, ingest, render, tools
from peripheral.settings import Settings


def _settings(folder, **overrides) -> Settings:
    base = dict(
        enabled=True,
        watch_dirs=(folder,),
        ocr_languages="eng",
        auto_scan_minutes=0,
        include_photos=True,
        max_text_chars=20000,
        retention_days=0,
        redact=True,
        thumbnails=True,
        record_agent_events=True,
    )
    base.update(overrides)
    return Settings(**base)


def test_indexes_screenshots_and_finds_them_by_text(hermes_home, tmp_path, ocr_ready):
    folder = tmp_path / "Screenshots"
    make_screenshot(
        folder / "Screenshot 2026-11-05 at 14.32.10.png",
        ["Stripe dashboard", "Payment failed", "Your card was declined", "code expired_card"],
    )
    make_screenshot(
        folder / "Screenshot 2026-11-05 at 09.05.00.png",
        ["GitHub pull request 482", "Add OCR fallback to the indexer", "checks passed"],
    )
    make_screenshot(
        folder / "Screenshot 2026-11-04 at 18.20.00.png",
        ["AWS billing", "Estimated charges this month", "USD 42.19"],
    )

    report = ingest.scan(_settings(folder), batch=10)
    assert report.added == 3, report.line()

    hits = db.search("card declined")
    assert hits, "the Stripe screen should match"
    assert "declined" in hits[0].snippet.lower() or "declined" in hits[0].title.lower()

    hits = db.search("checks passed")
    assert hits, "the GitHub screen should match"
    assert "Screenshot 2026-11-05 at 09.05.00" in hits[0].path
    assert "passed" in hits[0].snippet.lower()

    hits = db.search("estimated charges")
    assert hits and "42" in (hits[0].title + hits[0].snippet)

    assert db.search("kubernetes ingress") == []


def test_rescan_is_incremental_and_idempotent(hermes_home, tmp_path, ocr_ready):
    folder = tmp_path / "Shots"
    make_screenshot(folder / "a.png", ["Invoice 4471", "Total due 1,204.00"])
    settings = _settings(folder)

    first = ingest.scan(settings, batch=10)
    assert first.added == 1

    second = ingest.scan(settings, batch=10)
    assert second.added == 0
    assert second.skipped == 1

    forced = ingest.scan(settings, batch=10, force=True)
    assert forced.added == 0
    assert forced.duplicates == 1
    assert db.stats()["count"] == 1


def test_identical_copy_is_a_duplicate(hermes_home, tmp_path, ocr_ready):
    folder = tmp_path / "Shots"
    original = make_screenshot(folder / "one.png", ["Deploy failed", "build 8123 exited 1"])
    settings = _settings(folder)
    ingest.scan(settings, batch=10)
    shutil.copy(original, folder / "one-copy.png")

    report = ingest.scan(settings, batch=10)
    assert report.duplicates == 1
    assert db.stats()["count"] == 1


def test_forget_removes_from_search_but_not_disk(hermes_home, tmp_path, ocr_ready):
    folder = tmp_path / "Shots"
    make_screenshot(folder / "secret.png", ["Bank account 998877", "balance 15,000"])
    settings = _settings(folder)
    ingest.scan(settings, batch=10)

    hits = db.search("balance")
    assert hits
    shot_id = hits[0].id
    assert db.forget(shot_id) == 1
    assert db.search("balance") == []
    assert (folder / "secret.png").exists(), "the plugin never deletes the user's files"


def test_retention_prunes_old_entries(hermes_home, tmp_path, ocr_ready):
    import os

    folder = tmp_path / "Shots"
    old = make_screenshot(folder / "old.png", ["Old screenshot from March"])
    stamp = time.time() - 10 * 86400
    os.utime(old, (stamp, stamp))
    settings = _settings(folder, retention_days=1)
    ingest.scan(settings, batch=10)
    # The scan itself applies retention, so a ten-day-old screen never survives it.
    assert db.stats()["count"] == 0

    os.utime(old, (time.time(), time.time()))
    ingest.scan(_settings(folder), batch=10, force=True)
    assert db.stats()["count"] >= 1
    assert db.prune(3650) == 0


def test_timeline_interleaves_agent_events(hermes_home, tmp_path, ocr_ready):
    folder = tmp_path / "Shots"
    make_screenshot(folder / "a.png", ["Terraform plan", "1 to add, 0 to change"])
    settings = _settings(folder)
    ingest.scan(settings, batch=10)
    db.record_event("tool", "terminal", "terraform apply")

    shots = db.recent(10)
    assert shots
    text = render.timeline(shots, db.events_between(shots[-1].captured_at - 1, shots[0].captured_at + 60))
    assert "Terraform" in text
    assert "terminal" in text and "terraform apply" in text


def test_screen_index_tool_reports_on_a_folder(hermes_home, tmp_path, ocr_ready):
    folder = tmp_path / "Shots"
    make_screenshot(folder / "x.png", ["Quarterly revenue", "Q3 total 4.2M"])

    class _Ctx:
        def get_config(self, key, default=None):
            if key == "watch_dirs":
                return [str(folder)]
            return default

    peripheral_module = __import__("peripheral")
    peripheral_module._CTX = _Ctx()
    peripheral_module._CACHED = None
    try:
        text = tools.screen_index({"batch": 10})
        assert "added" in text.lower() or "1 added" in text
        found = tools.screen_recall({"query": "quarterly revenue"})
        assert "Quarterly" in found
    finally:
        peripheral_module._CTX = None
        peripheral_module._CACHED = None
