"""Pure-logic tests (spec 9.3): window, keys, dedupe, matching, sentences, rubric, counts."""

from __future__ import annotations

from datetime import date

import pytest

from jobs.classify import classify_rules
from jobs.dedupe import dedupe, dedupe_counted
from jobs.match import match_platforms
from jobs.models import ClassificationLabel, RawPosting
from jobs.normalise import canonical_url, normalise_status, posting_key, stored_text, strip_pii
from jobs.sentences import extract_platform_sentences, sentence_spans
from jobs.window import classify_window, window_start
from tests.jobs.conftest import frozen_config


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


def test_dedupe_merges_on_a_shared_id_and_keeps_every_url_and_ref() -> None:
    """Hand-written expectations (J8): same id, different URLs is one posting with both URLs."""
    one = raw("j9", "https://careers.example/a", call_ref="ref-1", description="short")
    two = raw("j9", "https://jobs.example/b?utm_source=x", call_ref="ref-2", description="longer")
    result = dedupe_counted([one, two])
    assert len(result.rows) == 1
    merged = result.rows[0]
    assert merged.source_id == "j9" and merged.url == "https://jobs.example/b?utm_source=x"
    assert merged.other_urls == ("https://careers.example/a",)
    assert result.call_refs == {"id:j9": ("ref-1", "ref-2")}


def test_dedupe_keeps_id_less_records_apart_and_ignores_unusable_other_urls() -> None:
    a = raw(None, "https://careers.example/a", other_urls=("not a url",), call_ref="r-a")
    b = raw(None, "https://careers.example/b", other_urls=("",), call_ref="r-b")
    result = dedupe_counted([a, b])
    assert [r.url for r in result.rows] == [
        "https://careers.example/a",
        "https://careers.example/b",
    ]
    assert all(r.other_urls == () for r in result.rows)
    assert result.call_refs == {
        "url:https://careers.example/a": ("r-a",),
        "url:https://careers.example/b": ("r-b",),
    }


def test_dedupe_takes_id_and_status_from_any_member_and_keys_by_the_smallest_url() -> None:
    # the kept record (complete, longer text) has no id and no status; the feed record has both
    board = raw(
        None,
        "https://careers.example/z",
        description="a much longer complete description",
        live_board_present=True,
        call_ref="r-board",
    )
    feed = raw("j5", "https://careers.example/z", description="", status="closed", call_ref="r-f")
    result = dedupe_counted([board, feed])
    merged = result.rows[0]
    assert merged.source_id == "j5" and merged.status == "closed" and merged.live_board_present
    assert merged.description == "a much longer complete description"
    assert result.call_refs == {"id:j5": ("r-board", "r-f")}
    # id-less, linked through other_urls: the key is the smallest canonical URL
    x = raw(None, "https://careers.example/y", other_urls=("https://careers.example/b",))
    y = raw(None, "https://careers.example/b", call_ref="r-y")
    linked = dedupe_counted([x, y])
    assert len(linked.rows) == 1
    assert list(linked.call_refs) == ["url:https://careers.example/b"]


def test_group_key_ignores_unusable_other_urls() -> None:
    from jobs.dedupe import group_key

    row = raw(None, "https://careers.example/q", other_urls=("not a url",))
    assert group_key(row) == "url:https://careers.example/q"


def test_every_rejected_record_is_counted() -> None:
    bad = [raw(f"b{i}", "not a url") for i in range(3)]
    assert dedupe_counted([*bad, raw("ok")]).rejected == 3


def test_status_only_from_explicit_values() -> None:
    assert normalise_status("Active") == "open" and normalise_status("filled") == "closed"
    # the feed's contract: a null status is an open posting; a word outside the vocabulary is
    # never guessed
    assert normalise_status(None) == "open" and normalise_status("maybe") == "unknown"


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


def test_punctuation_only_chunks_are_not_sentences() -> None:
    """Hand-written offsets: a run of dots or a line of marks is never a span (J3)."""
    assert sentence_spans("Hello. ... World.") == [(0, 6), (11, 17)]
    assert sentence_spans("Use SAP!\n?!\nThen stop.") == [(0, 8), (12, 22)]
    assert sentence_spans("  \n ... \n") == []


def test_every_rubric_rule_has_a_passing_positive_and_negative_example() -> None:
    cfg = frozen_config()
    for rule in cfg.rules:
        verdict = classify_rules(rule.positive, cfg.rules)
        assert (verdict.label, verdict.reason) == (rule.label, "rule_match"), rule.rule_id
        assert verdict.rule_id is not None  # the firing rule is recorded with the class
        assert classify_rules(rule.negative, cfg.rules).label != rule.label, rule.rule_id


def test_a_single_rule_hit_records_its_rule_id() -> None:
    from jobs.config import Rule

    rules = (
        Rule("r-build", "build", ("develop",), "x", "y"),
        Rule("r-build-2", "build", ("code",), "x", "y"),
        Rule("r-user", "user", ("uses",), "x", "y"),
    )
    hit = classify_rules("You will develop and code.", rules)
    assert (hit.label, hit.reason, hit.rule_id) == ("build", "rule_match", "r-build")
    both = classify_rules("You will develop what the team uses.", rules)
    assert (both.label, both.reason, both.rule_id) == (None, "ambiguous_rules", None)
    none = classify_rules("Nothing here.", rules)
    assert (none.label, none.reason, none.rule_id) == (None, "no_rule_match", None)


def test_conflicting_cues_are_ambiguous_and_cues_are_whole_words() -> None:
    cfg = frozen_config()
    assert classify_rules("The developer and the administrator", cfg.rules).reason == (
        "ambiguous_rules"
    )
    assert classify_rules("Developers wanted", cfg.rules).reason == "no_rule_match"


def test_class_vocabulary() -> None:
    assert ClassificationLabel("unclear") is ClassificationLabel.UNCLEAR
    with pytest.raises(ValueError):
        ClassificationLabel("manager")


@pytest.mark.parametrize(
    ("verified", "expected"),
    [
        ("www.quidelortho.com", "quidelortho.com"),
        ("Careers.Example.org", "careers.example.org"),
        ("", None),
        (None, None),
    ],
)
def test_board_search_uses_the_bare_careers_host(
    verified: str | None, expected: str | None
) -> None:
    """A verified careers host of "www.<domain>" must not reach the search filter with www."""
    from account_intelligence.connectors.models import WebSearchArgs
    from jobs.adapters import ConnectorPostingSource

    source = ConnectorPostingSource(object(), verified)
    assert source.careers_host == expected
    if expected:
        WebSearchArgs(queries=("role jobs",), include_domains=(expected,))
