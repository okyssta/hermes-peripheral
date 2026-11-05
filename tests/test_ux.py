"""The polish layer: what a person sees when they use the plugin, not what it stores.

Every case here was a real defect or a dead end found by walking the surfaces the
way a user does — first run, a paused plugin, a background-capture switch, a query
with one word that is not on the screen, a typo, a time word nobody parsed.
"""

from __future__ import annotations

import time

from conftest import make_screenshot
from peripheral import tools


def _cmd(host, text: str = "") -> str:
    return host.commands["peripheral"](text)


# --------------------------------------------------------------- first run


def test_first_run_status_counts_the_screenshots_it_can_index(plugin_host, ocr_ready):
    """An empty install must say how close the user is to a working memory."""
    host, screens = plugin_host
    for index in range(3):
        make_screenshot(screens / f"shot{index}.png", [f"Invoice INV-{index} overdue"])

    text = _cmd(host)
    assert "3 images already in" in text
    assert "/peripheral scan" in text
    assert "nothing indexed" in text.lower()


def test_first_run_status_explains_a_machine_with_nothing_to_index(plugin_host, monkeypatch):
    host, screens = plugin_host
    monkeypatch.setattr("peripheral.capture.available_backends", lambda: [])

    text = _cmd(host)
    assert "no screenshots found" in text
    assert "watch_dirs" in text


def test_status_after_indexing_drops_the_first_run_hint(plugin_host, ocr_ready):
    host, screens = plugin_host
    make_screenshot(
        screens / "one.png",
        ["Sentry — 3 new issues", "TypeError in checkout.py line 88", "First seen 12 minutes ago", "Assigned to sam"],
    )
    host.tools["screen_index"]({})

    text = _cmd(host)
    assert "1 screens" in text or "1 screen indexed" in text or "screens indexed" in text
    assert "Nothing indexed yet" not in text


# ------------------------------------------------------- pause and watch


def test_pause_takes_effect_on_the_very_next_tool_call(plugin_host):
    """The settings cache is two seconds; the user's next sentence is not."""
    host, _ = plugin_host
    _cmd(host, "pause")

    captured = host.tools["screen_capture"]({})
    assert "paused" in captured.lower(), captured

    _cmd(host, "resume")
    resumed = host.tools["screen_capture"]({})
    assert "paused" not in resumed.lower(), resumed


def test_watch_on_starts_the_capture_thread_without_a_restart(plugin_host):
    """`/peripheral watch on` used to store the setting and start nothing."""
    import peripheral

    host, _ = plugin_host
    answer = _cmd(host, "watch on")
    assert "background capture on" in answer, answer
    assert peripheral._WATCHER is not None and peripheral._WATCHER.running

    _cmd(host, "watch off")
    assert not (peripheral._WATCHER and peripheral._WATCHER.running)


def test_watch_on_is_visible_in_the_next_status(plugin_host):
    host, _ = plugin_host
    _cmd(host, "watch on")

    status = _cmd(host)
    assert "background capture: every" in status, status
    _cmd(host, "watch off")
    assert "background capture: off" in _cmd(host)


# --------------------------------------------------------------- searching


def test_a_query_with_one_absent_word_still_finds_the_screen(plugin_host, ocr_ready):
    """"card error" must reach "The card has expired" — the screen they meant."""
    host, screens = plugin_host
    make_screenshot(screens / "card.png", ["Checkout — payment failed", "The card has expired. Use a different card."])
    make_screenshot(screens / "pricing.png", ["Pricing — Starter $19"])
    host.tools["screen_index"]({})

    text = host.tools["screen_recall"]({"query": "card error"})
    assert "No screen held every word" in text, text
    assert "expired" in text.lower()
    assert "Pricing" not in text


def test_a_query_that_matches_every_word_keeps_the_strict_answer(plugin_host, ocr_ready):
    host, screens = plugin_host
    make_screenshot(screens / "card.png", ["Checkout — payment failed", "The card has expired."])
    host.tools["screen_index"]({})

    text = host.tools["screen_recall"]({"query": "card expired"})
    assert "No screen held every word" not in text
    assert "1 screen for" in text


def test_an_unparseable_time_bound_is_reported(plugin_host, ocr_ready):
    host, screens = plugin_host
    make_screenshot(screens / "card.png", ["Checkout — payment failed", "The card has expired."])
    host.tools["screen_index"]({})

    text = host.tools["screen_recall"]({"query": "card", "since": "somewhen last whenever"})
    assert "was not understood" in text, text


def test_a_typo_of_a_subcommand_gets_a_suggestion(plugin_host, ocr_ready):
    host, screens = plugin_host
    make_screenshot(screens / "card.png", ["Checkout — payment failed"])
    host.tools["screen_index"]({})

    text = _cmd(host, "scna")
    assert "Did you mean /peripheral scan?" in text, text


def test_a_real_word_on_a_screen_is_never_mistaken_for_a_typo(plugin_host, ocr_ready):
    """"stat" can be a real word on a dashboard; a hit must win over a suggestion."""
    host, screens = plugin_host
    make_screenshot(
        screens / "stats.png",
        ["Server stat page — 200 OK", "uptime 14 days", "requests 1.2M", "errors 0.02%"],
    )
    host.tools["screen_index"]({})

    text = _cmd(host, "stat")
    assert "Did you mean" not in text, text
    assert "200 OK" in text


# ------------------------------------------------------------------ time


def test_time_words_people_write_are_understood():
    now = time.time()
    tomorrow = tools.parse_when("tomorrow")
    assert tomorrow is not None and tomorrow > now

    hour_ago = tools.parse_when("an hour ago")
    assert hour_ago is not None and abs((now - hour_ago) - 3600) < 60

    week = tools.parse_when("this week")
    assert week is not None and time.localtime(week).tm_wday == 0

    monday = tools.parse_when("monday")
    assert monday is not None and time.localtime(monday).tm_wday == 0 and monday <= now


def test_unknown_time_words_still_return_none():
    assert tools.parse_when("bogus-value") is None
    assert tools.parse_when("whenever i felt like it") is None
