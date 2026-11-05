"""Capture: the screen itself, indexed like a screenshot.

Every test here runs without a display: the capture *backend* is faked at the one
seam where a real binary would be invoked, so the storage, dedupe, privacy and
tool-contract behaviour is exercised for real on a headless machine.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import make_screenshot

from peripheral import capture, db, images, ingest, tools
from peripheral.settings import Settings


def _settings(folder=None, **overrides) -> Settings:
    base = dict(
        enabled=True,
        watch_dirs=(folder,) if folder else (),
        ocr_languages="eng",
        auto_scan_minutes=0,
        include_photos=False,
        max_text_chars=20000,
        retention_days=0,
        redact=True,
        thumbnails=True,
        record_agent_events=True,
        capture_enabled=False,
        capture_interval_seconds=30,
        capture_min_gap_seconds=0,
        capture_max_per_hour=120,
        show_images=True,
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture()
def fake_capture(tmp_path, monkeypatch):
    """A capture backend that writes whatever image the test supplies."""

    state = {"image": None, "calls": 0, "fail": False}

    def _capture_to(destination, *, backends=None):
        state["calls"] += 1
        if state["fail"]:
            return capture.CaptureResult(ok=False, error="no capture backend is available on this machine")
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        state["image"](destination)
        return capture.CaptureResult(ok=True, path=destination, backend="fake", milliseconds=1)

    monkeypatch.setattr(capture, "capture_to", _capture_to)
    monkeypatch.setattr(os, "unlink", os.unlink)  # keep the real unlink explicit
    return state


# --------------------------------------------------------------- backends


def test_backends_refuse_without_a_display(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert capture.backend_command("x11-ffmpeg", "/tmp/x.png") is None
    assert capture.backend_command("wayland-grim", "/tmp/x.png") is None
    assert "unavailable" in capture.describe()


def test_x11_capture_is_an_argv_list_and_never_a_shell_string(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(capture.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "ffmpeg" else None)
    command = capture.backend_command("x11-ffmpeg", "/tmp/out.png")
    assert isinstance(command, list)
    assert command[0] == "ffmpeg"
    assert ":99" in command
    assert "/tmp/out.png" == command[-1]
    assert not any(";" in part or "|" in part for part in command)


def test_missing_binary_means_no_command(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(capture.shutil, "which", lambda name: None)
    assert capture.backend_command("x11-ffmpeg", "/tmp/out.png") is None
    assert capture.backend_command("x11-scrot", "/tmp/out.png") is None


def test_capture_reports_unavailable_instead_of_raising():
    result = capture.capture_to("/tmp/never-written.png", backends=())
    assert result.ok is False
    assert "no capture backend" in result.error


def test_temporary_frame_is_outside_the_users_media_tree(hermes_home):
    path = capture.temp_frame()
    assert path.endswith(".png")
    assert "frames" in path


# --------------------------------------------------------------- storage


def test_capture_stores_a_frame_and_indexes_it(hermes_home, fake_capture):
    fake_capture["image"] = lambda dest: make_screenshot(
        Path(dest), ["Deploy failed", "error: container exited with code 137"], size=(1280, 800)
    )
    outcome = ingest.capture_and_ingest(_settings(), force=True)

    assert outcome.ok and outcome.reason == "added", outcome.line()
    shot = db.get(outcome.shot_id)
    assert shot is not None
    assert shot.source == "capture"
    assert shot.kind == "capture"
    assert Path(shot.path).is_file()
    assert shot.path == shot.thumb_path  # the frame *is* the image; no second copy
    assert "container exited" in shot.ocr_text


def test_capturing_the_same_screen_twice_stores_one_frame(hermes_home, fake_capture):
    fake_capture["image"] = lambda dest: make_screenshot(Path(dest), ["Same screen", "nothing changed here"])
    first = ingest.capture_and_ingest(_settings(), force=True)
    second = ingest.capture_and_ingest(_settings(), force=True)

    assert first.reason == "added"
    assert second.reason == "duplicate"
    assert second.shot_id == first.shot_id
    frames = list((hermes_home / "plugin-data" / "peripheral" / "frames").glob("*.webp"))
    assert len(frames) == 1


def test_background_capture_skips_a_screen_that_did_not_change(hermes_home, fake_capture):
    fake_capture["image"] = lambda dest: make_screenshot(Path(dest), ["Acme dashboard", "monthly recurring revenue 42,910 dollars", "active customers 1284"])
    assert ingest.capture_and_ingest(_settings(), force=True).reason == "added"

    from PIL import Image

    def _same_screen_plus_one_pixel(dest):
        make_screenshot(Path(dest), ["Acme dashboard", "monthly recurring revenue 42,910 dollars", "active customers 1284"])
        image = Image.open(dest).convert("RGB")
        image.putpixel((1, 1), (0, 0, 0))
        image.save(dest)

    fake_capture["image"] = _same_screen_plus_one_pixel
    outcome = ingest.capture_and_ingest(_settings(), force=False)
    assert outcome.reason == "unchanged", outcome.line()
    assert db.stats()["count"] == 1


def test_an_explicit_capture_of_a_similar_screen_is_kept(hermes_home, fake_capture):
    fake_capture["image"] = lambda dest: make_screenshot(Path(dest), ["Acme dashboard", "monthly recurring revenue 42,910 dollars", "active customers 1284"])
    first = ingest.capture_and_ingest(_settings(), force=True)

    from PIL import Image

    def _same_screen_plus_one_pixel(dest):
        make_screenshot(Path(dest), ["Acme dashboard", "monthly recurring revenue 42,910 dollars", "active customers 1284"])
        image = Image.open(dest).convert("RGB")
        image.putpixel((1, 1), (0, 0, 0))
        image.save(dest)

    fake_capture["image"] = _same_screen_plus_one_pixel
    second = ingest.capture_and_ingest(_settings(), force=True)
    assert second.reason == "added"
    assert second.shot_id != first.shot_id


def test_capture_failure_is_reported_not_raised(hermes_home, fake_capture):
    fake_capture["fail"] = True
    outcome = ingest.capture_and_ingest(_settings(), force=True)
    assert outcome.ok is False
    assert outcome.reason == "unavailable"
    assert db.stats()["count"] == 0


# --------------------------------------------------------------- privacy


def test_forget_deletes_a_captured_frame_and_spares_a_user_screenshot(hermes_home, tmp_path, fake_capture):
    shots = tmp_path / "Screenshots"
    own = make_screenshot(shots / "Screenshot 2026-11-05 at 10.00.00.png", ["My own screenshot", "leave me alone"])

    fake_capture["image"] = lambda dest: make_screenshot(Path(dest), ["Captured screen", "delete this one"])
    outcome = ingest.capture_and_ingest(_settings(shots), force=True)
    frame = Path(db.get(outcome.shot_id).path)
    assert frame.is_file()

    removed = db.forget(outcome.shot_id)
    assert removed == 1
    assert not frame.exists(), "the frame Peripheral wrote must go with the row"
    assert db.get(outcome.shot_id) is None
    assert own.is_file(), "the user's own screenshot is never deleted"


def test_forget_all_clears_captured_frames_and_keeps_user_files(hermes_home, tmp_path, fake_capture):
    shots = tmp_path / "Screenshots"
    own = make_screenshot(shots / "Screenshot 2026-11-05 at 10.00.00.png", ["mine", "still here"])
    ingest.scan(_settings(shots), batch=10)

    fake_capture["image"] = lambda dest: make_screenshot(Path(dest), ["captured", "one"])
    ingest.capture_and_ingest(_settings(shots), force=True)
    db.forget(None)

    assert db.stats()["count"] == 0
    assert own.is_file()
    frames_dir = hermes_home / "plugin-data" / "peripheral" / "frames"
    assert not frames_dir.exists() or not list(frames_dir.glob("*.webp"))


# --------------------------------------------------------------- visual index


def test_a_machine_without_ocr_still_builds_a_visual_index(hermes_home, tmp_path, monkeypatch):
    """The README promises this; without the fix, ingest skipped every image."""
    from peripheral import ocr

    monkeypatch.setattr(ocr, "available", lambda: False)
    monkeypatch.setattr(
        ocr, "read_text", lambda path, languages: ocr.OcrResult(text="", lang="", ms=0)
    )

    folder = tmp_path / "Screenshots"
    make_screenshot(folder / "Screenshot 2026-11-05 at 12.00.00.png", ["a chart with no readable words"])

    report = ingest.scan(_settings(folder), batch=10)
    assert report.added == 1, report.line()
    shot = db.recent(1)[0]
    assert shot.text_chars == 0
    assert Path(shot.path).is_file()

    listing = tools.screen_timeline({"since": "1d"})
    assert f"#{shot.id}" in listing
    assert "no text" in listing


def test_data_url_fits_the_budget(hermes_home, tmp_path):
    path = make_screenshot(tmp_path / "big.png", ["a line of text that will not compress to nothing"] * 40, size=(1600, 1000))
    payload = images.data_url(str(path), max_edge=1568, budget_chars=40_000)
    assert payload.startswith("data:image/jpeg;base64,")
    assert len(payload) <= 40_000 + 32


# --------------------------------------------------------------- tools


def test_screen_show_attaches_the_image_when_the_model_can_see(hermes_home, tmp_path, monkeypatch):
    folder = tmp_path / "Screenshots"
    make_screenshot(folder / "Screenshot 2026-11-05 at 09.00.00.png", ["Chart of latency", "p99 240ms"])
    ingest.scan(_settings(folder), batch=10)
    shot_id = db.recent(1)[0].id

    monkeypatch.setattr(tools, "get_settings", lambda: _settings(folder))
    monkeypatch.setattr(tools, "vision_attached", lambda: True)

    result = tools.screen_show({"id": shot_id})
    assert isinstance(result, dict) and result["_multimodal"] is True
    kinds = [part["type"] for part in result["content"]]
    assert kinds == ["text", "image_url"]
    assert result["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert result["meta"]["native_vision"] is True


def test_screen_show_is_text_only_when_the_model_cannot_see(hermes_home, tmp_path, monkeypatch):
    folder = tmp_path / "Screenshots"
    make_screenshot(folder / "Screenshot 2026-11-05 at 09.00.00.png", ["Text only model", "no images please"])
    ingest.scan(_settings(folder), batch=10)
    shot_id = db.recent(1)[0].id

    monkeypatch.setattr(tools, "get_settings", lambda: _settings(folder))
    monkeypatch.setattr(tools, "vision_attached", lambda: False)

    result = tools.screen_show({"id": shot_id})
    assert isinstance(result, str)
    assert "screen #" in result


def test_screen_capture_tool_answers_plainly_with_no_display(hermes_home, monkeypatch):
    monkeypatch.setattr(tools, "get_settings", lambda: _settings())
    monkeypatch.setattr(capture, "available_backends", lambda: [])

    result = tools.screen_capture({})
    assert isinstance(result, str)
    assert "No screen was captured" in result
    assert "status" in result


def test_screen_capture_tool_indexes_and_names_the_screen(hermes_home, fake_capture, monkeypatch):
    fake_capture["image"] = lambda dest: make_screenshot(Path(dest), ["Checkout", "card declined code 402"])
    monkeypatch.setattr(tools, "get_settings", lambda: _settings())
    monkeypatch.setattr(tools, "vision_attached", lambda: False)

    result = tools.screen_capture({"note": "checkout error"})
    assert "screen #" in result
    shot = db.recent(1)[0]
    assert shot.title == "checkout error"
    assert "402" in (db.get(shot.id).ocr_text or "")


def test_screen_forget_tool_deletes_what_it_promises(hermes_home, fake_capture, monkeypatch):
    fake_capture["image"] = lambda dest: make_screenshot(Path(dest), ["forget this screen please", "it contains a private note about the customer"])
    monkeypatch.setattr(tools, "get_settings", lambda: _settings())
    monkeypatch.setattr(tools, "vision_attached", lambda: False)
    tools.screen_capture({})
    shot = db.recent(1)[0]
    frame = Path(shot.path)

    result = tools.screen_forget({"id": shot.id})
    assert "Forgotten" in result
    assert db.get(shot.id) is None
    assert not frame.exists()


def test_watcher_respects_the_hourly_ceiling(hermes_home):
    import time

    watcher = ingest.ScreenWatcher(lambda: _settings())
    watcher._kept = [time.time()] * 5
    assert watcher._allowed(10) is True
    assert watcher._allowed(5) is False


def test_search_survives_ocr_noise(hermes_home, tmp_path):
    """OCR misspells; a one-character-off query must still find the screen."""
    folder = tmp_path / "Screenshots"
    make_screenshot(
        folder / "Screenshot 2026-11-05 at 11.00.00.png",
        ["Payment failed", "Your card was declined", "expired card"],
    )
    ingest.scan(_settings(folder), batch=10)
    assert db.search("card declined"), "the exact query must match"
    assert db.search("declinded"), "a suffix typo falls back to prefix matching"
    assert not db.search("zzzzq"), "an honest miss stays a miss"
