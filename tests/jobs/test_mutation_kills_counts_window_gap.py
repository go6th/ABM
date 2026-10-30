"""Kill tests for the second mutation run (2026-10-30) of ``jobs.counts``, ``jobs.window`` and
``jobs.gap``: the exact text of each invariant, the guards the typed models cannot reach (driven
with duck-typed rows), the zero-month window and a carried posting's place in a gap pass."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from jobs import counts as C
from jobs.errors import InvariantViolation
from jobs.gap import is_remainder
from jobs.models import Classification, PlatformSentence, Posting
from jobs.window import months_before


def _posting(key: str = "k1", **kw: Any) -> Posting:
    base: dict[str, Any] = {
        "posting_key": key,
        "title": "Platform Engineer",
        "url": f"https://jobs.example/{key}",
        "urls": (f"https://jobs.example/{key}",),
        "provider_code": "S-0001",
        "raw_ref": "ledger/1",
        "retrieved_at": "2026-10-20T00:00:00+00:00",
        "window_bucket": "in_window",
    }
    return Posting(**{**base, **kw})


def _message(call) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(InvariantViolation) as caught:
        call()
    return str(caught.value)


# ---- counts -----------------------------------------------------------------------------------


def test_postings_must_be_deduplicated() -> None:
    twice = [_posting("same"), _posting("same")]
    assert _message(lambda: C.compute_counts([], twice, [], {})) == "postings are not deduplicated"


def test_a_read_posting_must_hold_the_sentence_of_every_platform_it_names() -> None:
    bare = _posting(read=True, platforms=("ServiceNow",), full_text_read=True)
    assert _message(lambda: C.compute_counts(["r"], [bare], ["ServiceNow"], {})) == (
        "a read posting names a platform without its sentence"
    )
    sentence = PlatformSentence(platform="ServiceNow", text="t", start=0, end=1, sha256="a")
    ok = _posting(read=True, platforms=("ServiceNow",), sentences=(sentence,), full_text_read=True)
    assert C.compute_counts(["r"], [ok], ["ServiceNow"], {}).distinct.read == 1


def test_the_counts_must_not_grow_down_the_chain() -> None:
    message = _message(lambda: C.compute_counts([], [_posting()], [], {}))
    assert message == "count chain read <= matched <= in_window <= deduped <= pulled"


def test_the_counts_of_a_consistent_set_are_these() -> None:
    sentence = PlatformSentence(platform="A", text="t", start=0, end=1, sha256="a")
    rows = [
        _posting(
            "k1",
            read=True,
            platforms=("A",),
            sentences=(sentence,),
            status="open",
            classification=Classification(label="build", classified_by="extractor", reason="r"),
        ),
        _posting("k2", window_bucket="out_of_window"),
        _posting("k3", window_bucket="undated", title_platforms=("A",)),
    ]
    counts = C.compute_counts(
        ["r1", "r2", "r3", "r4"], rows, ["B", "A"], {"B": "lane_not_in_scope"}
    )
    assert (
        counts.pulled,
        counts.deduped,
        counts.in_window,
        counts.out_of_window,
        counts.undated,
    ) == (
        4,
        3,
        2,
        1,
        1,
    )
    assert counts.distinct.matched == 2 and counts.distinct.read == 1
    assert counts.distinct.by_class["build"] == 1 and counts.distinct.title_only == 1
    assert list(counts.per_platform) == ["A", "B"]


def test_the_status_guard_names_the_base_it_failed_on() -> None:
    odd = [SimpleNamespace(status="bogus")]
    assert (
        _message(lambda: C._status(odd, "matched"))
        == "status counts do not sum to the matched base"
    )
    assert _message(lambda: C._status(odd, "read")) == "status counts do not sum to the read base"
    fine = C._status([SimpleNamespace(status="open"), SimpleNamespace(status="closed")], "matched")
    assert (fine.total, fine.open, fine.closed, fine.unknown_status) == (2, 1, 1, 0)


def test_the_class_guard_refuses_a_class_the_counts_do_not_know() -> None:
    odd = SimpleNamespace(
        read=True,
        hostile=False,
        platforms=(),
        sentences=(),
        posting_class="mystery",
        full_text_read=False,
        full_text_missing=False,
        title_only=False,
        status="open",
    )
    assert _message(lambda: C._block([odd])) == "class counts do not sum to the read base"


# ---- window -----------------------------------------------------------------------------------


def test_zero_months_before_is_the_same_day_and_negative_is_refused() -> None:
    assert months_before(date(2026, 10, 30), 0) == date(2026, 10, 30)
    with pytest.raises(ValueError) as caught:
        months_before(date(2026, 10, 30), -1)
    assert str(caught.value) == "months must not be negative"


def test_months_before_crosses_years_and_clamps_the_day() -> None:
    assert months_before(date(2026, 3, 31), 1) == date(2026, 2, 28)
    assert months_before(date(2026, 1, 15), 1) == date(2025, 12, 15)
    assert months_before(date(2026, 1, 15), 12) == date(2025, 1, 15)
    assert months_before(date(2026, 1, 15), 13) == date(2024, 12, 15)
    assert months_before(date(2026, 6, 30), 36) == date(2023, 6, 30)
    assert months_before(date(2024, 2, 29), 12) == date(2023, 2, 28)


# ---- gap --------------------------------------------------------------------------------------


def test_a_carried_posting_is_never_a_remainder_but_a_new_one_with_the_same_state_is() -> None:
    stuck = {"platforms": ("A",), "escalation": "cap"}
    assert is_remainder(_posting(**stuck)) is True  # a first research has no previous run
    assert is_remainder(_posting(**stuck, new_since_previous=True)) is True
    assert is_remainder(_posting(**stuck, new_since_previous=False)) is False
    assert is_remainder(_posting(**stuck, window_bucket="out_of_window")) is False
    assert is_remainder(_posting(**stuck, hostile=True)) is False
    assert is_remainder(_posting(**stuck, read=True)) is False
