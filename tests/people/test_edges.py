"""Edge paths: wrong-typed provider fields, a redacted title, extractor caps and mismatches, posts
failures and deadlines, top-people order, the erasure rule and unanswered association search."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from people.claims import title_claims
from people.config import load_people_config
from people.erasure import PERSON_FIELDS, PROFILE_FIELDS, erase_person, erase_post, erase_profile
from people.models import PartialDate, Post, Profile, RawProfile
from people.pipeline import top_people
from people.profiles import to_profile
from tests.fakes.people import Corpus, FakeLlm
from tests.people.conftest import person
from tests.people.kit import Kit, is_hostile


def _cfg(**caps: float) -> Any:
    base = load_people_config()
    return replace(base, caps={**base.caps, **caps})


def test_wrong_typed_provider_fields_are_dropped_not_guessed() -> None:
    raw = RawProfile(
        profile_url="https://www.linkedin.com/in/x",
        provider_code="p_people",
        raw_ref="raw",
        call_ref="call",
        fields={
            "name": 42,  # not a string
            "prior_employers": "Synthetic Co",  # not a list
            "certifications": ["ok", 7],  # mixed list
            "join_date": "last spring",  # not a date
            "last_activity_at": "2026-09",  # month only: activity unknown
            "personal_email": "x@example.org",  # not allow-listed
        },
    )
    p = to_profile(raw, "pk", load_people_config(), is_hostile=is_hostile)
    assert p.name is None and p.prior_employers is None and p.certifications is None
    assert p.join_date is None and p.last_activity_at is None and p.partial
    assert p.fields_dropped == 4  # name, prior_employers, certifications, personal_email


def test_redacted_title_gets_no_claim() -> None:
    claims, skipped = title_claims(
        [person("pa", title="Director, call [redacted]"), person("pb", title="Director, SAP")]
    )
    assert skipped == 1 and [c.local_id for c in claims] == ["person-pb"]
    assert title_claims([person("pc", "excluded", reason="security", title="x")]) == ([], 0)


@pytest.mark.asyncio
async def test_extractor_cap_mismatch_and_over_person_cap() -> None:
    capped = await Kit(Corpus(), cfg=_cfg(max_extractor_calls=1)).run()
    assert capped.exit_reasons["findings"] == "cap_reached"
    wrong = await Kit(
        Corpus(),
        extractor=FakeLlm(
            {}, raw={"platform team": {"person_key": "someone-else", "findings": []}}
        ),
    ).run()
    assert wrong.drops["person_mismatch"] == 1
    many = {
        "platform team": {
            "findings": [
                {
                    "claim": "Moved field service onto one org",
                    "sentence": "We moved field service onto one org last year.",
                }
            ]
            * 5
        }
    }
    over = await Kit(Corpus(), extractor=FakeLlm(many), cfg=_cfg(max_findings_per_person=2)).run()
    assert over.drops["over_person_cap"] == 3 and len(over.findings) == 2


@pytest.mark.asyncio
async def test_posts_failure_deadline_and_budget() -> None:
    corpus = Corpus()
    kit = Kit(corpus)

    async def broken(url: str, since: date) -> Any:
        from people.ports import SourceUnavailable

        raise SourceUnavailable("source_unavailable", "failed-post")

    kit.posts.pull = broken  # type: ignore[method-assign]
    result = await kit.run()
    ev = next(e for e in result.evidence if e.row_key == "posts_priority_people")
    assert ev.call_refs == ("failed-post",) and result.exit_reasons["posts"] == "converged"

    calls = {"n": 0}

    def late() -> bool:  # time runs out after the profiles
        calls["n"] += 1
        return calls["n"] > 22

    timed = await Kit(Corpus(), out_of_time=late).run()
    assert timed.exit_reasons["posts"] == "deadline"

    budget = Kit(Corpus())

    async def no_money(url: str, since: date) -> Any:
        from people.ports import SourceUnavailable

        raise SourceUnavailable("budget_cap")

    budget.posts.pull = no_money  # type: ignore[method-assign]
    spent = await budget.run()
    ev = next(e for e in spent.evidence if e.row_key == "posts_priority_people")
    assert spent.exit_reasons["posts"] == "budget" and ev.not_attempted_reason == "budget_cap"


@pytest.mark.asyncio
async def test_unanswered_title_search_reports_source_unavailable() -> None:
    corpus = Corpus()
    search = {k: {"unavailable": "source_unavailable"} for k in corpus.data["search"]}
    result = await Kit(corpus, search=search).run()
    ev = next(e for e in result.evidence if e.row_key == "people_by_platform")
    assert ev.call_refs == () and ev.not_attempted_reason == "source_unavailable"


@pytest.mark.asyncio
async def test_invariant_failure_is_recorded_not_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    import people.pipeline as pipeline_mod
    from people.errors import InvariantViolation

    def broken(*a: Any, **k: Any) -> Any:
        raise InvariantViolation("synthetic")

    monkeypatch.setattr(pipeline_mod, "compute_counts", broken)
    result = await Kit(Corpus()).run()
    assert result.counts is None and result.invariant_error == "synthetic"
    assert result.count_sentences == ()


@pytest.mark.asyncio
async def test_top_people_order() -> None:
    result = await Kit(Corpus()).run()
    cfg = load_people_config()
    top = result.top_people(3, cfg.level_order)
    assert [p.classification.priority for p in top] == ["A", "A", "A"]
    assert [p.classification.level for p in top][0] == "c_level"  # most senior first
    assert top_people(result.people, 0, cfg.level_order) == ()
    assert len(top_people(result.people, 99, cfg.level_order)) == 9


def test_erasure_rule_keeps_identity() -> None:
    p = person("pa", name="Synthetic Name", title="Director", merged_into=None)
    gone = erase_person(p)
    assert all(getattr(gone, f) is None for f in PERSON_FIELDS)
    assert (gone.person_key, gone.status, gone.profile_url) == (
        p.person_key,
        p.status,
        p.profile_url,
    )
    prof = Profile(
        person_key="pa",
        profile_url="https://www.linkedin.com/in/a",
        provider_code="p_people",
        raw_ref="r",
        call_ref="c",
        name="Synthetic",
        join_date=PartialDate(year=2020),
    )
    assert all(getattr(erase_profile(prof), f) is None for f in PROFILE_FIELDS)
    post = Post(
        person_key="pa",
        post_url="https://www.linkedin.com/posts/a",
        excerpt="text",
        excerpt_sha256="0" * 64,
        raw_ref="r",
        call_ref="c",
    )
    assert erase_post(post).excerpt == ""


def test_erasure_lists_match_the_migration() -> None:
    from pathlib import Path

    sql = (Path(__file__).resolve().parents[2] / "migrations/0014_people_pipeline.sql").read_text()
    profile_check = sql[sql.index("CONSTRAINT ck_person_profiles_erased") :]
    profile_check = profile_check[: profile_check.index(")\n);")]
    # migration 0089 replaces the check and the trigger with versions that include `employments`
    later = (Path(__file__).resolve().parents[2] / "migrations/0089_people_fields.sql").read_text()
    later_check = later[later.index("ADD CONSTRAINT ck_person_profiles_erased") :]
    later_check = later_check[: later_check.index("));")]
    trigger = later[later.index("CREATE TRIGGER trg_person_profiles_erasure_only") :]
    for f in PROFILE_FIELDS:
        assert f"{f} IS NULL" in (later_check if f == "employments" else profile_check), f
        assert f"{f} IS NULL" in later_check, f
        assert f"'{f}'" in trigger, f
    people_check = sql[sql.index("ADD CONSTRAINT ck_people_erased") :]
    for f in ("name", "title", "headline", "location"):
        assert f"{f} IS NULL" in people_check[:300], f
    assert set(PERSON_FIELDS) >= {"name", "title", "headline", "location"}
