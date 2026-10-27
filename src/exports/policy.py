"""The B-rules as pure functions (spec 3). No I/O, no clock, no environment.

Checks return ``Violation`` tuples so a build can list EVERY problem at once (B15); the builder
turns them into the typed errors. One implementation per rule: the statement policy and display
labels are the grading engine's (``grading.product``), the leak scan is the platform's client
output scanner (``safety.client_output``); nothing here re-implements either.

* B7  ``fact_violations``: a fact whose grade, primary-read flag or statement flag disagrees
  with the vocabulary or the statement policy is refused at read time.
* B8  ``visibility_violations``: any non-public row is a leak (the reader filters too).
* B9  ``leak_hits``: denylist, provider codes, internal hashes and the run's forbidden terms;
  ``guarded_names``: excluded, review, unresolved, merged, erased and internal-only people.
* B11 ``sanitise``, ``is_formula_like``, ``split_for_cell``: safe cell and run content.
* D-24 ``authored_violations``: banned dashes, stale phrases, abbreviations on first use and
  partner framings in authored prose only; ``quote_withheld``: a restricted term inside a
  verbatim quote withholds the sentence (the quote is never altered).
GUARANTEE(test_b9_forbidden_content_in_a_row)
GUARANTEE(test_b9_restricted_term_in_a_verbatim_quote_is_withheld_counted_listed)
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from account_intelligence.grading.config import GradingConfig
from account_intelligence.grading.product import statement_policy
from account_intelligence.safety.client_output import scan_text
from exports.config import ContentRules, provider_codes
from exports.content import FactRow

PRODUCT_GRADES = ("confirmed", "probable", "hypothesis", "lead", "internal")

# XML 1.0 forbids C0 controls except tab, newline and carriage return, lone surrogates and the
# two non-characters; C1 controls and DEL are legal but never meaningful in a client file.
_UNSAFE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ud800-\udfff￾￿]")
# A cell whose text starts with one of these could execute when a consumer re-parses it.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
# Internal identifiers that must never reach a client file: the configured provider codes
# (config/providers.yaml), never any ``p_`` token (a posting that said "p_value" failed a build)
_CODES = "|".join(re.escape(c) for c in provider_codes())
_PROVIDER_CODE = re.compile(rf"(?<![A-Za-z0-9_])(?:{_CODES})(?![A-Za-z0-9_])")
_HEX64 = re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{64}(?![0-9A-Za-z])")
_SPACED_HYPHEN = re.compile(r"\S\s+-\s+\S")


@dataclass(frozen=True)
class Violation:
    """One broken rule: a stable code, where it happened and the row id or check involved.
    Never carries the offending text."""

    code: str
    where: str
    item: str


def sanitise(text: str) -> str:
    """Remove XML-invalid and control characters (B11). Keeps tab and newline, right-to-left
    text and emoji; never reorders, trims or truncates."""
    return _UNSAFE.sub("", text)


def is_formula_like(text: str) -> bool:
    """True when the text would start a spreadsheet formula (written as a string cell, B11)."""
    return text.startswith(_FORMULA_START)


def split_for_cell(text: str, limit: int) -> tuple[str, ...]:
    """Split text into parts of at most ``limit`` characters (explicit, counted continuation;
    never silent truncation). Joining the parts gives the text back exactly."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    if len(text) <= limit:
        return (text,)
    return tuple(text[i : i + limit] for i in range(0, len(text), limit))


def authored_violations(
    text: str, rules: ContentRules, where: str, *, is_partner: bool
) -> tuple[Violation, ...]:
    """D-24 rules on authored prose (never applied to verbatim quotes)."""
    found: list[Violation] = []
    if any(ch in text for ch in rules.banned_dashes.characters):
        found.append(Violation("banned_dash", where, "dash character"))
    if rules.banned_dashes.spaced_hyphen_is_punctuation and _SPACED_HYPHEN.search(text):
        found.append(Violation("banned_dash", where, "spaced hyphen"))
    lowered = text.casefold()
    for phrase in rules.stale_phrases.phrases:
        if phrase.casefold() in lowered:
            found.append(Violation("stale_phrase", where, phrase))
    for entry in rules.abbreviations.entries:
        first = re.search(rf"(?<!\w){re.escape(entry.abbr)}(?!\w)", text)
        if first and entry.expansion.casefold() not in lowered[: first.start()]:
            found.append(Violation("abbreviation_first_use", where, entry.abbr))
    if is_partner:
        for framing in rules.partner_rule.framings:
            if framing.casefold() in lowered:
                found.append(Violation("partner_framing", where, framing))
    return tuple(found)


def quote_withheld(sentence: str, restricted: Iterable[str]) -> bool:
    """True when a verbatim quote contains a restricted term: the sentence is withheld, the link
    kept, the withholding counted (D-24). The quote itself is never altered."""
    return bool(leak_hits(sentence, restricted))


def leak_hits(text: str, extra_terms: Iterable[str] = ()) -> tuple[str, ...]:
    """Patterns that fire on ``text``: the shared denylist, the run's literal forbidden terms,
    provider codes and 64-digit internal hashes (call references). Patterns only, never text."""
    hits = [h.pattern for h in scan_text(text, tuple(extra_terms))]
    if _PROVIDER_CODE.search(text):
        hits.append("provider_code")
    if _HEX64.search(text):
        hits.append("internal_hash")
    return tuple(hits)


def denied_link(url: str) -> bool:
    """True when a link names a data tool by the client-output denylist (the third-party scan's
    own patterns, spec 12): a client file may neither show nor link it."""
    return bool(scan_text(url))


def fact_violations(fact: FactRow, grading: GradingConfig) -> tuple[Violation, ...]:
    """B7 at read time: the stored grade, flags and statement flag must agree with the grading
    vocabulary and the statement policy, or the fact is refused."""
    where = f"fact {fact.id}"
    if fact.product_grade not in PRODUCT_GRADES:
        return (Violation("grade_vocabulary", where, fact.product_grade),)
    found: list[Violation] = []
    if fact.product_grade == "confirmed" and not fact.read_in_primary_this_run:
        found.append(Violation("confirmed_without_primary_read", where, fact.id))
    if fact.may_state_to_prospect != statement_policy(fact.product_grade, grading):  # type: ignore[arg-type]
        found.append(Violation("statement_flag_disagrees", where, fact.id))
    needs_question = fact.product_grade in grading.requires_question or (
        "conflicting" in fact.flags
    )
    if needs_question and not (fact.confirming_question or "").strip():
        found.append(Violation("question_missing", where, fact.id))
    if fact.product_grade == "internal":
        found.append(Violation("internal_in_public_file", where, fact.id))
    return tuple(found)


def visibility_violations(rows: Iterable[tuple[str, str]], where: str) -> tuple[Violation, ...]:
    """B8: ``(row_id, visibility)`` pairs; anything not public is a leak."""
    return tuple(Violation("non_public_row", where, rid) for rid, vis in rows if vis != "public")


def guarded_names(names: Sequence[tuple[str, bool]]) -> tuple[str, ...]:
    """Names that must never appear in a client file (B9): every named person NOT on the list
    (excluded, review, unresolved, merged, out of scope, erased or internal-only). A name that is
    also a listed person's name is not guarded, since it would block the listed person. Sorted,
    distinct."""
    listed = {n.strip().casefold() for n, on_list in names if on_list and n.strip()}
    guarded = {
        n.strip()
        for n, on_list in names
        if not on_list and n.strip() and n.strip().casefold() not in listed
    }
    return tuple(sorted(guarded, key=lambda n: (n.casefold(), n)))


def restricted_terms(terms: Iterable[str], attribution: str) -> tuple[str, ...]:
    """The literal terms a client file may not carry, cleaned (B9): blank terms go; a term of
    three characters or fewer goes (it matches too much text to mean another account); a term
    that is the attribution or one of its words goes (a client whose own name is the platform's,
    "6th", would otherwise refuse every file for every other account, as on staging
    2026-10-16). Sorted, distinct."""
    own = {attribution.strip().casefold(), *attribution.casefold().split()}
    kept = {
        t.strip()
        for t in terms
        if t and t.strip() and len(t.strip()) > 3 and t.strip().casefold() not in own
    }
    return tuple(sorted(kept, key=lambda t: (t.casefold(), t)))
