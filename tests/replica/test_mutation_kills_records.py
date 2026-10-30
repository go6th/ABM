"""Kill tests for the mutation run of ``account_intelligence.archive_records`` (2026-10-30): every
record builder is pinned field by field (names, defaults, the guarded set), the JSON bodies byte
for byte. Hermetic, synthetic addresses and text."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

import pytest

from account_intelligence import archive_records as AR

T0 = datetime(2026, 10, 27, 9, tzinfo=UTC)


def _refuse_bad(text: str) -> tuple[str, bool]:
    return text, "BAD" in text


# ---- helpers ----------------------------------------------------------------------------------


def test_day_of_reads_dates_datetimes_and_iso_text() -> None:
    assert AR.day_of(date(2026, 10, 30)) == "2026-10-30"
    assert AR.day_of(datetime(2026, 10, 30, 12, 30, tzinfo=UTC)) == "2026-10-30"
    assert AR.day_of("2026-10-30T08:00:00Z") == "2026-10-30"
    assert AR.day_of("  2026-10-30  ") == "2026-10-30"
    assert AR.day_of("2026-10-3") is None
    assert AR.day_of("") is None and AR.day_of(None) is None and AR.day_of(20261030) is None


def test_publisher_of_is_the_host_without_www_and_trailing_dot() -> None:
    assert AR.publisher_of("https://www.press.example./a") == "press.example"
    assert AR.publisher_of("https://WWW.Press.Example/a") == "press.example"
    assert AR.publisher_of("https://press.example/a") == "press.example"
    assert AR.publisher_of("https://wwwx.example/a") == "wwwx.example"
    assert AR.publisher_of("  https://news.example/a ") == "news.example"


def test_redact_contact_hides_addresses_and_numbers_but_not_dates() -> None:
    out = AR.redact_contact("Mail hr@jobs.example or +1 555 010 7788 on 2026-10-30 and 30.10.2026")
    assert out == "Mail [redacted] or [redacted] on 2026-10-30 and 30.10.2026"


def test_the_bodies_are_stable_json_with_sorted_keys_and_real_characters() -> None:
    record = {"b": "é", "a": 1}
    assert AR.snapshot_bytes(record) == '{"a": 1, "b": "é"}'.encode()
    assert AR.identity_bytes({"b": "é", "a": 1, "captured_at": "x", "withheld": ["a"]}) == (
        '{"a": 1, "b": "é"}'.encode()
    )
    for key in sorted(AR.NOT_IDENTITY):
        assert key not in json.loads(AR.identity_bytes({key: 1, "kept": 2}))
    assert json.loads(AR.identity_bytes({"first_seen": "d", "last_seen": "d", "kept": 2})) == {
        "kept": 2
    }


@pytest.fixture
def registered_guard(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    monkeypatch.setattr(AR, "_page_guard", [])
    return AR._page_guard  # type: ignore[no-any-return]


def test_the_default_guard_is_the_registered_page_guard_else_the_denylist(
    registered_guard: list[Any],
) -> None:
    assert AR.default_guard("plain text") == ("plain text", False)
    assert AR.default_guard("12 credits used on this") == ("12 credits used on this", True)
    seen: list[str] = []

    def page_guard(text: str) -> tuple[str, bool]:
        seen.append(text)
        return text.upper(), text == "refuse me"

    AR.use_default_guard(page_guard)
    assert AR.default_guard("shown") == ("SHOWN", False)
    assert AR.default_guard("refuse me") == ("REFUSE ME", True)
    assert seen == ["shown", "refuse me"]
    # the registered guard replaces the denylist, it does not add to it
    assert AR.default_guard("12 credits used on this")[1] is False
    AR.use_default_guard(lambda text: ("second", False))
    assert AR.default_guard("x") == ("second", False)  # one guard at a time
    assert len(registered_guard) == 1


# ---- the record builders ----------------------------------------------------------------------


def test_a_page_record_has_these_fields_and_defaults() -> None:
    record = AR.page_record(
        "https://www.Press.example/a?token=1&q=2",
        "Body text, mail hr@jobs.example.",
        title="Headline",
        captured_at=T0,
        published_date="2026-09-30T08:00:00Z",
    )
    assert record == {
        "schema": "page-archive/1",
        "url": "https://www.Press.example/a?q=2",
        "kind": "page",
        "title": "Headline",
        "publisher": "press.example",
        "published_date": "2026-09-30",
        "text": "Body text, mail [redacted].",
        "captured_at": T0.isoformat(),
        "source": "6th agents",
    }
    other = AR.page_record("https://a.example/x", "t", title=None, captured_at=T0, kind="release")
    assert (other["kind"], other["title"], other["published_date"]) == ("release", "", None)


def test_a_page_text_is_cut_at_the_longest_body() -> None:
    record = AR.page_record(
        "https://a.example/x", "a" * (AR.MAX_TEXT + 5), title="T", captured_at=T0
    )
    assert len(record["text"]) == AR.MAX_TEXT
    news = AR.news_record(
        "https://a.example/x",
        title="T",
        summary="S",
        article_text="b" * (AR.MAX_TEXT + 5),
        category=None,
        published_date=None,
        captured_at=T0,
    )
    assert len(news["article_text"]) == AR.MAX_TEXT


def test_every_free_text_field_of_a_page_goes_through_the_guard() -> None:
    record = AR.page_record(
        "https://a.example/x", "BAD text", title="BAD title", captured_at=T0, guard=_refuse_bad
    )
    assert record["title"] == "" and record["text"] == ""
    assert record["withheld"] == ["text", "title"]
    assert record["url"] == "https://a.example/x" and record["publisher"] == "a.example"


def test_a_news_record_has_these_fields_and_defaults() -> None:
    record = AR.news_record(
        "https://www.press.example/n?apikey=9&id=3",
        title="Acme names a CIO",
        summary="Short summary.",
        article_text="Full text, call +1 555 010 7788.",
        category="leadership_change",
        published_date=date(2026, 9, 30),
        captured_at=T0,
    )
    assert record == {
        "schema": "news-archive/1",
        "url": "https://www.press.example/n?id=3",
        "title": "Acme names a CIO",
        "publisher": "press.example",
        "published_date": "2026-09-30",
        "category": "leadership change",
        "summary": "Short summary.",
        "article_text": "Full text, call [redacted].",
        "captured_at": T0.isoformat(),
        "source": "6th agents",
    }
    bare = AR.news_record(
        "https://a.example/n",
        title=None,
        summary=None,
        article_text="",
        category=None,
        published_date=None,
        captured_at=T0,
    )
    assert (bare["title"], bare["summary"], bare["category"], bare["published_date"]) == (
        "",
        "",
        "",
        None,
    )


def test_every_free_text_field_of_a_news_record_goes_through_the_guard() -> None:
    record = AR.news_record(
        "https://a.example/n",
        title="BAD t",
        summary="BAD s",
        article_text="BAD a",
        category="BAD c",
        published_date=None,
        captured_at=T0,
        guard=_refuse_bad,
    )
    assert record["withheld"] == ["article_text", "category", "summary", "title"]
    assert (record["title"], record["summary"], record["article_text"], record["category"]) == (
        "",
        "",
        "",
        "",
    )


def test_a_profile_record_has_these_fields_and_defaults() -> None:
    record = AR.profile_record(
        "https://www.linkedin.com/in/jane-doe?x=1",
        name="Jane Doe",
        job_title="CIO",
        headline="Leader",
        location="Springfield",
        captured_at=T0,
    )
    assert record == {
        "schema": "profile-archive/1",
        "url": "https://www.linkedin.com/in/jane-doe?x=1",
        "name": "Jane Doe",
        "job_title": "CIO",
        "headline": "Leader",
        "location": "Springfield",
        "text": "Jane Doe\nCIO\nLeader\nSpringfield",
        "captured_at": T0.isoformat(),
        "source": "6th agents",
    }
    bare = AR.profile_record(
        "https://a.example/in/x",
        name="Jane",
        job_title=None,
        headline=None,
        location=None,
        captured_at=T0,
    )
    assert (bare["job_title"], bare["headline"], bare["location"], bare["text"]) == (
        "",
        "",
        "",
        "Jane",
    )


def test_every_field_of_a_profile_record_goes_through_the_guard() -> None:
    record = AR.profile_record(
        "https://a.example/in/x",
        name="BAD n",
        job_title="BAD j",
        headline="BAD h",
        location="BAD l",
        captured_at=T0,
        guard=_refuse_bad,
    )
    assert record["withheld"] == ["headline", "job_title", "location", "name", "text"]
    only_headline = AR.profile_record(
        "https://a.example/in/x",
        name="N",
        job_title="J",
        headline="BAD h",
        location="L",
        captured_at=T0,
        guard=lambda t: (t, t == "BAD h"),
    )
    assert only_headline["withheld"] == ["headline"]  # the text holds the headline, not equal to it
    assert only_headline["text"] == "N\nJ\nBAD h\nL"


def test_a_detection_record_has_these_fields_and_defaults() -> None:
    record = AR.detection_record(
        "https://acme.example/stack",
        technology="Workday",
        first_seen="2025-01-02",
        last_seen=date(2026, 9, 30),
        sources=3,
        captured_at=T0,
    )
    assert record == {
        "schema": "detection-archive/1",
        "url": "https://acme.example/stack",
        "technology": "Workday",
        "first_seen": "2025-01-02",
        "last_seen": "2026-09-30",
        "sources": 3,
        "text": "Workday\nFirst seen 2025-01-02\nLast seen 2026-09-30\nSources 3",
        "captured_at": T0.isoformat(),
        "source": "6th agents",
    }
    bare = AR.detection_record(
        "https://a.example/s",
        technology="X",
        first_seen=None,
        last_seen=None,
        sources=0,
        captured_at=T0,
    )
    assert bare["text"] == "X\nFirst seen not given\nLast seen not given\nSources 0"
    assert bare["first_seen"] is None and bare["last_seen"] is None


def test_the_technology_and_text_of_a_detection_go_through_the_guard() -> None:
    record = AR.detection_record(
        "https://a.example/s",
        technology="BAD tech",
        first_seen=None,
        last_seen=None,
        sources=1,
        captured_at=T0,
        guard=_refuse_bad,
    )
    assert record["withheld"] == ["technology", "text"]
    assert record["technology"] == "" and record["text"] == ""
    clean = AR.detection_record(
        "https://a.example/s",
        technology="Tech",
        first_seen=None,
        last_seen=None,
        sources=1,
        captured_at=T0,
        guard=lambda t: (t, t.startswith("Tech\n")),
    )
    assert clean["withheld"] == ["text"] and clean["technology"] == "Tech"


def test_guarded_blanks_a_refused_field_and_lists_each_refusal_once() -> None:
    record = AR.guarded({"a": "BAD", "b": "ok", "c": None}, ("a", "b", "c"), _refuse_bad)
    assert record == {"a": "", "b": "ok", "c": "", "withheld": ["a"]}
    lists = AR.guarded({"tags": ["BAD 1", "ok", "BAD 2"]}, (), _refuse_bad, lists=("tags",))
    assert lists == {"tags": ["ok"], "withheld": ["tags"]}
    assert "withheld" not in AR.guarded({"a": "ok"}, ("a",), _refuse_bad)
    shown = AR.guarded({"a": "x"}, ("a",), lambda t: ("shown", False))
    assert shown["a"] == "shown"


def test_the_index_meta_keeps_short_one_line_fields_only() -> None:
    meta = AR.index_meta(
        {
            "status_at_capture": "open",
            "location": "  Spring   field ",
            "requisition": "",
            "schema": "s/1",
        }
    )
    assert meta == {"status_at_capture": "open", "location": "Spring field", "schema": "s/1"}
    assert AR.index_meta({"requisition": "x" * 300})["requisition"] == "x" * 200  # type: ignore[index]
    assert AR.index_meta({"other": "x"}) is None


# ---- replica.current (the newest-row clause; mutmut makes no mutant of an f-string) -------------


def test_the_newest_row_clause_keeps_a_row_only_when_no_later_row_is_the_same_thing() -> None:
    from account_intelligence.replica import current as C

    assert C.newest_only("things", "t", "n.k = t.k") == (
        " AND NOT EXISTS (SELECT 1 FROM things n WHERE n.account_id = t.account_id"
        " AND n.k = t.k AND (n.created_at, n.id) > (t.created_at, t.id))"
    )
    assert C.newest_only("job_postings", "j", "n.posting_key = j.posting_key") == C.NEWEST_POSTING
    assert "n.status = 'current' AND n.superseded_at IS NULL" in C.NEWEST_FACT
    assert "n.visibility = f.visibility" in C.NEWEST_FACT and " facts n " in C.NEWEST_FACT
    assert "p.person_key IS NOT NULL AND n.person_key = p.person_key" in C.NEWEST_PERSON
    assert " people n " in C.NEWEST_PERSON
    assert (
        "n.technology = t.technology" in C.NEWEST_DETECTION
        and " tech_detections n " in C.NEWEST_DETECTION
    )
