"""The gap-pass remainder rule and the progress test (spec 7), pure, on hand-written cases."""

from __future__ import annotations

from typing import Any

from jobs.gap import gap_remainder, is_remainder, progress
from jobs.models import Classification, Posting


def _p(key: str, **kw: Any) -> Posting:
    base: dict[str, Any] = dict(
        posting_key=key,
        title="Role",
        url=f"https://careers.example/{key}",
        urls=(f"https://careers.example/{key}",),
        provider_code="p_signals",
        raw_ref="synthetic/raw",
        retrieved_at="2026-10-01T00:00:00+00:00",
        window_bucket="in_window",
        platforms=("SAP",),
    )
    base.update(kw)
    return Posting(**base)


def _why(reason: str, by: str = "none", label: str = "unclassified") -> Classification:
    return Classification(label=label, classified_by=by, reason=reason)  # type: ignore[arg-type]


def test_remainder_is_exactly_the_retryable_postings() -> None:
    cases = {
        "cap": (_p("a", read=True, classification=_why("cap")), True),
        "error": (_p("b", classification=_why("error")), True),
        "unescalated": (_p("c", escalation="cap", full_text_missing=True), True),
        "read_spent": (_p("d", escalation="failed", full_text_missing=True), False),
        "disagreement": (
            _p("e", read=True, classification=_why("disagreement", "extractor")),
            False,
        ),
        "unclear": (_p("f", read=True, classification=_why("extractor_unclear")), False),
        "hostile": (_p("g", hostile=True, classification=_why("error")), False),
        "out_of_window": (
            _p("h", window_bucket="out_of_window", classification=_why("error")),
            False,
        ),
        "out_of_window_cap": (_p("i", window_bucket="out_of_window", escalation="cap"), False),
        "undated_error": (_p("j", window_bucket="undated", classification=_why("error")), True),
        "unmatched_cap": (_p("k", platforms=(), escalation="cap"), False),
        "classified": (
            _p("l", read=True, classification=_why("rule_match", "rule", "build")),
            False,
        ),
    }
    for name, (posting, want) in cases.items():
        assert is_remainder(posting) is want, name
    assert gap_remainder([c[0] for c in reversed(cases.values())]) == ("a", "b", "c", "j")


def test_progress_counts_a_new_read_or_a_new_class_and_nothing_else() -> None:
    unread = _p("a", escalation="cap")
    read_open = _p("a", read=True, classification=_why("cap"))
    read_done = _p("a", read=True, classification=_why("rule_match", "rule", "build"))
    still = _p("a", read=True, classification=_why("error"))
    assert progress([unread], [read_open])  # one more read, no new class
    assert progress([read_open], [read_done])  # same reads, one more class
    assert not progress([read_open], [still])  # a different failure reason is not progress
    assert not progress([read_done], [read_done])
    # an unread posting with a class (never produced, but the score must not count it)
    phantom = _p("a", read=False, classification=_why("rule_match", "rule", "build"))
    assert not progress([unread], [phantom])
    two_unread = [_p("a", escalation="cap"), _p("b", escalation="cap")]
    assert progress(two_unread, [_p("a", escalation="cap"), still])  # 0 reads to 1 read
