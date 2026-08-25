"""Tests for ``scripts/pii.py`` — pseudonymization before text leaves the machine.

Two properties matter and they pull against each other:

* **Nothing personal escapes.** A UID, phone number, e-mail, URL, or card
  number that survives scrubbing is a privacy failure, and this is the last
  gate before a paid API call.
* **Nothing analytical is destroyed.** Dates, prices, and diamond counts are
  the signal the extractor reads. Over-scrubbing is a silent quality
  regression that would look like "the model got worse".

Every test below pins one side or the other.
"""

from __future__ import annotations

import json

import pytest
from supportkit.pii import Scrubber, scrub_stats, scrub_text

# --------------------------------------------------------------------------
# Nothing personal escapes
# --------------------------------------------------------------------------

def test_emails_are_replaced():
    out = scrub_text("write to ivan.petrov+support@mail.example.com please")
    assert "ivan.petrov" not in out
    assert "<EMAIL_1>" in out


def test_urls_are_replaced_whole_not_shredded():
    """A profile URL contains digits and an @-ish shape; matching numbers first
    would leave the domain and path exposed."""
    out = scrub_text("proof: https://imo.example/u/8823174?ref=abc")
    assert "imo.example" not in out
    assert "8823174" not in out
    assert out.count("<URL_1>") == 1


def test_phone_numbers_in_several_shapes_are_replaced():
    for raw in ["+998 90 123 45 67", "555-123-4567", "(212) 555-0199", "+1 (415) 555-2671"]:
        out = scrub_text(f"call me on {raw} today")
        assert not any(ch.isdigit() for ch in out.replace("<PHONE_1>", "")), out


def test_long_identifier_runs_are_replaced():
    out = scrub_text("my account 8823174 is blocked")
    assert "8823174" not in out
    assert "<ID_1>" in out


def test_card_like_runs_are_replaced():
    out = scrub_text("charged card 4111 1111 1111 1111 twice")
    assert "4111" not in out
    assert "<CARD_1>" in out


def test_ip_addresses_and_handles_are_replaced():
    out = scrub_text("from 192.168.10.24 by @ivan_seller")
    assert "192.168" not in out and "ivan_seller" not in out
    assert "<IP_1>" in out and "<HANDLE_1>" in out


def test_none_and_empty_input_are_safe():
    assert scrub_text(None) == ""
    assert scrub_text("") == ""
    assert scrub_text("   ") == "   "


# --------------------------------------------------------------------------
# Nothing analytical is destroyed
# --------------------------------------------------------------------------

def test_dates_survive_scrubbing():
    """Dates look exactly like phone numbers to a loose pattern, and the
    evidence-quality score counts timestamps. Losing them is a real regression."""
    for date in ["03.04.2026", "2026-04-03", "3/4/2026"]:
        assert date in scrub_text(f"this happened on {date} and again later")


def test_small_numbers_and_money_amounts_survive():
    text = "I paid 25 diamonds and 1 234.56 som for SVIP level 3"
    assert scrub_text(text) == text


def test_ordinary_prose_is_returned_unchanged():
    text = "my group disappeared after the update and nobody explained why"
    assert scrub_text(text) == text


# --------------------------------------------------------------------------
# Pseudonym stability — the reason this isn't plain redaction
# --------------------------------------------------------------------------

def test_the_same_value_maps_to_the_same_placeholder():
    """'88231 was banned but 88231 broke no rules' must keep the fact that both
    mentions are one account — information [REDACTED] would destroy."""
    out = scrub_text("account 8823174 was banned but 8823174 broke no rules")
    assert out.count("<ID_1>") == 2
    assert "<ID_2>" not in out


def test_different_values_get_different_placeholders():
    out = scrub_text("compare 8823174 with 9911223")
    assert "<ID_1>" in out and "<ID_2>" in out


def test_mapping_is_stable_across_tickets_within_one_scrubber():
    scrubber = Scrubber()
    first = scrubber.scrub("user 8823174 reported a scam")
    second = scrubber.scrub("the same user 8823174 wrote again")
    assert "<ID_1>" in first and "<ID_1>" in second


def test_a_fresh_scrubber_starts_numbering_over():
    """Per-ticket scrubbers make tickets independently unlinkable."""
    assert "<ID_1>" in Scrubber().scrub("user 8823174")
    assert "<ID_1>" in Scrubber().scrub("user 9911223")


def test_case_differences_do_not_create_a_second_pseudonym():
    out = scrub_text("write to Ivan@Mail.com or ivan@mail.com")
    assert out.count("<EMAIL_1>") == 2


# --------------------------------------------------------------------------
# Auditing
# --------------------------------------------------------------------------

def test_scrub_stats_counts_kinds_without_retaining_values():
    stats = scrub_stats("uid 8823174, mail a@b.co, link https://x.example/y")
    assert stats == {"EMAIL": 1, "ID": 1, "URL": 1}


def test_summary_reports_distinct_values_per_kind():
    scrubber = Scrubber()
    scrubber.scrub("8823174 and 8823174 and 9911223")
    assert scrubber.summary()["ID"] == 2  # two distinct values, three mentions


def test_mixed_content_is_fully_covered():
    text = (
        "On 03.04.2026 my account 8823174 was banned. "
        "Proof: https://imo.example/case/55 — mail me at ivan@mail.example.com "
        "or call +998 90 123 45 67. I lost 25 diamonds."
    )
    out = scrub_text(text)
    for secret in ["8823174", "imo.example", "ivan@mail.example.com", "998 90 123"]:
        assert secret not in out
    assert "03.04.2026" in out  # date kept
    assert "25 diamonds" in out  # amount kept


# --------------------------------------------------------------------------
# The chokepoint: every remote DeepSeek call, not just the extractor
# --------------------------------------------------------------------------
#
# The first version of this work scrubbed inside llm_extract_rich_tickets only.
# That left nine other scripts sending raw ticket text to DeepSeek — including
# llm_open_taxonomy, the CANONICAL Stage 7 tagger. These tests pin the fix at
# the one function they all funnel through.


# The chokepoint contract. These four ship WITH the client, always — a scrubber
# nobody is forced to call is decoration.


def _capture_request(monkeypatch):
    """Intercept the outbound request and stop before anything is sent."""
    from supportkit import deepseek_client

    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        raise RuntimeError("stop here — we only care about what was about to be sent")

    monkeypatch.setattr(deepseek_client.urllib.request, "urlopen", fake_urlopen)
    return deepseek_client, captured


def test_every_deepseek_call_is_scrubbed_by_default(monkeypatch):
    """Property 1. Scrubbing in ONE caller is what left nine scripts leaking."""
    client, captured = _capture_request(monkeypatch)
    with pytest.raises(RuntimeError):
        client.call_deepseek(
            "key", "deepseek-chat",
            "ОБРАЩЕНИЕ:\nмой аккаунт 8823174, почта ivan@example.com",
            retries=1,
        )
    sent = captured["body"]["messages"][-1]["content"]
    assert "8823174" not in sent
    assert "ivan@example.com" not in sent
    assert "<ID_1>" in sent and "<EMAIL_1>" in sent


def test_the_system_prompt_is_never_rewritten(monkeypatch):
    """Property 2. System prompts are project-authored rules; rewriting one that
    happens to contain a long number would change model behaviour."""
    client, captured = _capture_request(monkeypatch)
    with pytest.raises(RuntimeError):
        client.call_deepseek(
            "key", "deepseek-chat", "обращение 8823174",
            system="Return at most 123456 items.", retries=1,
        )
    assert captured["body"]["messages"][0]["content"] == "Return at most 123456 items."


def test_a_caller_can_opt_out_when_placeholders_would_corrupt_its_output(monkeypatch):
    """Property 3. A translation call returns a rewritten copy of its input; a
    placeholder there would be baked into the shipped strings."""
    client, captured = _capture_request(monkeypatch)
    with pytest.raises(RuntimeError):
        client.call_deepseek("key", "m", "translate 8823174", retries=1, scrub_pii=False)
    assert "8823174" in captured["body"]["messages"][-1]["content"]


def test_the_environment_can_disable_scrubbing_but_never_force_it_on(monkeypatch):
    """Property 4. Asymmetric on purpose: a caller that opted out did so because
    placeholders break its output, and an env var must not undo that."""
    from supportkit.deepseek_client import PII_SCRUB_ENV, pii_scrub_enabled

    monkeypatch.delenv(PII_SCRUB_ENV, raising=False)
    assert pii_scrub_enabled(True) is True

    monkeypatch.setenv(PII_SCRUB_ENV, "0")
    assert pii_scrub_enabled(True) is False

    monkeypatch.setenv(PII_SCRUB_ENV, "1")
    assert pii_scrub_enabled(False) is False  # the caller's opt-out still wins


def test_no_api_key_is_an_error_before_anything_is_sent(monkeypatch):
    """Nothing in this repo spends money implicitly."""
    from supportkit.deepseek_client import resolve_api_key

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="No API key"):
        resolve_api_key(None)
