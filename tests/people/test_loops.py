"""Bounded loops (spec 7): paging caps, no-progress, budget and deadline exits, gap loop, mention
and profile caps, and a hypothesis check that the pipeline terminates within every cap."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from people.config import PeopleConfig, load_people_config
from people.models import Mention, PersonPage, RawPerson
from people.ports import SourceUnavailable
from people.search import SearchSpec, page_source, plan_title_searches, titles_for
from tests.fakes.people import Corpus, FakeRepo
from tests.people.kit import Kit


def _cfg(**caps: float) -> PeopleConfig:
    base = load_people_config()
    return replace(base, caps={**base.caps, **caps})


def _rec(i: int, url: bool = True) -> RawPerson:
    return RawPerson(
        source_record_id=f"s{i}",
        origin="title_search",
        name=f"Person {i}",
        title="Director",
        profile_url=f"https://www.linkedin.com/in/p{i}" if url else None,
    )


class Script:
    """Pages answered by cursor; ``fail_at`` raises a source failure on that call."""

    def __init__(
        self, pages: list[Any], fail_at: dict[int, SourceUnavailable] | None = None
    ) -> None:
        self.pages, self.fail_at, self.n = pages, fail_at or {}, 0

    async def __call__(self, cursor: str | None) -> PersonPage:
        n = self.n
        self.n += 1
        if n in self.fail_at:
            raise self.fail_at[n]
        return self.pages[min(n, len(self.pages) - 1)]


async def _noop(*_: Any) -> None:
    return None


async def _page(
    script: Script,
    cfg: PeopleConfig,
    *,
    people_left: int = 10_000,
    late: bool = False,
    stored: list[Any] | None = None,
) -> Any:
    seen_people: set[str] = set()
    return await page_source(
        SearchSpec("title:salesforce:1", "salesforce", 1, ("x",)),
        script,
        _noop,
        cfg,
        stored=stored or [],
        seen_records=set(),
        seen_people=seen_people,
        people_left=lambda: people_left - len(seen_people),
        out_of_time=lambda: late,
    )


@pytest.mark.asyncio
async def test_endless_cursor_stops_at_the_page_cap() -> None:
    pages = [
        PersonPage(records=(_rec(i),), next_cursor=f"c{i + 1}", call_ref=f"r{i}") for i in range(50)
    ]
    out = await _page(Script(pages), _cfg(max_pages_per_source=4))
    assert (out.pages, out.exit_reason, out.cap_hit) == (4, "cap_reached", True)


@pytest.mark.asyncio
async def test_people_cap_deadline_budget_and_failure_exits() -> None:
    pages = [
        PersonPage(records=(_rec(i), _rec(i + 100)), next_cursor=f"c{i + 1}", call_ref=f"r{i}")
        for i in range(9)
    ]
    out = await _page(Script(pages), _cfg(), people_left=3)
    assert (out.exit_reason, out.stop_detail) == ("cap_reached", "max_people_per_run")
    assert (await _page(Script(pages), _cfg(), late=True)).exit_reason == "deadline"
    out = await _page(Script(pages, {1: SourceUnavailable("budget_cap")}), _cfg())
    assert (out.exit_reason, out.pages) == ("budget", 1)
    out = await _page(
        Script(pages, {0: SourceUnavailable("source_unavailable", "bad-ref")}), _cfg()
    )
    assert (out.exit_reason, out.failed_refs, out.unavailable) == ("no_progress", ["bad-ref"], None)
    out = await _page(Script(pages, {0: SourceUnavailable("not_available_yet")}), _cfg())
    assert out.unavailable == "not_available_yet" and out.live_calls == 0


@pytest.mark.asyncio
async def test_stored_pages_are_replayed_without_a_call() -> None:
    first = PersonPage(records=(_rec(1),), next_cursor="c1", call_ref="r0")
    second = PersonPage(records=(_rec(2),), next_cursor=None, call_ref="r1")
    script = Script([second])
    out = await _page(script, _cfg(), stored=[(0, None, first)])
    assert script.n == 1 and out.live_calls == 1 and len(out.records) == 2


def test_title_plan_and_fallback() -> None:
    cfg = load_people_config()
    specs, not_searched = plan_title_searches(cfg, frozenset({"veeva"}))
    assert not_searched == {"veeva": "lane_not_in_scope", "data_ai": "not_available_yet"}
    assert {s.source_id for s in specs} >= {"title:salesforce:1", "title:salesforce:2"}
    assert titles_for(cfg, "salesforce", "manager") == ("Salesforce Senior Manager",)
    off = replace(cfg, title_fallback="none")
    assert plan_title_searches(off, frozenset())[0] == []
    configured = replace(
        cfg, ownership_titles={"sap": ("SAP Platform Owner",) * 2}, leadership_titles={}
    )
    assert titles_for(configured, "sap", "senior") == ("SAP Platform Owner",)
    many = replace(cfg, ownership_titles={"sap": tuple(f"T{i}" for i in range(25))})
    spec = next(s for s in plan_title_searches(many, frozenset())[0] if s.lane == "sap")
    assert len(spec.titles) == 20 and spec.titles_truncated


@pytest.mark.asyncio
async def test_mention_caps_and_deadline() -> None:
    from people.mentions import resolve_mentions

    class Empty:
        def __init__(self) -> None:
            self.n = 0

        async def resolve(self, name: str, attempt: int) -> PersonPage:
            self.n += 1
            return PersonPage(call_ref=f"m{self.n}")

    mentions = [
        Mention(
            name=f"Person {i}",
            mention_url="https://synthetic-acme.example.com/n",
            sentence=f"Person {i} spoke.",
        )
        for i in range(6)
    ]
    resolver = Empty()
    out = await resolve_mentions(mentions, resolver, _cfg(mention_run_cap=3), lambda: False)
    assert resolver.n == 3 and out.exit_reason == "cap_reached" and out.unresolved == 6
    out = await resolve_mentions(mentions, Empty(), _cfg(), lambda: True)
    assert out.exit_reason == "deadline" and out.unresolved == 6 and not out.call_refs

    class Broke:
        async def resolve(self, name: str, attempt: int) -> PersonPage:
            raise SourceUnavailable("budget_cap", "ref-b")

    out = await resolve_mentions(mentions, Broke(), _cfg(), lambda: False)
    assert out.exit_reason == "budget" and out.unresolved == 6 and out.failed_refs == ["ref-b"]


@pytest.mark.asyncio
async def test_profile_cap_budget_and_isolated_failure() -> None:
    kit = Kit(Corpus(), cfg=_cfg(max_profiles_per_run=2))
    result = await kit.run()
    states = [p.profile_state for p in result.people if p.status == "in_scope"]
    assert states.count("cap") == len(states) - 2 and result.flags["profiles_cap_hit"]
    assert result.exit_reasons["profiles"] == "cap_reached"
    corpus = Corpus()
    corpus.data["profiles"]["https://www.linkedin.com/in/raj-patel"] = {"budget": True}
    result = await Kit(corpus).run()
    assert result.exit_reasons["profiles"] == "budget"
    assert any(p.profile_failure == "budget_cap" for p in result.people)


@pytest.mark.asyncio
async def test_gap_loop_cap_and_convergence() -> None:
    result = await Kit(Corpus(), cfg=_cfg(max_gap_passes=1)).run()
    assert result.exit_reasons["gap_loop"] == "cap_reached"  # data_ai is still a gap
    corpus = Corpus()
    result = await Kit(
        corpus,
        search={
            k: v for k, v in corpus.data["search"].items() if k != "association:tableau_analytics"
        },
    ).run()
    assert result.exit_reasons["gap_loop"] == "no_progress"
    assert result.counts is not None and result.counts.by_lane_in_scope["tableau_analytics"] == 0


@pytest.mark.asyncio
async def test_deadline_ends_every_loop_honestly() -> None:
    result = await Kit(Corpus(), out_of_time=lambda: True).run()
    # nothing was searched; the mentions are still kept, every one unresolved (nothing disappears)
    assert result.counts is not None and result.counts.found == result.counts.unresolved == 4
    assert all(v in ("deadline", "converged", "no_progress") for v in result.exit_reasons.values())
    people_ev = next(e for e in result.evidence if e.row_key == "people_by_platform")
    assert people_ev.call_refs == () and people_ev.not_attempted_reason == "not_available_yet"


@pytest.mark.asyncio
async def test_everything_off_and_mentions_not_configured() -> None:
    cfg = load_people_config()
    corpus = Corpus()
    corpus.lanes_off = frozenset(cfg.lane_ids())
    result = await Kit(corpus, mentions_configured=False).run()
    ev = {e.row_key: e for e in result.evidence}
    assert ev["people_by_platform"].not_attempted_reason == "lane_not_in_scope"
    assert ev["full_profiles_senior_people"].not_attempted_reason == "not_planned"
    assert result.counts is not None and result.counts.found == 0


PAGE = st.lists(st.tuples(st.integers(0, 6), st.booleans()), max_size=4)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    st.lists(
        st.tuples(
            PAGE, st.sampled_from([None, "same", "next"]), st.sampled_from(["ok", "fail", "budget"])
        ),
        min_size=1,
        max_size=8,
    )
)
def test_paging_terminates_within_caps_for_any_sequence(seq: list[Any]) -> None:
    import asyncio

    pages, fails = [], {}
    for i, (recs, nxt, outcome) in enumerate(seq):
        cursor = None if nxt is None else ("c0" if nxt == "same" else f"c{i + 1}")
        pages.append(
            PersonPage(
                records=tuple(_rec(n, url) for n, url in recs), next_cursor=cursor, call_ref=f"r{i}"
            )
        )
        if outcome == "fail":
            fails[i] = SourceUnavailable("source_unavailable", f"f{i}")
        elif outcome == "budget":
            fails[i] = SourceUnavailable("budget_cap")
    cfg = _cfg(max_pages_per_source=5)
    script = Script(pages, fails)
    out = asyncio.run(_page(script, cfg, people_left=12))
    assert script.n <= 5 and out.pages <= 5
    assert out.exit_reason in ("converged", "no_progress", "cap_reached", "budget", "deadline")
    assert len({r.source_record_id for r in out.records}) == len(out.records)


@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(
    st.sets(
        st.sampled_from(
            [
                "title:salesforce:1",
                "title:servicenow:1",
                "title:sap:2",
                "association:tableau_analytics",
            ]
        )
    ),
    st.booleans(),
)
def test_pipeline_terminates_and_keeps_invariants_for_any_outage(
    failing: set[str], late: bool
) -> None:
    import asyncio

    corpus = Corpus()
    search = {k: ({"fail": True} if k in failing else v) for k, v in corpus.data["search"].items()}
    calls = {"n": 0}

    def out_of_time() -> bool:
        calls["n"] += 1
        return late and calls["n"] > 5

    result = asyncio.run(Kit(corpus, search=search, out_of_time=out_of_time, repo=FakeRepo()).run())
    assert result.invariant_error is None and result.counts is not None
    c = result.counts
    assert c.found == c.distinct + c.merged_away
    assert corpus.ledger.paid <= 60


@pytest.mark.asyncio
async def test_a_lane_switched_off_is_not_a_shortfall() -> None:
    cfg = load_people_config()
    corpus = Corpus()
    corpus.lanes_off = frozenset(lane for lane in cfg.lane_ids() if lane != "servicenow")
    result = await Kit(corpus).run()
    ev = next(e for e in result.evidence if e.row_key == "people_by_platform")
    assert set(result.lanes_not_searched.values()) == {"lane_not_in_scope"}
    assert not ev.cap_hit and ev.expected_calls == 2  # two passes, both complete
    corpus = Corpus()
    corpus.lanes_off = frozenset(lane for lane in cfg.lane_ids() if lane != "data_ai")
    ev = next(e for e in (await Kit(corpus).run()).evidence if e.row_key == "people_by_platform")
    assert ev.cap_hit is True or ev.not_attempted_reason == "not_available_yet"
