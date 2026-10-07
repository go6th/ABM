"""Synthetic evaluation datasets for the CI/CD gate.

Human-curated question sets do not scale; the models
themselves generate the corpus. The generation rules are
enforced here, deterministically, so a synthetic set that
would poison the eval gate (self-referential phrasing,
PII leakage, unanswerable questions) never reaches the
pipeline — the generator proposes, this module disposes.

Scenario coverage is explicit: happy paths establish the
baseline, edge cases stress multi-document reasoning, and
adversarial cases validate the refusal metrics. The
diversity selector maximises topical spread with a greedy
max-min pass over embedding similarity (the blueprint's
Diversity agent, in pure code).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from account_intelligence.rag.cache import cosine_similarity

Scenario = Literal["happy_path", "edge_case", "adversarial"]

_SELF_REFERENCE = (
    "based on the provided",
    "in the context above",
    "the passage states",
    "according to the document",
    "the text says",
    "as mentioned earlier",
    "given the context",
)
_PII_PATTERNS = (
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),  # email
    re.compile(r"\b(?:\+?\d[\d\s()-]{6,}\d)\b"),  # phone
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # SSN shape
)


@dataclass(frozen=True)
class SyntheticExample:
    """One generated test case with its scenario and ground truth."""

    question: str
    answer: str
    context: str
    scenario: Scenario


def is_self_referential(question: str) -> bool:
    """True when the question leans on the prompt, not the data.

    A question that says "based on the provided text" only
    works for the generation prompt — it cannot be posed to
    a real user and leaks the evaluation setup into the set.
    """
    folded = question.casefold()
    return any(phrase in folded for phrase in _SELF_REFERENCE)


def contains_pii(text: str) -> bool:
    """True when ``text`` carries an email, phone or SSN shape."""
    return any(pattern.search(text) for pattern in _PII_PATTERNS)


def is_answerable(example: SyntheticExample) -> bool:
    """True when the answer's content terms appear in the context.

    A question whose ground-truth answer shares no content
    with the context is unanswerable by construction —
    including it would train the gate to expect hallucinations.
    """
    from account_intelligence.rag.reflection import content_terms

    terms = content_terms(example.answer)
    if not terms:
        return False
    context_terms = set(example.context.casefold().split())
    return all(term in context_terms for term in terms)


def validate(example: SyntheticExample) -> tuple[str, ...]:
    """Every rule ``example`` violates (empty means it is clean)."""
    violations: list[str] = []
    if is_self_referential(example.question):
        violations.append("self_referential")
    if contains_pii(example.question) or contains_pii(example.answer):
        violations.append("pii")
    if not is_answerable(example):
        violations.append("unanswerable")
    if not example.question.strip():
        violations.append("empty_question")
    return tuple(violations)


def filter_valid(
    examples: Sequence[SyntheticExample],
) -> tuple[SyntheticExample, ...]:
    """Only examples that violate no rule."""
    return tuple(example for example in examples if not validate(example))


def scenario_balance(
    examples: Sequence[SyntheticExample],
) -> dict[str, int]:
    """Count of examples per scenario (the coverage matrix)."""
    counts: dict[str, int] = {}
    for example in examples:
        counts[example.scenario] = counts.get(example.scenario, 0) + 1
    return counts


def select_diverse(
    examples: Sequence[SyntheticExample],
    embeddings: Sequence[Sequence[float]],
    *,
    count: int,
) -> tuple[SyntheticExample, ...]:
    """Greedy max-min selection for topical coverage.

    The first pick is the first valid example; every next
    pick is the example whose minimum similarity to the
    already-picked set is smallest — the farthest point
    sampling of the blueprint's Diversity agent.
    """
    if count < 0:
        raise ValueError("count cannot be negative")
    if len(examples) != len(embeddings):
        raise ValueError("examples and embeddings must align")
    if count == 0 or not examples:
        return ()
    picked: list[int] = [0]
    remaining = set(range(1, len(examples)))
    while len(picked) < count and remaining:
        best: int | None = None
        best_score = float("inf")
        for candidate in sorted(remaining):
            similarity = max(
                cosine_similarity(embeddings[candidate], embeddings[chosen]) for chosen in picked
            )
            if similarity < best_score:
                best_score = similarity
                best = candidate
        if best is None:
            break
        picked.append(best)
        remaining.discard(best)
    return tuple(examples[index] for index in picked)
