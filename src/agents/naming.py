"""G9: tool, provider and person names never reach a client file or an agent prompt.

``client_hits`` combines three checks on text bound for a client:

1. the platform denylist (``safety.client_output.scan_text``: broker names, credit counts);
2. provider display names and provider codes from ``config/routing.yaml`` and
   ``config/providers.yaml`` (internal identities);
3. word digests from ``config/skill/name_digests.yaml`` (the person's name is held only as a
   digest, so it appears nowhere in the repository).

A provider whose display name is also an ordinary English word (``WORD_NAMES``: "Parallel") is
DENIED by default: it is a hit in every case and position (capitalised anywhere, including the
start of a sentence, UPPER CASE, ``Parallel's``, after ``provider:`` or ``via``, inside a domain
or a compound tool name). Only the lowercase English phrases "in parallel" and "parallel
processing/execution/workflows/computing/tasks/teams/lines/universe/to/with" are exempt, so a job
description that says "works in parallel with data engineers" passes and a capitalised "Parallel
processing" costs one withheld cell, never a leak (security review 2026-10-29, F3). Every other
provider name is caught in any case, anywhere. The text is also checked in NFKC form with
zero-width characters removed, so a lookalike spelling does not slip past.

Hits carry the pattern kind and offset, never the matched text.

``scrub_client_text`` is the withholding form of the guard for a page of cells: the text with
tool names scrubbed, and whether the cell must be withheld instead of shown. It is memoised by
text, so a page viewed twice (a sort, Show more rows) is scanned once.

GUARANTEE(test_g9_planted_tool_names_are_caught_in_every_deliverable)
GUARANTEE(test_g9_person_name_digest_is_caught)
GUARANTEE(test_conversion_operator_only_file_is_internal_only)
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path

import yaml

from account_intelligence import archive_records as AR
from account_intelligence.config import CONFIG_DIR
from account_intelligence.safety.client_output import scan_text
from agents.errors import GuardViolation

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)

# Provider display names (casefolded) that are also ordinary English words. They are DENIED by
# default: the name is a hit in every case and position, and only the lowercase English phrases
# of ``_ENGLISH_BEFORE`` / ``_ENGLISH_AFTER`` are exempt (security review 2026-10-29, F3: an
# allow-by-position rule let "Source: Parallel", "Parallel returned 4 results.", "provider:
# parallel", "pulled via parallel", "PARALLEL" and "Parallel's API" through).
WORD_NAMES: frozenset[str] = frozenset({"parallel"})
# The word is ordinary English only when it is written in lowercase AND sits in one of these
# phrases: "in parallel" ("run/runs/running/works/working in parallel" end in it), or followed
# by one of the nouns/prepositions below. A false positive costs one cell, a false negative is a
# leak, so a capitalised or upper-case "Parallel processing" is withheld.
_ENGLISH_BEFORE = re.compile(r"(?<!\w)in\s+$", re.I)
_ENGLISH_AFTER = re.compile(
    r"^\s+(?:processing|execution|workflows?|computing|tasks?|teams?|lines?|universe|to|with)"
    r"(?!\w)",
    re.I,
)
# how far to look around a match for the phrase (never the whole text: the scan is linear)
_LOOK_BEFORE = 8
_LOOK_AFTER = 24
# a text with more vendor hits than this is a hit already: stop counting
_MAX_VENDOR_HITS = 1000


def word_digest(word: str) -> str:
    """sha256 of the case-folded word."""
    return _digest(word.casefold())


@lru_cache(maxsize=1 << 16)
def _digest(folded: str) -> str:
    return hashlib.sha256(folded.encode()).hexdigest()


@lru_cache(maxsize=1)
def name_digests(config_dir: Path | None = None) -> frozenset[str]:
    """The forbidden word digests."""
    doc = yaml.safe_load(((config_dir or CONFIG_DIR) / "skill" / "name_digests.yaml").read_text())
    return frozenset(str(d["sha256"]) for d in doc["digests"]["value"])


@lru_cache(maxsize=1)
def provider_terms(config_dir: Path | None = None) -> tuple[str, ...]:
    """Provider display names, broker keys and provider codes (internal identities)."""
    base = config_dir or CONFIG_DIR
    routing = yaml.safe_load((base / "routing.yaml").read_text())
    providers = yaml.safe_load((base / "providers.yaml").read_text())
    terms = {str(b["display_name"]) for b in routing["brokers"].values()}
    terms |= {str(k) for k in routing["brokers"]}
    terms |= {str(k) for k in (providers.get("providers") or {})}
    terms.discard("builtin")
    terms.discard("Built-in web tools")
    return tuple(sorted(terms))


def is_word_name(term: str) -> bool:
    """True for a provider term that is also an ordinary English word (``WORD_NAMES``)."""
    return term.strip().casefold() in WORD_NAMES


@lru_cache(maxsize=64)
def _vendor_pattern(term: str) -> re.Pattern[str]:
    """The word-like provider name as a whole word in any case (a hyphen, a dot or an
    apostrophe ends the word: ``parallel-search``, a dotted ``parallel.<tld>`` form and
    ``Parallel's`` match)."""
    return re.compile(rf"(?<!\w){re.escape(term.strip().casefold())}(?!\w)", re.I)


def _english_use(text: str, start: int, end: int) -> bool:
    """True when the match is the lowercase English word inside an allow-listed phrase."""
    if text[start:end] != text[start:end].casefold():
        return False
    before = text[max(0, start - _LOOK_BEFORE) : start]
    after = text[end : end + _LOOK_AFTER]
    return bool(_ENGLISH_BEFORE.search(before) or _ENGLISH_AFTER.match(after))


def _vendor_hits(text: str, term: str) -> list[int]:
    """Offsets where a word-like provider name is NOT one of the allow-listed English uses
    (deny by default). One linear pass; each match looks at a few characters around it only."""
    hits: list[int] = []
    for m in _vendor_pattern(term).finditer(text):
        if not _english_use(text, m.start(), m.end()):
            hits.append(m.start())
            if len(hits) >= _MAX_VENDOR_HITS:
                break
    return hits


@lru_cache(maxsize=64)
def _term_patterns(terms: tuple[str, ...]) -> tuple[re.Pattern[str] | None, tuple[str, ...]]:
    """One alternation for every name-like term (any case) and the display form of every
    word-like term (matched by ``_vendor_hits``)."""
    names = sorted({t.strip() for t in terms if t.strip() and not is_word_name(t)}, key=len)
    pattern = None
    if names:
        joined = "|".join(re.escape(n) for n in reversed(names))
        pattern = re.compile(rf"(?<!\w)(?:{joined})(?!\w)", re.I)
    words: dict[str, str] = {}
    for term in terms:
        key = term.strip().casefold()
        # the configured display name gives the vendor form; a lowercase key does not
        if key and is_word_name(term) and (key not in words or term.strip() != key):
            words[key] = term.strip()
    return pattern, tuple(words.values())


def digest_hits(text: str) -> tuple[int, ...]:
    """Offsets of words whose digest is forbidden."""
    forbidden = name_digests()
    return tuple(m.start() for m in _WORD.finditer(text) if word_digest(m.group()) in forbidden)


_INVISIBLE = dict.fromkeys(
    map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad\u200e\u200f\u202a\u202b\u202c\u202d\u202e")
)


def _folded(text: str) -> str:
    """The text in NFKC form without zero-width and bidi control characters (what a reader sees),
    so ``Para\u200bllel`` or a full-width ``Parallel`` is matched like the plain word."""
    if text.isascii():
        return text
    return unicodedata.normalize("NFKC", text).translate(_INVISIBLE)


def client_hits(text: str, extra_terms: Iterable[str] = ()) -> tuple[tuple[str, int], ...]:
    """Every hit as ``(kind, offset)``: ``denylist``, ``provider`` or ``name``."""
    extra = tuple(extra_terms)
    pattern, words = _term_patterns((*provider_terms(), *extra))
    hits = [("denylist", h.start) for h in scan_text(text)]
    # the text as written and as a reader sees it (offsets of the folded form are only a count)
    for seen in dict.fromkeys((text, _folded(text))):
        if pattern is not None:
            hits += [("provider", m.start()) for m in pattern.finditer(seen)]
        for word in words:
            hits += [("provider", offset) for offset in _vendor_hits(seen, word)]
    hits += [("name", offset) for offset in digest_hits(text)]
    return tuple(sorted(set(hits)))


@lru_cache(maxsize=1)
def _host_patterns() -> tuple[tuple[str, ...], re.Pattern[str] | None]:
    from documents.gate import provider_hosts

    hosts = tuple(provider_hosts())
    if not hosts:
        return hosts, None
    joined = "|".join(re.escape(h) for h in sorted(hosts, key=len, reverse=True))
    return hosts, re.compile(rf"(?<![\w-])(?:[\w-]+\.)*({joined})(?![\w-])", re.I)


def third_party_domain_hits(text: str) -> tuple[str, ...]:
    """Provider domains named in text, for the output-side and pre-delivery guards."""
    hosts, pattern = _host_patterns()
    if pattern is None or "." not in text:
        return ()
    found = {m.group(1).casefold() for m in pattern.finditer(text)}
    return tuple(host for host in hosts if host.casefold() in found)


@lru_cache(maxsize=1)
def _tool_pattern() -> tuple[dict[str, str], re.Pattern[str] | None]:
    from agents.skill_context import tool_names

    names = {n.casefold(): n for n in tool_names() if n.strip()}
    if not names:
        return names, None
    joined = "|".join(re.escape(n) for n in sorted(names.values(), key=len, reverse=True))
    return names, re.compile(rf"(?<!\w)(?:{joined})(?!\w)", re.I)


def tool_name_hits(text: str) -> tuple[tuple[str, int], ...]:
    """Remaining configured data-tool names after the output scrub, as (name, offset)."""
    names, pattern = _tool_pattern()
    if pattern is None:
        return ()
    return tuple(
        (names.get(m.group(0).casefold(), m.group(0)), m.start()) for m in pattern.finditer(text)
    )


def sanitize_client_value(value: object) -> object:
    """Scrub tool names in nested output, then fail closed on remaining client terms/domains."""
    if isinstance(value, str):
        cleaned, reason = _scrubbed(value)
        if reason is not None:
            raise GuardViolation("G9", reason)
        return cleaned
    if isinstance(value, Mapping):
        return {key: sanitize_client_value(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_client_value(nested) for nested in value]
    return value


# Texts longer than this are scanned each time, never held by the memo (a page cuts its cells
# to 2,000 characters before the scan; only the one-cell route sees a whole 32,000-character
# description, and holding those would pin megabytes).
MEMO_MAX_CHARS = 8192


def _scrubbed(text: str) -> tuple[str, str | None]:
    """The text with tool names scrubbed and the reason it must be withheld (None: clean).
    Memoised by text up to ``MEMO_MAX_CHARS``: the cells of a page viewed again (a sort, Show
    more rows) are not scanned again."""
    return _scrubbed_memo(text) if len(text) <= MEMO_MAX_CHARS else _scrub(text)


@lru_cache(maxsize=4096)
def _scrubbed_memo(text: str) -> tuple[str, str | None]:
    return _scrub(text)


def _scrub(text: str) -> tuple[str, str | None]:
    from agents.skill_context import strip_tool_names

    if third_party_domain_hits(text):
        return text, "third_party_domain"
    # the scrub is a pass per tool name: run it only on text that names one (one scan)
    cleaned = strip_tool_names(text) if tool_name_hits(text) else text
    if tool_name_hits(cleaned) or client_hits(cleaned):
        return cleaned, "client_output_leak"
    return cleaned, None


def scrub_client_text(text: str) -> tuple[str, bool]:
    """The withholding form of the guard for one cell: ``(text to show, withheld)``. Tool names
    are scrubbed; a remaining provider term, person name or provider domain withholds the cell
    (the caller shows a neutral marker) instead of failing the whole response."""
    cleaned, reason = _scrubbed(text)
    return (cleaned, False) if reason is None else (cleaned, True)


def archive_guard(text: str) -> tuple[str, bool]:
    """The guard every free-text field of a link-archive copy passes: the page guard (tool names
    scrubbed; a provider term, person name or provider domain withholds) and the platform
    denylist (broker names, credit figures). A refused field is blanked in the copy, never the
    whole copy (``account_intelligence.archive_records``)."""
    shown, withheld = scrub_client_text(text)
    return shown, withheld or bool(scan_text(shown))


# every archive writer that is given no guard uses this one (one write-time guard path)
AR.use_default_guard(archive_guard)


def assert_client_clean(texts: Iterable[str], extra_terms: Iterable[str] = ()) -> None:
    """Raise ``GuardViolation(G9)`` with a reason if client text contains a guarded term."""
    extra = tuple(extra_terms)
    values = tuple(texts)
    domains = sum(len(third_party_domain_hits(text)) for text in values)
    if domains:
        raise GuardViolation("G9", "third_party_domain", f"{domains} hit(s)")
    count = sum(len(tool_name_hits(text)) + len(client_hits(text, extra)) for text in values)
    if count:
        raise GuardViolation("G9", "client_output_leak", f"{count} hit(s)")
