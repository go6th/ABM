"""URL normalisation: the dedupe key behind stable source codes."""

from __future__ import annotations

import hashlib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from account_intelligence.urls import normalize_url, url_hash


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTPS://News.Example.COM/Story", "https://news.example.com/Story"),
        ("https://example.com", "https://example.com/"),
        ("https://example.com/", "https://example.com/"),
        ("https://example.com/a/b/", "https://example.com/a/b"),
        ("https://example.com:443/a", "https://example.com/a"),
        ("http://example.com:80/a", "http://example.com/a"),
        ("https://example.com:8443/a", "https://example.com:8443/a"),
        ("https://example.com/a#section-2", "https://example.com/a"),
        ("https://example.com/a?b=2&a=1", "https://example.com/a?a=1&b=2"),
        ("https://example.com/a?utm_source=x&UTM_Medium=y&id=7", "https://example.com/a?id=7"),
        ("https://example.com/a?gclid=1&fbclid=2", "https://example.com/a"),
        ("https://user:secret@example.com/a", "https://example.com/a"),
        ("https://example.com./a", "https://example.com/a"),
        ("  https://example.com/a  ", "https://example.com/a"),
        ("https://example.com/a?empty=", "https://example.com/a?empty="),
    ],
)
def test_normalize_url(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


def test_scheme_is_kept_because_http_and_https_can_differ() -> None:
    assert normalize_url("http://example.com/a") != normalize_url("https://example.com/a")


def test_path_case_is_kept() -> None:
    assert normalize_url("https://example.com/Report") != normalize_url(
        "https://example.com/report"
    )


@pytest.mark.parametrize(
    "bad",
    [
        "ftp://example.com/a",
        "example.com/a",
        "https:///a",
        "mailto:a@example.com",
        "https://ex ample.com/",
        "https://example.com:99999/",
    ],
)
def test_non_http_or_malformed_urls_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        normalize_url(bad)


def test_url_hash_is_sha256_hex_of_the_normalized_url() -> None:
    normalized = normalize_url("https://example.com/a")
    assert url_hash(normalized) == hashlib.sha256(normalized.encode()).hexdigest()
    assert len(url_hash(normalized)) == 64


segment = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_.", min_size=1, max_size=12)
urls = st.builds(
    lambda scheme, host, path, params, fragment: (
        f"{scheme}://{host}.example.com/{'/'.join(path)}"
        + ("?" + "&".join(f"{k}={v}" for k, v in params) if params else "")
        + (f"#{fragment}" if fragment else "")
    ),
    st.sampled_from(["http", "https", "HTTPS"]),
    segment.filter(lambda s: s[0].isalnum() and s[-1].isalnum() and ".." not in s),
    st.lists(segment, max_size=4),
    st.lists(st.tuples(segment, segment), max_size=4),
    st.one_of(st.none(), segment),
)


@given(urls)
def test_normalization_is_idempotent(url: str) -> None:
    once = normalize_url(url)
    assert normalize_url(once) == once


@given(urls, st.sampled_from(["utm_source=mail", "gclid=abc", "fbclid=1"]))
def test_tracking_parameters_never_change_the_key(url: str, tracker: str) -> None:
    base, _, fragment = url.partition("#")
    joined = f"{base}{'&' if '?' in base else '?'}{tracker}" + (f"#{fragment}" if fragment else "")
    assert normalize_url(joined) == normalize_url(url)
