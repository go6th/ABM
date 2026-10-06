"""Narrative reread (V6): per sentence a verdict against its cited rows only; code applies it.

``supported`` keeps the sentence; ``overstated`` replaces it with the grade-appropriate phrasing
by template (never model text); ``unsupported`` and ``contradicted`` remove it from the build input.
Every action is returned for logging. The model never writes replacement text.

GUARANTEE(test_narrative_verdicts_never_insert_model_text): V6.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

Verdict = Literal["supported", "overstated", "unsupported", "contradicted"]
TEMPLATE = "The available sources suggest this; it has not been confirmed: {subject}."


class SentenceVerdict(BaseModel):
    """The verifier's only output for one sentence."""

    model_config = ConfigDict(extra="forbid")
    verdict: Verdict


@dataclass(frozen=True)
class Sentence:
    """A narrative sentence, the rows it cites and a short subject for the template."""

    key: str
    text: str
    cited_rows: tuple[str, ...]
    subject: str


@dataclass(frozen=True)
class SentenceAction:
    """What code did with one sentence."""

    key: str
    verdict: str
    action: str  # keep, rephrase, remove
    text: str | None  # the text kept or the template; None when removed


Ask = Callable[[str, Mapping[str, str]], Awaitable[SentenceVerdict]]


def apply(sentence: Sentence, verdict: str, actions: Mapping[str, str]) -> SentenceAction:
    """The code action for a verdict (pure)."""
    action = actions[verdict]
    if action == "keep":
        return SentenceAction(sentence.key, verdict, "keep", sentence.text)
    if action == "rephrase":
        return SentenceAction(
            sentence.key, verdict, "rephrase", TEMPLATE.format(subject=sentence.subject)
        )
    return SentenceAction(sentence.key, verdict, "remove", None)


async def reread(
    sentences: Sequence[Sentence],
    rows: Mapping[str, str],
    ask: Ask,
    actions: Mapping[str, str],
) -> list[SentenceAction]:
    """Each sentence against its cited rows only; an unavailable verifier removes the sentence
    (it cannot be shown to be supported)."""
    out: list[SentenceAction] = []
    # LOOP(narrative reread): cap=one verdict per sentence; exit=all sentences decided
    for s in sentences:
        cited = {k: rows[k] for k in s.cited_rows if k in rows}
        if not cited:
            out.append(apply(s, "unsupported", actions))
            continue
        try:
            verdict = (await ask(s.text, cited)).verdict
        except Exception:
            verdict = "unsupported"
        out.append(apply(s, verdict, actions))
    return out
