"""The structured archive records of every kind of link (news, page, profile, detection): their
fields, the identity that ignores the capture time, and the guard that blanks a field instead of
failing the copy. SYNTHETIC data only."""

from __future__ import annotations

from datetime import UTC, datetime

from account_intelligence import archive_records as AR

T0 = datetime(2026, 10, 27, 9, tzinfo=UTC)
T1 = datetime(2026, 10, 29, 9, tzinfo=UTC)


def test_a_news_record_has_headline_publisher_date_category_summary_and_text() -> None:
    record = AR.news_record(
        "https://www.press.example/releases/1",
        title="Acme names a CIO",
        summary="Acme appointed a CIO.",
        article_text="Acme names a CIO\nBody.",
        category="leadership_change",
        published_date="2026-09-30T08:00:00Z",
        captured_at=T0,
    )
    assert record["schema"] == "news-archive/1" and record["source"] == "6th agents"
    assert (record["publisher"], record["published_date"], record["category"]) == (
        "press.example",
        "2026-09-30",
        "leadership change",
    )
    assert "withheld" not in record and record["article_text"].startswith("Acme names")


def test_profile_and_detection_records_keep_their_own_fields() -> None:
    profile = AR.profile_record(
        "https://www.linkedin.com/in/jane-doe",
        name="Jane Doe",
        job_title="CIO",
        headline="Technology leader",
        location="Springfield",
        captured_at=T0,
    )
    assert profile["schema"] == "profile-archive/1"
    assert (profile["name"], profile["job_title"], profile["headline"]) == (
        "Jane Doe",
        "CIO",
        "Technology leader",
    )
    assert profile["text"] == "Jane Doe\nCIO\nTechnology leader\nSpringfield"
    detection = AR.detection_record(
        "https://acme.example/stack",
        technology="Workday",
        first_seen="2025-01-02",
        last_seen="2026-09-30T00:00:00Z",
        sources=3,
        captured_at=T0,
    )
    assert detection["schema"] == "detection-archive/1"
    assert (detection["technology"], detection["first_seen"], detection["last_seen"]) == (
        "Workday",
        "2025-01-02",
        "2026-09-30",
    )
    assert "Sources 3" in detection["text"]


def test_identity_ignores_when_we_looked_and_sees_changed_content() -> None:
    a = AR.page_record("https://a.example/p", "Same text", title="T", captured_at=T0)
    b = AR.page_record("https://a.example/p", "Same text", title="T", captured_at=T1)
    c = AR.page_record("https://a.example/p", "Edited text", title="T", captured_at=T0)
    assert AR.identity_bytes(a) == AR.identity_bytes(b) != AR.identity_bytes(c)
    assert AR.snapshot_bytes(a) != AR.snapshot_bytes(b)  # the body keeps its own capture time


def test_a_refused_field_is_blank_and_named_and_a_refused_list_item_is_dropped() -> None:
    def guard(text: str) -> tuple[str, bool]:
        return text, "secret" in text

    news = AR.news_record(
        "https://a.example/n",
        title="Fine",
        summary="A secret summary",
        article_text="ok",
        category=None,
        published_date=None,
        captured_at=T0,
        guard=guard,
    )
    assert news["summary"] == "" and news["title"] == "Fine" and news["withheld"] == ["summary"]
    rec = AR.guarded({"tags": ["a", "secret b", "c"]}, (), guard, lists=("tags",))
    assert rec["tags"] == ["a", "c"] and rec["withheld"] == ["tags"]


def test_the_default_guard_is_the_platform_denylist_and_a_publisher_is_the_host() -> None:
    assert AR.default_guard("plain text") == ("plain text", False)
    assert AR.publisher_of("https://WWW.Example.com/a/b") == "example.com"
    assert AR.publisher_of("not a url") == ""
