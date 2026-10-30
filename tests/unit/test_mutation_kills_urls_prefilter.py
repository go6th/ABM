"""Kill tests for the mutation run of ``account_intelligence.urls`` and
``account_intelligence.safety.prefilter`` (2026-10-30).

The existing prefilter tests compare the filtered scan with the plain one, which stays equal when
the filter never skips anything; these pin what the filter proves (the needles, the folding, the
slicing of a large text, the word index). Hermetic: pure functions, synthetic text.
"""

from __future__ import annotations

import re

import pytest

from account_intelligence import urls as U
from account_intelligence.safety import prefilter as PF


@pytest.fixture(autouse=True)
def _fresh_needles() -> None:
    """``needles`` is cached by pattern; a mutant of the helpers behind it must run in each test."""
    PF.needles.cache_clear()


# ---- urls.normalize_url -----------------------------------------------------------------------


def test_normalize_url_refuses_with_the_offending_url_in_the_message() -> None:
    with pytest.raises(ValueError) as e1:
        U.normalize_url("https://a.example/x y")
    assert str(e1.value) == "URL contains whitespace: 'https://a.example/x y'"
    with pytest.raises(ValueError) as e2:
        U.normalize_url("ftp://a.example/x")
    assert str(e2.value) == "not an http(s) URL: 'ftp://a.example/x'"
    with pytest.raises(ValueError) as e3:
        U.normalize_url("https:///x")
    assert str(e3.value) == "URL has no host: 'https:///x'"
    with pytest.raises(ValueError) as e4:
        U.normalize_url("https://a.example:99999/x")
    assert str(e4.value) == "URL has an invalid port: 'https://a.example:99999/x'"


def test_normalize_url_strips_the_trailing_dot_of_a_host_and_slashes_of_a_path() -> None:
    assert U.normalize_url("https://Example.COM./a/") == "https://example.com/a"
    assert U.normalize_url("https://xx.example/") == "https://xx.example/"
    assert U.normalize_url("https://x.example//") == "https://x.example/"
    assert U.normalize_url("https://x.example/aX/") == "https://x.example/aX"
    assert U.normalize_url("https://x.example") == "https://x.example/"
    assert U.normalize_url("https://x.example/a") == "https://x.example/a"


def test_url_hash_is_the_sha256_of_the_utf8_text() -> None:
    import hashlib

    assert (
        U.url_hash("https://x.example/é")
        == hashlib.sha256("https://x.example/é".encode()).hexdigest()
    )


# ---- urls.strip_secrets -----------------------------------------------------------------------


def test_strip_secrets_removes_credentials_and_secret_parameters_only() -> None:
    url = "https://user:pw@Host.example:8443/p/a?x=1&token=2&Key=3&y=4&SIG&flag#frag"
    assert U.strip_secrets(url) == "https://Host.example:8443/p/a?x=1&y=4&flag#frag"


def test_strip_secrets_takes_the_host_after_the_last_at_sign() -> None:
    assert U.strip_secrets("https://u:p@ss@host.example/a") == "https://host.example/a"
    assert U.strip_secrets("https://u@host.example/a") == "https://host.example/a"


def test_strip_secrets_compares_the_key_not_the_value_and_keeps_order() -> None:
    assert (
        U.strip_secrets("https://h.example/a?x=token&b=2&a=1")
        == "https://h.example/a?x=token&b=2&a=1"
    )
    assert U.strip_secrets("https://h.example/a?token=a=b&c=d") == "https://h.example/a?c=d"
    assert U.strip_secrets("https://h.example/a?TOKEN=1&Api_Key=2&c=3") == "https://h.example/a?c=3"
    assert U.strip_secrets("https://h.example/a?x-amz-signature=1&x-goog-credential=2&q=1") == (
        "https://h.example/a?q=1"
    )
    assert U.strip_secrets("https://h.example/a?token=1") == "https://h.example/a"
    assert U.strip_secrets("https://h.example/a?q=1&q=2") == "https://h.example/a?q=1&q=2"


def test_strip_secrets_trims_whitespace_around_the_address_and_ignores_an_empty_query() -> None:
    assert U.strip_secrets("  https://h.example/a?t=1  ") == "https://h.example/a?t=1"
    assert U.strip_secrets("https://h.example/a") == "https://h.example/a"
    assert U.strip_secrets("https://h.example/a?") == "https://h.example/a"


def test_strip_secrets_leaves_what_is_not_an_http_address_as_it_is() -> None:
    for url in (
        "ftp://u:p@host.example/a?token=1",
        "mailto:a@b.example?token=1",
        "https:///a?token=1",
        "https://host.example:99999/a?token=1",
        "not a url",
    ):
        assert U.strip_secrets(url) == url
    assert U.strip_secrets("HTTPS://u:p@host.example/a?token=1") == "https://host.example/a"


# ---- urls.clean_http_url ----------------------------------------------------------------------


def test_clean_http_url_repairs_spelling_only() -> None:
    assert U.clean_http_url("  HTTPS://Example.COM:8080/Path With Space?Q=1#Frag  ") == (
        "https://example.com:8080/Path%20With%20Space?Q=1#Frag"
    )
    assert U.clean_http_url("http://a.example/x") == "http://a.example/x"


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "",
        "   ",
        "ftp://a.example/x",
        "javascript:alert(1)",
        "https:///x",
        "https://u:p@a.example/x",
        "https://u@a.example/x",
        "https://a.example/x\ny",
        "https://a.example/x\\y",
        "https://a.example/​x",
        "https://a.example/‮x",
        "https://a.example/\x7fx",
        "https://a.example:99999/x",
        "a.example/x",
    ],
)
def test_clean_http_url_refuses_what_is_not_a_usable_address(bad: object) -> None:
    assert U.clean_http_url(bad) is None  # type: ignore[arg-type]


def test_clean_http_url_allows_exactly_the_longest_link() -> None:
    head = "https://a.example/"
    ok = head + "a" * (U.MAX_LINK - len(head))
    assert len(ok) == U.MAX_LINK and U.clean_http_url(ok) == ok
    assert U.clean_http_url(ok + "a") is None


# ---- prefilter.fold ---------------------------------------------------------------------------


def test_fold_lowers_ascii_and_folds_the_letters_the_engine_counts_equal_to_ascii() -> None:
    assert PF.fold("AbC") == "abc"
    assert PF.fold("İ") == "i" and PF.fold("ı") == "i"
    assert PF.fold("İSTANBUL ıı") == "istanbul ii"
    assert PF.fold("ſ") == "s" and PF.fold("K") == "k"  # long s, Kelvin sign
    assert PF.fold("Straße") == "strasse"


# ---- prefilter.needles ------------------------------------------------------------------------


def test_needles_are_the_longest_literal_run_of_the_pattern() -> None:
    assert PF.needles(re.compile("hello world")) == ("hello world",)
    assert PF.needles(re.compile("Hello World", re.I)) == ("hello world",)
    assert PF.needles(re.compile("Hello World")) == ("Hello World",)
    assert PF.needles(re.compile(r"\bfoo\b")) == ("foo",)
    assert PF.needles(re.compile(r"(?<!\w)foo(?!\w)")) == ("foo",)
    assert PF.needles(re.compile(r"ab\d+cdef")) == ("cdef",)
    assert PF.needles(re.compile(r"foo\d+bar")) == ("foo",)  # equal length: the first run
    assert PF.needles(re.compile(r"fo\d+barbaz\d+xyz")) == ("barbaz",)
    assert PF.needles(re.compile(r"(foo)bar")) == ("bar",)


def test_needles_need_three_ascii_characters() -> None:
    assert PF.needles(re.compile("ab")) is None
    assert PF.needles(re.compile("abc")) == ("abc",)
    assert PF.needles(re.compile("ééé")) is None
    assert PF.needles(re.compile(r"éé\d+abc")) == ("abc",)
    assert PF.needles(re.compile(r"\d+")) is None


def test_needles_of_an_alternation_hold_one_per_branch_or_none() -> None:
    assert PF.needles(re.compile("foo|barbaz")) == ("foo", "barbaz")
    assert PF.needles(re.compile("foo|foo")) == ("foo",)
    assert PF.needles(re.compile("Foo|BARBAZ", re.I)) == ("foo", "barbaz")
    assert PF.needles(re.compile("foo|ab")) is None
    assert PF.needles(re.compile("foo|\\d+")) is None


# ---- prefilter.Scanned / cannot_match ---------------------------------------------------------


def test_a_pattern_provably_cannot_match_a_text_without_its_needle() -> None:
    assert PF.cannot_match(re.compile("hello world"), "nothing here") is True
    assert PF.cannot_match(re.compile("hello world"), "say hello world now") is False
    assert PF.cannot_match(re.compile("Hello World", re.I), "SAY HELLO WORLD") is False
    assert PF.cannot_match(re.compile("Hello World"), "say hello world") is True  # exact case
    assert PF.cannot_match(re.compile(r"\d+"), "nothing") is False  # no needle: always run
    assert PF.cannot_match(re.compile("foo|barbaz"), "xx barbaz") is False
    assert PF.cannot_match(re.compile("foo|barbaz"), "qux") is True


@pytest.fixture
def small_slice(monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(PF, "SLICE", 16)
    return 16


def test_a_large_text_is_scanned_in_slices_with_an_overlap(small_slice: int) -> None:
    pattern = re.compile("needle")
    # the needle straddles the boundary of the first slice (characters 13 to 19)
    straddle = "x" * 13 + "needle" + "y" * 40
    assert len(straddle) > small_slice
    assert PF.cannot_match(pattern, straddle) is False
    assert PF.cannot_match(pattern, "x" * 100) is True
    assert PF.cannot_match(pattern, "x" * 80 + "needle") is False  # in the last slice


def test_a_large_text_keeps_the_case_of_a_case_sensitive_needle(small_slice: int) -> None:
    pattern = re.compile("Needle")
    assert PF.cannot_match(pattern, "x" * 40 + "Needle" + "y" * 40) is False
    assert PF.cannot_match(pattern, "x" * 40 + "needle" + "y" * 40) is True
    folded = re.compile("Needle", re.I)
    assert PF.cannot_match(folded, "x" * 40 + "NEEDLE" + "y" * 40) is False


def test_the_words_of_a_large_text_are_the_same_as_of_a_small_one(small_slice: int) -> None:
    text = "Alpha beta, gamma delta; epsilon zeta eta theta iota kappa lambda mu"
    big = PF.Scanned(text).words()
    assert big == {w.lower() for w in re.findall(r"\w+", text)}
    assert PF.Scanned("Ünï ÇÖde Plain").words() == {"ünï", "çöde", "plain"}


# ---- prefilter.build_index / TermIndex --------------------------------------------------------


def test_the_index_holds_one_pattern_per_non_blank_term() -> None:
    index = PF.build_index(("Foo Bar", "  ", "Baz"))
    assert isinstance(index.patterns, tuple) and len(index.patterns) == 2
    assert index.patterns[0].pattern == r"(?<!\w)Foo\ Bar(?!\w)"
    assert index.patterns[0].flags & re.IGNORECASE
    assert index.patterns[0].search("see foo bar now") is not None
    assert index.patterns[0].search("see foobar now") is None
    assert index.patterns[1].search("x BAZ.") is not None
    assert index.patterns[1].search("xbaz") is None
    assert index.words == (("foo", "bar"), ("baz",))
    assert isinstance(index.words, tuple)


def test_a_term_is_filed_under_its_longest_word() -> None:
    index = PF.build_index(("Foo Barbaz", "Qux Barbaz", "Solo"))
    assert index.by_word == {"barbaz": (0, 1), "solo": (2,)}
    assert index.always == ()
    tie = PF.build_index(("Foo Bar",))
    assert tie.by_word == {"foo": (0,)}  # equal length: the first word


def test_terms_without_an_ascii_word_are_always_run() -> None:
    index = PF.build_index(("Ünï", "---", "Plain"))
    assert index.always == (0, 1)
    assert index.by_word == {"plain": (2,)}
    assert index.words == ((), (), ("plain",))
    assert isinstance(index.always, tuple)


def test_candidates_are_the_terms_whose_every_word_is_in_the_text() -> None:
    index = PF.build_index(("Foo Bar", "Baz", "Ünï"))
    assert index.candidates(PF.Scanned("bar foo here")) == [0, 2]
    assert index.candidates(PF.Scanned("only foo here")) == [2]  # "bar" is missing
    assert index.candidates(PF.Scanned("BAZ and foo")) == [1, 2]
    assert index.candidates(PF.Scanned("nothing")) == [2]
    assert PF.build_index(()).candidates(PF.Scanned("anything")) == []
    assert PF.build_index(("Plain",)).candidates(PF.Scanned("nothing")) == []
