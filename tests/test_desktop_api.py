"""The desktop half's backend routes.

These call the router's endpoint functions directly — the same objects FastAPI
mounts at ``/api/plugins/peripheral/*`` — so the contract the pane relies on
(shape, ordering, missing-file tolerance) is tested without an HTTP client or a
display.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from peripheral import db, ingest
from peripheral.dashboard import plugin_api as api


def _insert(home: Path, *, name="a.png", text="payment declined for card ending 4242", title="Checkout"):
    image = home / name
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"not really a png")  # the route must tolerate a bad thumb
    shot_id = db.insert_shot(
        {
            "path": str(image),
            "sha256": "sha-" + name,
            "dhash": "0" * 16,
            "captured_at": time.time() - 60,
            "ingested_at": time.time(),
            "width": 1280,
            "height": 800,
            "bytes": 1234,
            "kind": "screenshot",
            "source": "screenshot",
            "thumb_path": str(image),
            "ocr_text": text,
            "ocr_lang": "eng",
            "ocr_ms": 120,
            "text_chars": len(text),
            "title": title,
        }
    )
    assert shot_id
    return shot_id


def test_status_reports_empty_index(hermes_home):
    out = api.status()
    assert out["count"] == 0
    assert isinstance(out["ocr"], bool)
    assert isinstance(out["capture_backends"], list)


def test_shots_and_detail_roundtrip(hermes_home):
    shot_id = _insert(hermes_home)
    page = api.shots(limit=10, q="")
    assert page["count"] == 1
    item = page["items"][0]
    assert item["id"] == shot_id
    assert item["chars"] > 0
    assert item["when"] and item["ago"]
    assert item["thumb"] is None  # unreadable file: omitted, not fatal

    detail = api.shot(shot_id, image=True)
    assert detail["id"] == shot_id
    assert "declined" in detail["text"]
    assert detail["width"] == 1280
    assert detail["image"] is None  # same tolerance on the full frame


def test_search_uses_the_same_index(hermes_home):
    _insert(hermes_home, text="expired card on the billing page", title="Billing")
    _insert(hermes_home, name="b.png", text="pytest run finished, 48 passed", title="Tests")

    hits = api.shots(limit=10, q="expired card")
    assert hits["query"] == "expired card"
    assert hits["count"] == 1
    assert "Billing" in hits["items"][0]["title"]

    # the prefix fallback has to survive OCR noise through this route too
    noisy = api.shots(limit=10, q="expird")
    assert noisy["count"] >= 1


def test_unknown_screen_is_404(hermes_home):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as caught:
        api.shot(9999)
    assert caught.value.status_code == 404


def test_forget_one_and_all(hermes_home):
    first = _insert(hermes_home)
    _insert(hermes_home, name="b.png", text="something else", title="Other")

    out = api.forget(api.ForgetBody(id=first))
    assert out["deleted"] == 1 and out["remaining"] == 1

    with pytest.raises(Exception):
        api.forget(api.ForgetBody())  # neither id nor all

    out = api.forget(api.ForgetBody(all=True))
    assert out["remaining"] == 0


def test_capture_and_scan_report_what_happened(hermes_home, monkeypatch):
    monkeypatch.setattr(
        ingest,
        "capture_and_ingest",
        lambda settings, **kw: ingest.CaptureOutcome(
            ok=True, reason="added", shot_id=7, backend="fake", milliseconds=12
        ),
    )
    out = api.capture_now()
    assert out["ok"] is True and out["id"] == 7
    assert "captured" in out["line"]

    monkeypatch.setattr(
        ingest,
        "scan",
        lambda settings, **kw: ingest.ScanReport(
            scanned=3, added=1, duplicates=1, seconds=0.4, ocr_available=True
        ),
    )
    report = api.scan_now()
    assert report["added"] == 1 and report["scanned"] == 3
    assert "indexed:" in report["line"]


def test_events_route_bounds_its_window(hermes_home):
    db.record_event("tool", "terminal", "ran pytest", session_id="s1")
    out = api.events(limit=10)
    assert out["until"] >= out["since"]
    assert out["items"] == [] or out["items"][0]["kind"] == "tool"
