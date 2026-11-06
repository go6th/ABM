"""Parked from tests/verify/test_verify.py with src/verify/narrative.py (ADR-0136)."""

from __future__ import annotations

from typing import Any

import pytest

from verify import narrative
from verify.config import load_verify_config


@pytest.mark.asyncio
async def test_narrative_verdicts_never_insert_model_text() -> None:
    actions = load_verify_config().narrative_actions
    rows = {"r1": "Salesforce Service Cloud (Confirmed)"}
    sentences = [
        narrative.Sentence(f"s{i}", f"Sentence {i}.", ("r1",), "the CRM platform") for i in range(4)
    ] + [narrative.Sentence("s9", "Uncited.", (), "x")]
    verdicts = iter(["supported", "overstated", "unsupported", "contradicted"])

    async def ask(text: str, cited: Any) -> narrative.SentenceVerdict:
        assert set(cited) == {"r1"}  # only its cited rows
        return narrative.SentenceVerdict(verdict=next(verdicts))

    out = await narrative.reread(sentences, rows, ask, actions)
    assert [(a.action, a.text) for a in out] == [
        ("keep", "Sentence 0."),
        ("rephrase", narrative.TEMPLATE.format(subject="the CRM platform")),
        ("remove", None),
        ("remove", None),
        ("remove", None),
    ]

    async def down(text: str, cited: Any) -> narrative.SentenceVerdict:
        raise RuntimeError("no verifier")

    assert (await narrative.reread(sentences[:1], rows, down, actions))[0].action == "remove"
