"""Figures checked in code (V5): every number in a narrative sentence must be one the number
registry holds for the rows the sentence cites, with its base. A model may only point at figure
mentions that patterns cannot find; comparison is always code (``exports.numbers``, one
implementation of number extraction).

GUARANTEE(test_figures_are_checked_against_the_registry_in_code): V5.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from exports.numbers import normal_form, numbers_in


@dataclass(frozen=True)
class FigureFinding:
    """One figure in one sentence."""

    sentence_key: str
    figure: str
    outcome: str  # figure_ok, figure_mismatch, figure_unregistered


def check_sentence(
    sentence_key: str,
    sentence: str,
    registry: Mapping[str, tuple[int, str]],
    cited: Sequence[str],
) -> list[FigureFinding]:
    """Every number of ``sentence`` against the registry values of the cited keys."""
    allowed = {normal_form(str(registry[k][0])) for k in cited if k in registry}
    known = {normal_form(str(v)) for v, _ in registry.values()}
    out: list[FigureFinding] = []
    for figure in numbers_in(sentence):
        form = normal_form(figure)
        if form in allowed:
            out.append(FigureFinding(sentence_key, figure, "figure_ok"))
        elif form in known:
            out.append(FigureFinding(sentence_key, figure, "figure_mismatch"))
        else:
            out.append(FigureFinding(sentence_key, figure, "figure_unregistered"))
    return out
