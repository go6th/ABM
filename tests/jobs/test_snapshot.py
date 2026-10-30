"""The structured posting copy of the link archive (``posting-archive/1``): the record's fields,
its identity (what the posting said, not when we looked), the guard that withholds a field instead
of failing the copy, and the requisition reading the Job Postings workbook shares. SYNTHETIC data
only (``.example`` addresses)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from jobs import snapshot as SN
from jobs.models import Classification, PlatformSentence, Posting

WHEN = datetime(2026, 10, 20, 9, tzinfo=UTC)
TEXT = (
    "Senior Platform Engineer\nJob ID: R-557322\nYou will build flows on ServiceNow.\n"
    "Questions: hr@jobs.example or +1 555 010 7788.\nWe also use Workday."
)


def _posting(**kw: object) -> Posting:
    base: dict[str, object] = {
        "posting_key": "id:p1",
        "title": "Senior Platform Engineer",
        "location": "Springfield, US",
        "url": "https://jobs.example/p1",
        "urls": ("https://jobs.example/p1",),
        "provider_code": "S-0001",
        "raw_ref": "ledger/1",
        "retrieved_at": WHEN.isoformat(),
        "window_bucket": "in_window",
        "status": "open",
        "first_seen_at": date(2026, 9, 1),
        "last_seen": date(2026, 10, 18),
        "posted_at": date(2026, 8, 30),
        "seniority": "mid_senior",
        "platforms": ("ServiceNow", "Workday"),
        "sentences": (
            PlatformSentence(
                platform="ServiceNow",
                text="You will build flows on ServiceNow.",
                start=0,
                end=5,
                sha256="a",
            ),
            PlatformSentence(
                platform="Workday", text="We also use Workday.", start=6, end=9, sha256="b"
            ),
        ),
        "classification": Classification(
            label="build", classified_by="extractor", reason="builds the platform"
        ),
        "text_ref": "t/postings/p1",
    }
    return Posting(**{**base, **kw})  # type: ignore[arg-type]


def test_the_record_carries_every_field_with_the_schema_and_no_tool_names() -> None:
    record = SN.build_snapshot(_posting(), TEXT, captured_at=WHEN)
    assert record["schema"] == "posting-archive/1" and record["source"] == "6th agents"
    assert record["url"] == "https://jobs.example/p1"
    assert record["title"] == "Senior Platform Engineer"
    assert record["requisition"] == "R-557322"  # the workbook's reading of the Job ID label
    assert record["location"] == "Springfield, US"
    assert record["status_at_capture"] == "open"
    assert (record["first_seen"], record["last_seen"], record["posted_at"]) == (
        "2026-09-01",
        "2026-10-18",
        "2026-08-30",
    )
    assert record["seniority"] == "mid_senior" and record["class"] == "Build role"
    assert record["platforms"] == ["ServiceNow", "Workday"]
    assert record["platform_sentences"] == [
        {"platform": "ServiceNow", "text": "You will build flows on ServiceNow."},
        {"platform": "Workday", "text": "We also use Workday."},
    ]
    assert record["captured_at"] == WHEN.isoformat() and "withheld" not in record
    # contact details are removed from the description, the text is otherwise whole
    assert (
        "hr@jobs.example" not in record["description"]
        and "555 010 7788" not in record["description"]
    )
    assert "Job ID: R-557322" in record["description"]
    # the body round-trips as JSON and names neither a provider code nor a ledger reference
    body = SN.snapshot_bytes(record)
    assert json.loads(body) == record
    assert b"S-0001" not in body and b"ledger/1" not in body


def test_a_closed_and_an_unknown_status_are_kept_as_they_were() -> None:
    assert (
        SN.build_snapshot(_posting(status="closed"), TEXT, captured_at=WHEN)["status_at_capture"]
        == "closed"
    )
    assert (
        SN.build_snapshot(_posting(status="unknown"), TEXT, captured_at=WHEN)["status_at_capture"]
        == "unknown"
    )


def test_the_identity_is_what_the_posting_said_not_when_we_looked() -> None:
    a = SN.build_snapshot(_posting(), TEXT, captured_at=WHEN)
    later = SN.build_snapshot(
        _posting(last_seen=date(2026, 11, 20)), TEXT, captured_at=datetime(2026, 11, 21, tzinfo=UTC)
    )
    assert SN.identity_bytes(a) == SN.identity_bytes(later)  # same posting seen again: one copy
    closed = SN.build_snapshot(_posting(status="closed"), TEXT, captured_at=WHEN)
    reworded = SN.build_snapshot(_posting(), TEXT + "\nNew benefits.", captured_at=WHEN)
    moved = SN.build_snapshot(_posting(location="Shelbyville, US"), TEXT, captured_at=WHEN)
    assert len({SN.identity_bytes(r) for r in (a, closed, reworded, moved)}) == 4


def test_a_posting_that_returns_to_an_earlier_state_is_a_new_copy() -> None:
    """Review 2026-10-29, finding 13: open, closed, open again. The third state says the same
    words as the first; without the day the status last changed it maps to the first copy's key,
    nothing new is written and the newest copy stays ``closed``."""
    opened = SN.build_snapshot(_posting(), TEXT, captured_at=WHEN)
    closed = SN.build_snapshot(_posting(status="closed"), TEXT, captured_at=WHEN)
    again = SN.build_snapshot(_posting(), TEXT, captured_at=datetime(2026, 12, 1, tzinfo=UTC))
    assert SN.identity_bytes(opened) == SN.identity_bytes(again)  # the collision the review found
    day = date(2026, 12, 1)
    assert SN.identity_bytes(again, day) != SN.identity_bytes(opened)
    assert SN.identity_bytes(closed, date(2026, 11, 1)) != SN.identity_bytes(closed)
    # the same change seen again (a later research, status unchanged) is the same copy
    assert SN.identity_bytes(again, day) == SN.identity_bytes(again, day)
    assert SN.identity_bytes(opened, None) == SN.identity_bytes(
        opened
    )  # unchanged postings keep keys


def test_a_field_the_guard_refuses_is_withheld_not_the_copy() -> None:
    def guard(text: str) -> tuple[str, bool]:
        return text, "Workday" in text or "Springfield" in text

    record = SN.build_snapshot(_posting(), TEXT, captured_at=WHEN, guard=guard)
    assert record["description"] == "" and record["location"] == ""
    assert record["platforms"] == ["ServiceNow"]
    assert [x["platform"] for x in record["platform_sentences"]] == ["ServiceNow"]
    assert record["withheld"] == ["description", "location", "platform_sentences", "platforms"]
    assert record["title"] == "Senior Platform Engineer" and record["url"]  # the rest stays


def test_the_default_guard_is_the_platform_denylist() -> None:
    shown, refused = SN.default_guard("A plain sentence about flows.")
    assert (shown, refused) == ("A plain sentence about flows.", False)


def test_requisition_reading_is_the_workbooks_and_blank_without_a_label() -> None:
    from exports.readers.jobs import requisition_of as workbook_reading

    assert workbook_reading is SN.requisition_of  # one function, not two copies
    assert SN.requisition_of("Requisition ID 12345", SN.requisition_pattern()) == "12345"
    assert SN.requisition_of("No label here, R-1", SN.requisition_pattern()) == ""
