"""Whole-pipeline golden run against the hand-written oracle (spec 9.1, 9.7), stage idempotency and
both people-source modes (spec 9.5)."""

from __future__ import annotations

import pytest

from tests.fakes.people import Corpus, FakeRepo, oracle
from tests.people.kit import Kit, by_record


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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
