"""Pure-logic tests (spec 9.3): window, keys, dedupe, matching, sentences, rubric, counts."""

from __future__ import annotations

from datetime import date

import pytest

from jobs.classify import classify_rules
from jobs.config import load_jobs_config
from jobs.dedupe import dedupe, dedupe_counted
from jobs.match import match_platforms
from jobs.models import ClassificationLabel, RawPosting
from jobs.normalise import canonical_url, normalise_status, posting_key, stored_text, strip_pii
from jobs.sentences import extract_platform_sentences, sentence_spans
from jobs.window import classify_window, window_start


def raw(key: str | None, url: str = "", **kw: object) -> RawPosting:
    base: dict[str, object] = dict(
        source_id=key,
        title="Role",
        description="Synthetic text.",
        url=url or f"https://example.org/{key}",
        provider_code="p_signals",
        raw_ref=f"raw:{key}",
        retrieved_at="2026-01-01T00:00:00Z",
        text_complete=True,
    )
    base.update(kw)
    return RawPosting(**base)  # type: ignore[arg-type]


def test_calendar_window_boundaries_to_the_day() -> None:
    assert window_start(date(2026, 10, 1)) == date(2025, 10, 1)
    assert window_start(date(2024, 2, 29)) == date(2023, 2, 28)  # month end clamps
    assert window_start(date(2026, 3, 31)) == date(2025, 3, 31)
    assert classify_window(date(2025, 10, 1), date(2026, 10, 1)) == "in_window"
    assert classify_window(date(2025, 9, 30), date(2026, 10, 1)) == "out_of_window"
    assert classify_window(date(2026, 10, 2), date(2026, 10, 1)) == "out_of_window"  # future
    assert classify_window(None, date(2026, 10, 1)) == "undated"


def test_url_canonicalisation_and_keys() -> None:
    assert canonical_url("https://EXAMPLE.org/a/?utm_source=x#f") == "https://example.org/a"
    assert canonical_url("ftp://example.org/a") == "" and canonical_url("not a url") == ""
    assert posting_key(raw("a")) == "id:a"
    assert posting_key(raw(None, "https://example.org/x")) == "url:https://example.org/x"
    assert posting_key(raw(None, "bad"), with_strength=True)[1] is True  # weak fingerprint


def test_dedupe_merges_board_hit_and_feed_record_and_counts_rejects() -> None:
    board = raw(None, "https://careers.example/j1", live_board_present=True, text_complete=False)
    feed = raw("j1", "https://careers.example/j1", status="closed")
    other = raw("j2")
    broken = raw("j3", "not a url")
    result = dedupe_counted([board, feed, other, broken, feed])
    assert result.rejected == 1
    assert [r.source_id for r in result.rows] == ["j1", "j2"]
    merged = result.rows[0]
    assert merged.live_board_present and merged.text_complete is True
    assert dedupe(dedupe([board, feed])) == dedupe([feed, board])  # idempotent, order free


def test_dedupe_never_merges_on_title() -> None:
    a = raw("a", title="Salesforce Developer")
    b = raw("b", title="Salesforce Developer")
    assert len(dedupe([a, b])) == 2


def test_status_only_from_explicit_values() -> None:
    assert normalise_status("Active") == "open" and normalise_status("filled") == "closed"
    assert normalise_status(None) == normalise_status("maybe") == "unknown"


def test_stored_text_strips_markup_and_keeps_lines_and_pii_is_derived_only() -> None:
    text = stored_text("<p>One &amp; two.</p><script>x</script><li>Three\x00</li>")
    assert text == "One & two.\nx\nThree"
    assert strip_pii("mail a.b@example.com or +1 555 010 9999") == "mail [redacted] or [redacted]"


def test_whole_word_lexicon_matching() -> None:
    lexicon = {"SAP": (), "Salesforce": ("Service Cloud",)}
    m = match_platforms("Role", "We use Service Cloud; sapphire is not a platform.", lexicon)
    assert m.description == ("Salesforce",)  # alias matched; SAP never matches inside a word
    m = match_platforms("SAP Lead", "No platform here.", lexicon)
    assert (m.description, m.title_only) == ((), ("SAP",))


def test_sentence_spans_are_verbatim_with_offsets() -> None:
    text = "Use Salesforce.com daily! Contact a.b@example.com now.\nSAP basis"
    spans = sentence_spans(text)
    assert [text[a:b] for a, b in spans] == [
        "Use Salesforce.com daily!",
        "Contact a.b@example.com now.",
        "SAP basis",
    ]
    found = extract_platform_sentences(text, ("SAP", "Salesforce"), {"SAP": (), "Salesforce": ()})
    assert [(s.platform, text[s.start : s.end]) for s in found] == [
        ("SAP", "SAP basis"),
        ("Salesforce", "Use Salesforce.com daily!"),
    ]


def test_every_rubric_rule_has_a_passing_positive_and_negative_example() -> None:
    cfg = load_jobs_config()
    for rule in cfg.rules:
        assert classify_rules(rule.positive, cfg.rules).label == rule.label, rule.rule_id
        assert classify_rules(rule.negative, cfg.rules).label != rule.label, rule.rule_id


def test_conflicting_cues_are_ambiguous_and_cues_are_whole_words() -> None:
    cfg = load_jobs_config()
    assert classify_rules("The developer and the administrator", cfg.rules).reason == (
        "ambiguous_rules"
    )
    assert classify_rules("Developers wanted", cfg.rules).reason == "no_rule_match"


def test_class_vocabulary() -> None:
    assert ClassificationLabel("unclear") is ClassificationLabel.UNCLEAR
    with pytest.raises(ValueError):
        ClassificationLabel("manager")
