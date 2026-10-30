"""The client-output scan skips a pattern only when it provably cannot match (fix wave C): the
filtered scan returns exactly the hits of running every pattern, on plain, folded, obfuscated and
random texts, and the folding agrees with the regular expression engine's own case tables."""

from __future__ import annotations

import random
import re
import sys

import pytest

from account_intelligence.safety import client_output as CO
from account_intelligence.safety import prefilter as PF
from tests.exports.corpus import broker

TERMS = ("Jane Smith", "O'Neil", "C&A", "Acme Inc.", "José García", "***", "ab", "Smith", "x y z")
# data tools are named through the routing asset, never typed (a static guard keeps the names out
# of the tests)
SCRAPE, SIGNALS, ORG = broker("p_scrape"), broker("p_signals"), broker("p_org")
DENY = ("Parallel Search", SCRAPE.upper(), SIGNALS.lower(), ORG, "12 credits", "credits used")


def reference(text: str, terms: tuple[str, ...]) -> tuple[CO.Hit, ...]:
    """Every pattern run on the text, in order: what the scan returned before the filter."""
    patterns = list(CO.load_patterns()) + [
        re.compile(r"(?<!\w)" + re.escape(t.strip()) + r"(?!\w)", re.I) for t in terms if t.strip()
    ]
    return tuple(CO.Hit(p.pattern, m.start()) for p in patterns for m in p.finditer(text))


TEXTS = [
    "",
    "Nothing to see here, a plain job description about Salesforce administration.",
    f"We use Parallel   Search and {SCRAPE.swapcase()} every day; 12 credits used.",
    "ſumble and fırecrawl and FİRECRAWL and Kelvin predıctleads",  # long s, dotless i, dotted I
    "Jane Smith met o'neil at C&A; ACME INC. and acme inc paid José García.",
    "jane smithson, Smith-Jones, smith's, SMITH, x y z, x y  z, ab_cd, ab cd, AB",
    "Parallelsearch, parallel searching, credit, credits, 5 credit used credits remaining",
    "Ｊane Ｓmith fullwidth, jane smith nbsp, jane​smith zero width",
    "ß ẞ ﬁrecrawl ﬃ ﬆ ǅ µ μ Μ ς σ Σ",
    "tést José GARCÍA josé garcía JOSÉ GARCIA",
]


@pytest.mark.parametrize("text", TEXTS)
def test_the_filtered_scan_returns_the_hits_of_the_plain_scan(text: str) -> None:
    assert CO.scan_text(text, TERMS) == reference(text, TERMS)
    assert CO.scan_text(text) == reference(text, ())


def test_random_texts_give_the_same_hits() -> None:
    rng = random.Random(11)
    alphabet = list("abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,-_&'")
    alphabet += ["ſ", "ı", "İ", "K", "é", "É", "ß", "​", " ", "Ω", "Å", "ǅ"]
    words = [t.lower() for t in (*TERMS, *DENY)]
    for _ in range(600):
        chunks = [
            rng.choice(words)
            if rng.random() < 0.15
            else "".join(rng.choices(alphabet, k=rng.randint(1, 9)))
            for _ in range(rng.randint(1, 25))
        ]
        text = rng.choice([" ", "", "-"]).join(chunks)
        if rng.random() < 0.3:
            text = text.upper()
        assert CO.scan_text(text, TERMS) == reference(text, TERMS), text


def test_a_term_set_of_thousands_costs_one_pass_over_the_words() -> None:
    terms = tuple(f"Person{n} Name{n}" for n in range(3000)) + ("Jane Smith",)
    text = "A description with nothing in it. " * 400 + " jane smith "
    assert [h.pattern for h in CO.scan_text(text, terms)] == [r"(?<!\w)Jane\ Smith(?!\w)"]


def test_the_needles_are_read_from_the_parsed_pattern() -> None:
    def n(p: str, flags: int = re.I) -> tuple[str, ...] | None:
        return PF.needles(re.compile(p, flags))

    assert n(r"\bparallel\s+(search|fetch|ai|web)\b") == ("parallel",)
    assert n(r"\b\d[\d,\.]*\s+credits?\b") == ("credit",)
    assert n("salesforce|\\bsfdc\\b|sales cloud") == ("salesforce", "sfdc", "sales cloud")
    assert n(r"\bSAP\b|S/4 ?HANA|\bAriba\b", 0) == ("SAP", "HANA", "Ariba")
    assert n("a|b") is None  # nothing long enough
    assert n(r"\w+") is None and n(r"(?:foo|bar)baz") == ("baz",)
    assert n(r"foo|\w+bar") == ("foo", "bar")
    assert n(r"foo|\w+") is None  # one alternative has no literal: always run


def test_the_fold_agrees_with_the_engine_for_every_ascii_letter() -> None:
    """For every code point the engine counts equal to an ASCII letter (ignoring case), the fold
    of that code point is the letter, so a needle search on the folded text cannot miss it."""
    letters = "abcdefghijklmnopqrstuvwxyz"
    compiled = {c: re.compile(c, re.I) for c in letters}
    for cp in range(0x80, 0x30000):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        ch = chr(cp)
        for letter, rx in compiled.items():
            if rx.fullmatch(ch):
                assert letter in PF.fold(ch), (hex(cp), letter)


def test_the_engines_case_equivalences_fold_alike_for_non_ascii_terms() -> None:
    """Non-ASCII terms are always run, not indexed; this pins the one fact the index relies on
    for ASCII words: an ASCII word in a term is a whole word of any text the term matches."""
    extra = getattr(sys.modules.get("re._casefix"), "_EXTRA_CASES", None)
    if extra is None:  # pragma: no cover - other Python layouts
        pytest.skip("no case table to read")
    for cp, others in extra.items():
        for other in others:
            assert PF.fold(chr(cp)) == PF.fold(chr(other)), (hex(cp), hex(other))


def test_a_platform_is_named_exactly_when_its_pattern_matches() -> None:
    """``PlatformEntry.names`` gives the answer of the bare pattern for every dictionary entry on
    texts that name, nearly name and do not name the platform."""
    from exports.config import default_export_config

    cfg = default_export_config()
    texts = [
        "Works with Salesforce and SFDC daily; Sales Cloud, Service Cloud.",
        "Experience with S/4HANA, S4 HANA, SAP, sap and Ariba or ariba.",
        "ServiceNow administrator; service now and SERVICENOW. Workday, WORKDAY, workdays.",
        "Veeva Vault CRM, veeva, VEEVAı and promomats; eTMF, CTMS.",
        "Nothing about any platform appears in this sentence at all.",
        "ſalesforce and Sales Cloud with Kelvin Kafka and Confluent kafka",
    ]
    for entry in cfg.platforms:
        for text in texts:
            assert entry.names(text) == bool(entry.regex.search(text)), (entry.name, text)


@pytest.mark.parametrize("size", [1, 5, 7, 16, 33])
def test_a_text_read_in_slices_gives_the_same_hits(
    monkeypatch: pytest.MonkeyPatch, size: int
) -> None:
    """A large text is folded and tokenised slice by slice (memory); a needle or a word that
    straddles a slice boundary is still found."""
    monkeypatch.setattr(PF, "SLICE", size)
    for text in TEXTS:
        assert CO.scan_text(text, TERMS) == reference(text, TERMS), text
    rng = random.Random(size)
    for _ in range(120):
        words = rng.choices(
            [*(t.lower() for t in (*TERMS, *DENY)), "plain", "text", "ſumble"], k=12
        )
        text = " ".join(words)
        assert CO.scan_text(text, TERMS) == reference(text, TERMS), text
