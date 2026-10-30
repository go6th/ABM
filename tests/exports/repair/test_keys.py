"""Key helpers are total: text that merely looks like a URL never raises."""

from __future__ import annotations

import pytest

from exports.repair.keys import canonical_url, host_of, posting_id


@pytest.mark.parametrize(
    "text", ["Annual: Integration & Transition Services Agreement Exit", "a:b:c", "http://[bad", ""]
)
def test_odd_text_never_raises(text: str) -> None:
    canonical_url(text)
    host_of(text)
    posting_id(text)


def test_canonical_url_folds_scheme_www_tracking_and_slash() -> None:
    assert (
        canonical_url("HTTP://www.Example.com/a/b/?utm_source=x&b=2&a=1#frag")
        == "https://example.com/a/b?a=1&b=2"
    )


@pytest.mark.parametrize(
    ("url", "req", "expected"),
    [
        ("https://jobs.example.com/en/job/tempe/role/159/991440", "", "991440"),
        ("https://x.myworkdayjobs.com/en/job/Foo_R-12345-1", "", "R-12345"),
        ("https://x.example.com/job/role", "REQ 77", "REQ 77"),
    ],
)
def test_posting_id(url: str, req: str, expected: str) -> None:
    assert posting_id(url, req) == expected
