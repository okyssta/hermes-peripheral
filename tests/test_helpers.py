"""Unit tests for the pure helpers: query building, time parsing, redaction, text."""

from __future__ import annotations

import time

from peripheral import db, ocr, render
from peripheral.tools import parse_when


def test_fts_query_neutralises_operators():
    assert db.fts_query('error "quoted" AND OR NEAR(x)') == '"error"* "quoted"*'
    assert db.fts_query("stripe expired") == '"stripe"* "expired"*'
    assert db.fts_query("   ") == ""
    assert db.fts_query("a") == ""
    long = " ".join(f"word{i}" for i in range(30))
    assert len(db.fts_query(long).split()) == 12


def test_fts_query_dedupes_case_insensitively():
    assert db.fts_query("Stripe stripe STRIPE card") == '"Stripe"* "card"*'


def test_parse_when_accepts_the_forms_a_model_writes():
    now = time.time()
    assert parse_when("") is None
    assert parse_when("any") is None
    assert parse_when("bogus-value") is None
    two_hours = parse_when("2h")
    assert two_hours is not None and abs((now - two_hours) - 7200) < 30
    yesterday = parse_when("yesterday")
    assert yesterday is not None and yesterday < now
    assert parse_when("2026-11-04") is not None
    end_of_day = parse_when("2026-11-04", end=True)
    assert end_of_day - parse_when("2026-11-04") == 86399


def test_redaction_removes_secrets_and_keeps_sentences():
    sample = (
        "Charge failed for card 4242 4242 4242 4242\n"
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456\n"
        'api_key = "sk-live-abcdefghijklmnopqrstuvwx"\n'
        "Contact sam@example.com about the invoice"
    )
    cleaned = ocr.redact(sample)
    assert "4242 4242 4242 4242" not in cleaned
    assert "abcdefghijklmnopqrstuvwxyz123456" not in cleaned
    assert "sk-live" not in cleaned
    assert "sam@example.com" in cleaned
    assert "Contact" in cleaned


def test_clean_text_drops_noise_and_repeats():
    raw = "\n".join(
        [
            "  Stripe   dashboard ",
            "",
            "|",
            "-",
            "Payment failed",
            "Payment failed",
            "   ",
            "Your card was declined",
        ]
    )
    cleaned = ocr.clean_text(raw)
    assert cleaned.splitlines() == ["Stripe dashboard", "Payment failed", "Your card was declined"]


def test_clean_text_honours_the_character_cap():
    cleaned = ocr.clean_text("\n".join(f"line {index} of text" for index in range(5000)), max_chars=500)
    assert len(cleaned) == 500


def test_guess_title_picks_the_first_real_line():
    assert ocr.guess_title("|\n--\nStripe dashboard\nPayment failed") == "Stripe dashboard"
    assert ocr.guess_title("") == ""


def test_human_age_reads_like_a_person():
    now = time.time()
    assert db.human_age(now - 10, now) == "just now"
    assert db.human_age(now - 600, now) == "10m ago"
    assert db.human_age(now - 7200, now) == "2h ago"
    assert db.human_age(now - 86400 * 3, now) == "3d ago"


def test_render_results_says_what_to_do_when_empty():
    text = render.results("nothing here", [])
    assert "No screen matched" in text
    assert "distinctive word" in text
