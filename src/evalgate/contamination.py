"""Contamination check (E2): no golden label value and no golden source text may appear in a
prompt, a few-shot example or a test fixture the model sees. A hit fails the eval.

GUARANTEE(test_a_label_planted_in_a_prompt_fails_the_contamination_check): E2.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

MIN_LEN = 12  # shorter strings collide by chance (proposal)


def needles(values: Iterable[str], min_len: int = MIN_LEN) -> frozenset[str]:
    """Normalised label values and source sentences long enough to be distinctive."""
    return frozenset(" ".join(v.casefold().split()) for v in values if len(v.strip()) >= min_len)


def scan(paths: Sequence[Path], values: Iterable[str]) -> list[tuple[str, str]]:
    """``(file, needle)`` for every needle found in a scanned file."""
    targets = needles(values)
    hits: list[tuple[str, str]] = []
    for path in sorted(paths):
        text = " ".join(path.read_text(errors="replace").casefold().split())
        hits.extend((str(path), n) for n in sorted(targets) if n in text)
    return hits
