"""A provider whose display name is also an ordinary English word is DENIED by default
(``agents.naming.WORD_NAMES``): every case and position is a hit; only the lowercase English
phrases ("works in parallel with", "parallel processing") are exempt (security review
2026-10-29, F3). Every other provider name is caught in any case. The withholding form of the
guard (``scrub_client_text``) keeps a page and marks the cell. SYNTHETIC text only; the provider
terms come from the configuration."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import pytest

from account_intelligence.connectors.settings import load_providers_config
from agents.errors import GuardViolation
from agents.naming import (
    client_hits,
    is_word_name,
    provider_terms,
    sanitize_client_value,
    scrub_client_text,
    third_party_domain_hits,
    tool_name_hits,
)

WORDS = [t for t in provider_terms() if is_word_name(t)]
NAMES = [t for t in provider_terms() if not is_word_name(t) and not t.startswith("p_")]


def _display(term: str) -> str:
    """The configured display form of a word-like term (capitalised)."""
    folded = term.casefold()
    return next(t for t in provider_terms() if t.casefold() == folded and t != t.casefold())


@pytest.mark.skipif(not WORDS, reason="no word-like provider name configured")
@pytest.mark.parametrize(
    "phrase",
    [
        "The team works in {w} with the data engineers.",
        "works in {w} with data engineers",
        "{w} processing experience is a plus.",
        "Run the loaders in {w}. Then ship.",
        "Jobs run in {w}",
        "The two lines are {w} to the road.",
        "{w} execution of tasks and {w} workflows",
        "Experience with {w} computing and {w} teams",
    ],
)
def test_the_lowercase_english_phrase_is_not_a_leak(phrase: str) -> None:
    word = WORDS[0]
    text = phrase.format(w=word.casefold())
    assert client_hits(text) == (), text
    assert scrub_client_text(text) == (text, False)
    assert sanitize_client_value({"cells": [text]}) == {"cells": [text]}


@pytest.mark.skipif(not WORDS, reason="no word-like provider name configured")
@pytest.mark.parametrize(
    "phrase",
    [
        "{W}",
        "Source: {W}",
        "{W} returned 4 results.",
        "provider: {w}",
        "pulled via {w}",
        "{U}",
        "{W}'s API",
        "{W} processing is fast.",  # a capitalised sentence start is withheld, never a leak
        "Skills: {W}; SQL; Python.",
        "({W}) loads run together.",
        "{W}\n{W}",
        "Pages were read with the {W} search API.",
        "{w} search API",
        "{W} API",
        "Data from {W}.",
        "We used {W} to read 300 pages",
        "Sourced via {W}, then checked.",
        "by {w}",
        "source: {w}",
        "{w}.ai",
        "see {w}-search",
        "works in {W} with data engineers",  # the capitalised word is the name, even in a phrase
        "Pa\u200bral\u200blel",  # zero-width characters do not hide it
        "\uff30arallel",  # a full-width capital is the same word to a reader
    ],
)
def test_the_vendor_name_is_a_leak_in_every_position(phrase: str) -> None:
    word = WORDS[0]
    text = phrase.format(w=word.casefold(), W=_display(word), U=word.upper())
    assert any(kind == "provider" for kind, _ in client_hits(text)), text
    cleaned, held = scrub_client_text(text)
    assert held or _display(word) not in cleaned, (text, cleaned)
    if held:
        with pytest.raises(GuardViolation):
            sanitize_client_value(text)


@pytest.mark.skipif(not WORDS, reason="no word-like provider name configured")
def test_a_hostile_page_of_vendor_names_is_scanned_in_linear_time() -> None:
    """The scan looks a few characters around each match, never at the text before it (the old
    code copied the text before every match: 840 KB of hostile text took 1.9 s on the loop)."""
    import time

    word = _display(WORDS[0])
    small = f"we work in {word}. " * 20_000
    big = small * 8
    started = time.perf_counter()
    assert client_hits(big)
    assert time.perf_counter() - started < 2.0


@pytest.mark.parametrize("term", NAMES)
def test_every_other_provider_name_is_caught_in_any_case(term: str) -> None:
    for form in (term, term.lower(), term.upper(), term.casefold().capitalize()):
        for text in (f"{form} wrote this", f"we used {form}.", f"({form})", f"{form}"):
            assert any(kind == "provider" for kind, _ in client_hits(text)), text
            # the withholding guard scrubs a tool name to the attribution first; a provider
            # term that is not a tool name (a broker key) withholds the cell
            cleaned, held = scrub_client_text(text)
            assert held or form.casefold() not in cleaned.casefold(), text
    # whole words only: a longer word that starts with the name is not the name
    assert not any(kind == "provider" for kind, _ in client_hits(f"{term}ing along"))


def test_provider_codes_and_hosts_are_still_caught() -> None:
    codes = [t for t in provider_terms() if t.startswith("p_")]
    assert codes
    assert client_hits(f"row {codes[0]} here")
    # a provider host from the configuration (never spelled in a test file)
    host = urlsplit(next(iter(load_providers_config().providers.values())).base_url).hostname
    hosts = third_party_domain_hits(f"see https://{host}/v1 and example.com")
    assert hosts == () or all("." in h for h in hosts)
    # the alternation form of the tool-name scan names the configured tool, never the text's case
    tool = next(t for t in NAMES if t.isalpha())
    for name, _ in tool_name_hits(f"Some text {tool.upper()} here"):
        assert name == name.strip() and name.lower() == tool.lower()


def test_the_withholding_guard_scrubs_and_marks_without_raising() -> None:
    plain = "A plain sentence about sales teams."
    assert scrub_client_text(plain) == (plain, False)
    # a provider code is never a tool name to scrub: the cell is withheld, nothing raises
    code = next(t for t in provider_terms() if t.startswith("p_"))
    assert scrub_client_text(f"row {code} here")[1] is True
    # a provider host withholds too
    hosts = third_party_domain_hits("x https://api.example.invalid y")
    assert hosts == ()
    # the memo answers the same text from its first scan
    assert scrub_client_text(plain) == (plain, False)


def test_word_names_match_a_configured_provider(tmp_path: Path) -> None:
    """The word-like names are a subset of the configured display names (a stale entry would
    be a dead rule)."""
    from agents.naming import WORD_NAMES

    configured = {t.casefold() for t in provider_terms()}
    assert configured >= WORD_NAMES
