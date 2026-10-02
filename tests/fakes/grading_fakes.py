"""Synthetic fakes for the grading ports. Every fixture here is synthetic."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from account_intelligence.grading.errors import NotConfigured
from account_intelligence.grading.ports import FetchRecord, RunInfo, RunSource


@dataclass
class FakeSources:
    items: list[RunSource] = field(default_factory=list)

    async def sources(self, run: RunInfo) -> Sequence[RunSource]:
        return list(self.items)


@dataclass
class FakeLedger:
    items: dict[str, FetchRecord] = field(default_factory=dict)

    async def fetches(self, run: RunInfo) -> Mapping[str, FetchRecord]:
        return dict(self.items)


@dataclass
class ScriptedJudge:
    """Returns scripted outputs in order; ``raise`` entries raise NotConfigured."""

    script: list[Any]
    model_task: str = "verification"
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def judge(self, claim: str, sentence: str) -> Any:
        self.calls.append((claim, sentence))
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if item == "raise":
            raise NotConfigured("judge adapter not configured")
        return item


@dataclass
class FakeLlm:
    """A scripted generator: returns the proposals it was given, verbatim (lying or not)."""

    proposals: list[Any]

    def propose(self) -> list[Any]:
        return list(self.proposals)
