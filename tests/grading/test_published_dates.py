"""A source's date is the page's own stated date (master prompt v2 section 5): a scraped page
carries the article's metadata date; a calendar date alone is a dateline; a time without a zone
is not trusted; the day the page was read never becomes its date."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from account_intelligence.connectors.models import ScrapedPageRecord
from account_intelligence.grading.adapters import record_published
from tests.grading.test_adapters_docs import BASE


def _page(publish_date: str | None) -> ScrapedPageRecord:
    return ScrapedPageRecord(
        kind="scraped_page",
        requested_url="https://acme.example/news/a",
        final_url="https://acme.example/news/a",
        target_status=200,
        markdown_text="Acme names a new chief technology officer.",
        title_text="Acme names CTO",
        publish_date=publish_date,
        **BASE,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-09-01T10:00:00Z", datetime(2026, 9, 1, 10, tzinfo=UTC)),
        ("2026-09-01", datetime(2026, 9, 1, tzinfo=UTC)),
        ("2026-09-01T10:00:00", None),
        ("yesterday", None),
        (None, None),
    ],
)
def test_a_scraped_page_is_dated_by_its_own_metadata(
    raw: str | None, expected: datetime | None
) -> None:
    assert record_published(_page(raw)) == expected
