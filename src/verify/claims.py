"""The claims verifier (V1, V2, V4, V8, V9): a blind, fresh-context re-read of the stored source of
every Confirmed claim.

* **Fresh context (V1):** one new request per claim with no history and no tools; the input is the
  stored source text (bounded) and an attribute question; never the generator's reasoning.
* **Blind (V2):** the verifier is NOT shown the claimed value. It returns an answer and a verbatim
  quote; code checks the quote is in the stored source (whitespace and case normalised), that the
  answer is supported by the quote, normalises the answer with the claim's own normaliser and
  compares it with the claim's normalised value. Agreement is ``confirmed``; anything else is a
  disagreement (``value_mismatch`` or ``not_found``).
* **Verified or capped (V4):** an unavailable verifier (any typed model error, schema failure,
  budget), a judge of the generator's family (``not_independent``) or an unreadable source yields
  a non-confirmed outcome, so the claim is capped and never delivered as Confirmed.
* **Idempotent (V8):** the result is keyed by claim, source text hash, prompt hash and verifier;
  the router replays a stored call at no cost.
* **Bounded (V9):** at most ``max_claims_per_run`` claims, High rows first; the rest are capped.

GUARANTEE(test_a_verifier_that_echoes_or_fabricates_fails_closed): V2 lying scenarios.
GUARANTEE(test_an_unavailable_or_same_family_verifier_caps_the_claim): V4.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from account_intelligence.grading.config import Normaliser
from account_intelligence.grading.spans import casefold_text, normalise_value, normalise_ws


class Reread(BaseModel):
    """The verifier's only output."""

    model_config = ConfigDict(extra="forbid")
    answer: str | None
    quote: str | None


@dataclass(frozen=True)
class ClaimToVerify:
    """What code knows about one Confirmed claim (the value never reaches the verifier)."""

    key: str
    attribute_key: str
    normalised_value: str
    normaliser: Normaliser
    source_text: str | None  # None: the stored source could not be read
    high: bool = False


@dataclass(frozen=True)
class Finding:
    """One verification result."""

    key: str
    outcome: str
    source_text_hash: str
    quote_hash: str | None


Ask = Callable[[str, str], Awaitable[Reread]]  # (question, source_text) -> Reread; raises typed


def question(attribute_key: str) -> str:
    """The attribute question (from the key only; never the value)."""
    parts = attribute_key.replace("_", " ").split(".")
    return f"What does the source state for: {' / '.join(parts)}?"


def text_hash(text: str | None) -> str:
    """sha256 of the stored source text ("unreadable" when absent)."""
    return hashlib.sha256((text or "").encode()).hexdigest() if text else "unreadable"


def judge_reread(claim: ClaimToVerify, reread: Reread) -> str:
    """The outcome of one re-read, decided in code (pure)."""
    if claim.source_text is None:
        return "unreadable"
    if not reread.answer or not reread.quote:
        return "not_found"
    quote = casefold_text(normalise_ws(reread.quote))
    source = casefold_text(normalise_ws(claim.source_text))
    if not quote or quote not in source:
        return "not_found"  # a fabricated quote proves nothing
    answer = normalise_value(reread.answer, claim.normaliser)
    if casefold_text(normalise_ws(answer)) not in casefold_text(
        normalise_ws(normalise_value(reread.quote, claim.normaliser))
    ):
        return "not_found"  # the answer is not in its own quote
    if answer != claim.normalised_value:
        return "value_mismatch"
    return "confirmed"


async def verify(
    claims: Sequence[ClaimToVerify],
    ask: Ask,
    max_claims: int,
    max_chars: int,
    independent: bool,
) -> list[Finding]:
    """One finding per claim (see the module docstring); never raises for a model failure."""
    # High rows first (the budget order), then grouped by source so the re-reads of one source
    # run back to back while its cached block is warm
    ordered = sorted(claims, key=lambda c: (not c.high, text_hash(c.source_text), c.key))
    findings: list[Finding] = []
    # LOOP(verification batch): cap=max_claims_per_run claims, one re-read each; exit=converged,
    # budget (the rest capped), unavailable (that claim capped)
    for i, claim in enumerate(ordered):
        h = text_hash(claim.source_text)
        if i >= max_claims:
            findings.append(Finding(claim.key, "unavailable", h, None))
            continue
        if not independent:
            findings.append(Finding(claim.key, "not_independent", h, None))
            continue
        if claim.source_text is None:
            findings.append(Finding(claim.key, "unreadable", h, None))
            continue
        try:
            reread = await ask(question(claim.attribute_key), claim.source_text[:max_chars])
        except Exception:  # any typed model failure: the claim is capped, never confirmed (V4)
            findings.append(Finding(claim.key, "unavailable", h, None))
            continue
        quote_hash = hashlib.sha256(reread.quote.encode()).hexdigest() if reread.quote else None
        findings.append(Finding(claim.key, judge_reread(claim, reread), h, quote_hash))
    return findings
