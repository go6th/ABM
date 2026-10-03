"""Whole-pipeline golden run against the hand-written oracle (spec 9.1, 9.7), stage idempotency and
both people-source modes (spec 9.5)."""

from __future__ import annotations

from typing import Any

import pytest

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
    oracle,
)

CANARY = "CANARY-7f3a9e"
pytestmark = pytest.mark.asyncio


def is_hostile(text: str) -> bool:
    """The grading layer's one hostile-content check (G14), reused."""
    try:
        check_hostile(text, load_grading_config())
    except HostileContent:
        return True
    return False


def markers() -> tuple[str, ...]:
    return tuple(load_grading_config().spans.instruction_markers)


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
            cfg or load_people_config(),
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
            "00000000-0000-0000-0000-000000000001",
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


async def test_golden_run_matches_the_oracle() -> None:
    kit = Kit(Corpus())
    result = await kit.run()
    expect = oracle()
    rows = by_record(result)
    for group in expect["groups"]:
        members = [rows[m] for m in group["members"]]
        with_url = sorted((p for p in members if p.profile_url), key=lambda p: p.person_key)
        head = with_url[0] if with_url else members[0]
        for p in members:
            if p is head:
                c = p.classification
                assert (c.status, c.reason) == (group["status"], group.get("reason")), group
                if group["status"] == "in_scope":
                    assert (c.level, list(c.lanes), c.priority) == (
                        group["level"],
                        group["lanes"],
                        group["priority"],
                    ), group
                assert p.possible_duplicate is bool(group.get("possible_duplicate")), group
                assert p.internal_only is bool(group.get("internal_only")), group
                assert p.merged_into is None
            else:
                assert p.status == "merged" and p.merged_into == head.person_key, group
    assert len(rows) == sum(len(g["members"]) for g in expect["groups"])
    assert result.invariant_error is None and result.counts is not None
    got = result.counts.model_dump()
    for key, value in expect["counts"].items():
        assert got[key] == value, key
    assert {k: v for k, v in result.drops.items() if v} == expect["drops"]
    assert len(result.findings) == expect["findings"]
    assert len(result.claims) == expect["claims"]
    assert result.lanes_not_searched == expect["lanes_not_searched"]
    for key, value in expect["exits"].items():
        assert result.exit_reasons[key] == value, key
    for ev in result.evidence:
        want = expect["evidence"][ev.row_key]
        assert (len(ev.call_refs), ev.expected_calls, ev.cap_hit) == (
            want["refs"],
            want["expected_calls"],
            want["cap_hit"],
        ), ev.row_key
    assert [e["row"] for e in kit.rec.evidence] == list(expect["evidence"])
    assert len(kit.rec.claims) == expect["claims"]
    in_scope_urls = {p.profile_url for p in result.people if p.status == "in_scope"}
    assert set(result.source_codes) == in_scope_urls


async def test_contact_fields_never_reach_rows_or_record() -> None:
    from people.pipeline import result_to_json

    kit = Kit(Corpus())
    result = await kit.run()
    import json

    dumped = json.dumps(result_to_json(result))
    assert "jane.doe@synthetic.example" not in dumped and "555 0100" not in dumped
    assert "mei@synthetic.example" not in dumped
    jane = next(p for p in result.profiles if p.profile_url.endswith("/jane-doe"))
    assert jane.fields_dropped == 2


async def test_stage_is_idempotent_and_reentry_makes_no_paid_call() -> None:
    corpus = Corpus()
    kit = Kit(corpus)
    first = await kit.run()
    paid = kit.ledger.paid
    again = await kit.run()
    assert again.replayed and again.calls_made == 0 and kit.ledger.paid == paid
    assert again.people == first.people and again.counts == first.counts
    # a fresh pass over the same store and ledger reaches the same rows with no new paid call
    fresh = Kit(corpus, repo=FakeRepo())
    fresh.repo.pages = kit.repo.pages
    fresh.repo.profiles = kit.repo.profiles
    fresh.repo.posts = kit.repo.posts
    replay = await fresh.run()
    assert [(p.person_key, p.status, p.classification) for p in replay.people] == [
        (p.person_key, p.status, p.classification) for p in first.people
    ]
    assert replay.counts == first.counts
    assert kit.ledger.paid == paid  # the failing profile is tried again; a failed call is not paid
    assert {p.profile_state for p in replay.people if p.status == "in_scope"} == {
        "reused",
        "failed",
    }


async def test_a_person_already_pulled_for_the_account_is_not_pulled_again() -> None:
    corpus = Corpus()
    kit = Kit(corpus)
    await kit.run()
    later = Kit(Corpus(), repo=FakeRepo())
    later.repo.profiles = dict(kit.repo.profiles)  # an earlier run stored them
    result = await later.run()
    assert later.profiles.pulled == []  # only the failing profile is tried again
    reused = [p for p in result.people if p.profile_state == "reused"]
    assert len(reused) == 6


async def test_both_people_source_modes_reach_the_oracle_statuses() -> None:
    """Dual mode: the association source is another provider (internal-only); the merged list and
    statuses do not depend on which provider answered, only the distribution flag does."""
    single = await Kit(Corpus()).run()
    corpus = Corpus()
    dual_search = dict(corpus.data["search"])
    for key, entry in list(dual_search.items()):
        if key.startswith("title:") and "pages" in entry:
            dual_search[key] = {
                **entry,
                "pages": [
                    {**page, "records": [{**r, "provider": "p_org"} for r in page["records"]]}
                    for page in entry["pages"]
                ],
            }
    dual = await Kit(corpus, search=dual_search).run()
    assert [(p.source_record_id, p.status, p.classification.reason) for p in single.people] == [
        (p.source_record_id, p.status, p.classification.reason) for p in dual.people
    ]
    assert single.counts is not None and dual.counts is not None
    assert single.counts.in_scope == dual.counts.in_scope
    internal = {
        p.source_record_id for p in dual.people if p.status == "in_scope" and p.internal_only
    }
    omar = {"r05", "r18", "r19"}  # one member came from the resolver (another provider)
    assert all(
        p.internal_only != (p.source_record_id in omar)
        for p in dual.people
        if p.status == "in_scope"
    )
    assert internal and not internal & omar
