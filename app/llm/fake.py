"""Deterministic offline provider for tests and dry runs of the benchmark harness.

It answers the extraction task by quoting sentences from the source pack in the prompt. Per-model
knobs drop facts and mis-cite URLs deterministically so the scorer can be shown to catch both.
Its output is never evidence about any real model.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from app.llm.provider import Completion, CompletionRequest

SOURCE_PACK_MARKER = "SOURCE_PACK_JSON:"
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class FakeBehaviour:
    recall: float = 1.0
    miscite_rate: float = 0.0


def _fraction(*parts: str) -> float:
    digest = hashlib.sha256("|".join(parts).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def _tokens(text: str) -> int:
    # Rough and stable: about four characters per token.
    return max(1, len(text) // 4)


class FakeProvider:
    name = "fake"

    def __init__(self, behaviours: dict[str, FakeBehaviour] | None = None) -> None:
        self.behaviours = behaviours or {}
        self.requests: list[CompletionRequest] = []

    def complete(self, request: CompletionRequest) -> Completion:
        self.requests.append(request)
        behaviour = self.behaviours.get(request.model, FakeBehaviour())
        text = json.dumps({"facts": self._extract(request, behaviour)})
        return Completion(
            model=request.model,
            text=text,
            input_tokens=_tokens(request.system) + _tokens(request.prompt),
            output_tokens=_tokens(text),
        )

    @staticmethod
    def _extract(request: CompletionRequest, b: FakeBehaviour) -> list[dict[str, str]]:
        _, _, payload = request.prompt.partition(SOURCE_PACK_MARKER)
        if not payload.strip():
            return []
        sources = json.loads(payload)
        urls = [s["url"] for s in sources]
        facts: list[dict[str, str]] = []
        for src in sources:
            sentences = [
                t.strip()
                for t in _SENTENCE.split(src["text"])
                if t.strip() and not t.startswith("SYNTHETIC TEXT")
            ]
            for index, sentence in enumerate(sentences):
                if _fraction(request.model, "keep", sentence) >= b.recall:
                    continue
                url = src["url"]
                if len(urls) > 1 and _fraction(request.model, "cite", sentence) < b.miscite_rate:
                    url = next(u for u in urls if u != src["url"])
                facts.append(
                    {
                        "row_id": src["row_ids"][index % len(src["row_ids"])],
                        "statement": sentence,
                        "grade": "Confirmed" if src["primary"] else "Lead",
                        "url": url,
                        "supporting_sentence": sentence,
                    }
                )
        return facts
