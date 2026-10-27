"""Shared pipeline kit for the people tests: the corpus fakes wired into a pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from account_intelligence.grading.config import load_grading_config
from account_intelligence.grading.errors import HostileContent
from account_intelligence.grading.spans import check_hostile
from people.config import PeopleConfig, load_people_config
from people.pipeline import PeoplePipeline, PipelineResult
from people.stage import run_people
from tests.fakes.people import (
    Corpus,
    FakeCosts,
    FakeLlm,
    FakeMentions,
    FakePosts,
    FakeProfiles,
    FakeRepo,
    FakeResolver,
    FakeRunInput,
    FakeSearch,
    Recorder,
)

RUN_ID = "00000000-0000-0000-0000-000000000001"

CANARY = "CANARY-7f3a9e"


def is_hostile(text: str) -> bool:
    """The grading layer's one hostile-content check (G14), reused."""
    try:
        check_hostile(text, load_grading_config())
    except HostileContent:
        return True
    return False


def markers() -> tuple[str, ...]:
    return tuple(load_grading_config().spans.instruction_markers)


# the frozen 2026-10-02 lexicon the golden oracle was written against (tests/people/conftest.py)
_FROZEN = Path(__file__).resolve().parents[1] / "fixtures/synthetic/jobs/config"


class Kit:
    """A pipeline over the corpus fakes, with handles on every fake."""

    def __init__(
        self,
        corpus: Corpus,
        *,
        cfg: PeopleConfig | None = None,
        repo: FakeRepo | None = None,
        extractor: FakeLlm | None = None,
        post_cost: float | None = 0.02,
        profile_provider: str = "p_people",
        search: dict[str, Any] | None = None,
        out_of_time: Any = None,
        mentions_configured: bool = True,
        activity_known: bool = True,
    ) -> None:
        d = corpus.data
        self.corpus = corpus
        self.ledger = corpus.ledger
        self.search = FakeSearch(search if search is not None else d["search"], self.ledger)
        self.resolver = FakeResolver(d["resolver"], self.ledger)
        self.profiles = FakeProfiles(d["profiles"], self.ledger, profile_provider)
        self.posts = FakePosts(d["posts"], self.ledger, post_cost, activity_known)
        self.llm = extractor or FakeLlm(d["extractor"])
        self.repo = repo or FakeRepo()
        self.rec = Recorder()
        self.pipeline = PeoplePipeline(
            cfg or load_people_config(_FROZEN),
            self.search,
            FakeMentions(corpus.mentions(), mentions_configured),
            self.resolver,
            self.profiles,
            self.posts,
            FakeCosts(self.ledger),
            self.llm,
            self.repo,
            canary=CANARY,
            hostile_markers=markers(),
            is_hostile=is_hostile,
            **({"out_of_time": out_of_time} if out_of_time else {}),
        )

    async def run(self, run_input: FakeRunInput | None = None) -> PipelineResult:
        return await run_people(
            RUN_ID,
            self.corpus.account,
            self.pipeline,
            self.repo,
            self.rec,
            self.rec,
            self.rec,
            run_input or FakeRunInput(self.corpus),
        )


def by_record(result: PipelineResult) -> dict[str, Any]:
    out = {}
    for p in result.people:
        label = p.source_record_id
        if p.origin == "mention" and p.source_record_id.startswith("mention:"):
            label = f"mention:{p.name}"
        out[label] = p
    return out
