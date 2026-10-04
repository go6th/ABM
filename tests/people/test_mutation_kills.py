"""Exact-output oracles written after the mutation check (spec 9.10): every scope branch's rule id
and evidence (the audit trail of P4), merge events, tenure ranges, counts by reason and the
rendered sentences. Each assertion pins a value a surviving mutant changed."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from people.config import PeopleConfig, TenureBucket
from people.counts import compute_counts, render_count_sentences
from people.errors import MergeError
from people.merge import MergeEvent, MergeItem, corroborates, plan_merge, stable_plan, unmerge
from people.models import PartialDate, Profile
from people.names import normalise_name
from people.scope import ScopeInput, _same_org, classify
from people.tenure import bucket_of, compute_tenure
from tests.people.conftest import person

DOMAIN = "synthetic-acme.example.com"


def _in(**kw: object) -> ScopeInput:
    base: dict[str, object] = {
        "merged": False,
        "has_url": True,
        "title": "Director, Salesforce Platform",
        "headline": None,
        "employer_domain": None,
        "is_current": None,
        "search_domain": DOMAIN,
        "location": None,
    }
    base.update(kw)
    return ScopeInput(**base)  # type: ignore[arg-type]


# (inputs, regions) -> (status, reason, rule_id, evidence, level, level_rule, band, lanes, priority)
ORACLE = [
    ({"merged": True}, (), ("merged", None, "merge", None, None, None, None, (), None)),
    (
        {"is_current": False},
        (),
        (
            "out_of_scope",
            "not_current",
            "scope.not_current",
            "is_current=false",
            None,
            None,
            None,
            (),
            None,
        ),
    ),
    (
        {"employer_domain": "Other.example"},
        (),
        (
            "out_of_scope",
            "not_current",
            "scope.other_employer",
            "employer_domain=Other.example",
            None,
            None,
            None,
            (),
            None,
        ),
    ),
    (
        {"title": "Head of IT Audit"},
        (),
        ("excluded", "it_audit", "excl.it_audit", "IT Audit", None, None, None, (), None),
    ),
    (
        {"has_url": False},
        (),
        ("unresolved", None, "scope.no_profile_url", None, None, None, None, (), None),
    ),
    (
        {"search_domain": None},
        (),
        (
            "review",
            "employment_unknown",
            "scope.employment_unknown",
            None,
            None,
            None,
            None,
            (),
            None,
        ),
    ),
    (
        {"title": "Program Lead, SAP"},
        (),
        ("review", "level_unknown", "scope.level", None, None, None, None, (), None),
    ),
    (
        {"title": "Manager", "headline": "Runs SAP"},
        (),
        (
            "out_of_scope",
            "below_scope",
            "level.manager",
            "Manager",
            "manager",
            "level.manager",
            None,
            ("sap",),
            None,
        ),
    ),
    (
        {"title": "Director", "headline": "Leads ServiceNow"},
        ("Germany",),
        (
            "review",
            "location_unknown",
            "scope.geography",
            None,
            "director",
            "level.director",
            "senior",
            ("servicenow",),
            None,
        ),
    ),
    (
        {"location": "Pune, India"},
        ("Germany",),
        (
            "out_of_scope",
            "outside_geography",
            "scope.geography",
            "Pune, India",
            "director",
            "level.director",
            "senior",
            ("salesforce",),
            None,
        ),
    ),
    (
        {"title": "Head of Compliance Systems"},
        (),
        (
            "review",
            "exclusion_ambiguous",
            "review.compliance",
            "Compliance",
            "head",
            "level.head",
            "senior",
            (),
            None,
        ),
    ),
    (
        {"title": "Senior Manager", "headline": "SAP"},
        (),
        (
            "in_scope",
            None,
            "level.senior_manager",
            "Senior Manager",
            "senior_manager",
            "level.senior_manager",
            "manager",
            ("sap",),
            "B",
        ),
    ),
    (
        {"is_current": True, "search_domain": None, "employer_domain": "eu." + DOMAIN},
        (),
        (
            "in_scope",
            None,
            "level.director",
            "Director",
            "director",
            "level.director",
            "senior",
            ("salesforce",),
            "A",
        ),
    ),
]


@pytest.mark.parametrize(("kw", "regions", "want"), ORACLE)
def test_scope_oracle_exact(
    cfg: PeopleConfig, kw: dict[str, object], regions: tuple[str, ...], want: tuple[object, ...]
) -> None:
    c = classify(_in(**kw), cfg, DOMAIN, regions)
    got = (
        c.status,
        c.reason,
        c.rule_id,
        c.evidence,
        c.level,
        c.level_rule,
        c.band,
        c.lanes,
        c.priority,
    )
    assert got == want
    if c.status == "in_scope":
        assert c.priority_rule is not None and c.priority_rule.startswith(
            f"prio.{c.priority.lower()}"
        )  # type: ignore[union-attr]


def test_priority_unmatched_exact(cfg: PeopleConfig) -> None:
    c = classify(_in(), replace(cfg, priority_rules=()), DOMAIN)
    assert (c.status, c.reason, c.rule_id, c.level, c.band, c.lanes) == (
        "review",
        "priority_unmatched",
        "scope.priority",
        "director",
        "senior",
        ("salesforce",),
    )


def test_same_org_exact() -> None:
    assert _same_org("WWW." + DOMAIN.upper(), DOMAIN) is True  # case-insensitive, www. dropped
    assert _same_org("www." + DOMAIN, DOMAIN) and _same_org(" eu." + DOMAIN, DOMAIN)
    assert (
        not _same_org("x" + DOMAIN, DOMAIN)
        and not _same_org(None, DOMAIN)
        and not _same_org("", DOMAIN)
    )


# ---- merge --------------------------------------------------------------------------------------

U1, U2 = "https://www.linkedin.com/in/a", "https://www.linkedin.com/in/b"


def _i(key: str, url: str | None, name: str, **kw: object) -> MergeItem:
    return MergeItem(key=key, url=url, name=normalise_name(name), **kw)  # type: ignore[arg-type]


def test_merge_events_exact() -> None:
    plan = plan_merge(
        [
            _i("a", U1, "Jo Bo", lanes=frozenset({"sap"})),
            _i("b", U1, "Jo Bo"),
            _i("c", None, "Jo Bo", lanes=frozenset({"sap"})),
            _i("d", U1, "Other", lanes=frozenset({"sap"})),
        ],
        0.5,
    )
    assert plan.events == (
        MergeEvent("merge", "b", "a", "merge.url"),
        MergeEvent("merge", "c", "a", "merge.name"),
        MergeEvent("merge", "d", "a", "merge.url"),
    )
    back = unmerge(plan, "c")
    assert back.events[-1] == MergeEvent("unmerge", "c", None, "unmerge.manual")
    assert back.possible_duplicates == frozenset({"c"}) and back.canonical_of["c"] == "c"
    assert back.canonical_of["b"] == "a" and back.merged_away == 2


def test_merge_keeps_scanning_after_a_record_without_a_name() -> None:
    items = [
        _i("n", None, ""),
        _i("a", U1, "Jo Bo", lanes=frozenset({"sap"})),
        _i("m", None, "Jo Bo", lanes=frozenset({"sap"})),
    ]
    assert plan_merge(items, 0.5).merged_into("m") == "a"


def test_corroboration_threshold_is_used() -> None:
    a = _i("a", None, "x", title_tokens=frozenset({"director", "crm", "ops"}))
    b = _i("b", None, "x", title_tokens=frozenset({"director", "crm"}))
    assert corroborates(a, b, 0.6) and not corroborates(a, b, 0.7)
    assert not corroborates(
        _i("a", None, "x", employer_domain=None), _i("b", None, "x", employer_domain=None), 0.5
    )
    with pytest.raises(MergeError, match="duplicate record keys"):
        plan_merge([_i("a", U1, "x"), _i("a", U1, "x")], 0.5)


def test_stable_plan_checks_every_round(monkeypatch: pytest.MonkeyPatch) -> None:
    import people.merge as merge

    real = merge.plan_merge
    rounds: list[int] = []

    def counting(items: list[MergeItem], threshold: float) -> merge.MergePlan:
        rounds.append(len(items))
        plan = real(items, threshold)
        if len(rounds) == 3:  # only the third computation differs
            return merge.MergePlan({k: k for k in plan.canonical_of}, frozenset())
        return plan

    monkeypatch.setattr(merge, "plan_merge", counting)
    items = [_i("a", U1, "J"), _i("b", U1, "J")]
    assert stable_plan(items, 0.5, 2).merged_away == 1 and len(rounds) == 2
    rounds.clear()
    with pytest.raises(MergeError, match="not stable"):
        stable_plan(items, 0.5, 3)
    rounds.clear()
    with pytest.raises(MergeError):
        stable_plan(items, 0.5, 4)
    rounds.clear()
    merge.stable_plan(items, 0.5, 1)
    assert len(rounds) == 2  # a cap below 2 still checks once


def test_check_plan_messages() -> None:
    from people.merge import MergePlan, check_plan

    with pytest.raises(MergeError, match="unknown record"):
        check_plan(MergePlan({"a": "z"}, frozenset()))
    with pytest.raises(MergeError, match="merged record"):
        check_plan(MergePlan({"a": "b", "b": "c", "c": "c"}, frozenset()))
    with pytest.raises(MergeError, match="is not merged"):
        unmerge(MergePlan({"a": "a"}, frozenset()), "a")


# ---- tenure -------------------------------------------------------------------------------------

B = (TenureBucket("b0", 0, 12), TenureBucket("b1", 12, 36), TenureBucket("b2", 36, None))


def test_tenure_exact_ranges() -> None:
    as_of = date(2026, 10, 3)
    t = compute_tenure(PartialDate.parse("2024-10-03"), None, as_of, B)
    assert (t.status, t.basis, t.months_min, t.months_max, t.bucket, t.reason) == (
        "known",
        "join_date",
        24,
        24,
        "b1",
        None,
    )
    t = compute_tenure(PartialDate.parse("2025-10"), PartialDate.parse("2025-10"), as_of, B)
    assert (t.months_min, t.months_max, t.bucket) == (11, 12, None) and t.reason == "precision"
    t = compute_tenure(PartialDate.parse("2026-10-03"), None, as_of, B)
    assert (t.status, t.months_min, t.months_max) == ("known", 0, 0)
    t = compute_tenure(PartialDate.parse("2026-10-04"), None, as_of, B)
    assert (t.status, t.reason) == ("date_invalid", "future_date")
    t = compute_tenure(PartialDate.parse("2020-05"), PartialDate.parse("2020-05"), as_of, B)
    assert t.status == "known"  # same month: not before the join date
    t = compute_tenure(PartialDate.parse("2020-05-10"), PartialDate.parse("2020-05-09"), as_of, B)
    assert t.reason == "role_start_before_join"
    assert (
        bucket_of(11, B) == "b0"
        and bucket_of(12, B) == "b1"
        and bucket_of(35, B) == "b1"
        and bucket_of(36, B) == "b2"
    )


# ---- counts -------------------------------------------------------------------------------------


def _profile(key: str, **kw: object) -> Profile:
    return Profile(
        person_key=key,
        profile_url=f"https://www.example.org/in/{key}",
        provider_code="p",
        raw_ref="r",
        call_ref="c",
        **kw,
    )  # type: ignore[arg-type]


def test_counts_exact_by_reason_and_sentences(cfg: PeopleConfig) -> None:
    people = [
        person("a", lanes=["sap"], profile_state="read"),
        person("b", "out_of_scope", reason="below_scope", level="manager"),
        person("c", "out_of_scope", reason="below_scope", level="manager"),
        person("d", "out_of_scope", reason="not_current"),
        person("e", "review", reason="level_unknown", level=None),
        person("f", "excluded", reason="security"),
        person("g", priority="C", profile_state="not_requested"),
        person("h", profile_state="cap"),
        person("i", "unresolved"),
        person("j", "merged", merged_into="a"),
        person("k", profile_state="partial"),
    ]
    profiles = {
        "a": _profile(
            "a",
            join_date=PartialDate(year=2010),
            certifications=("SAP Certified",),
            prior_employers=("Ref Co",),
        ),
        "k": _profile("k", certifications=None, fields_dropped=2),
        "h": _profile("h"),  # a profile row of a person whose state is cap is not read
    }
    c = compute_counts(
        people,
        profiles,
        [],
        reference_employers=("Ref Co",),
        alumni_aliases={},
        as_of=date(2026, 10, 3),
        buckets=cfg.tenure_buckets,
        lanes=cfg.lanes,
    )
    assert c.out_of_scope == {"below_scope": 2, "not_current": 1}
    assert list(c.out_of_scope) == ["below_scope", "not_current"]  # sorted
    assert (c.review, c.excluded) == ({"level_unknown": 1}, {"security": 1})
    assert (c.in_scope, c.unresolved, c.merged_away, c.distinct, c.found) == (4, 1, 1, 10, 11)
    assert (c.profiles_requested, c.profiles_read, c.profiles_partial, c.profiles_failed) == (
        3,
        2,
        1,
        0,
    )
    assert (c.alumni.count, c.alumni.base, c.alumni.computed) == (1, 2, True)
    assert (c.cert_field_provided, c.cert_field_absent, c.certified_by_lane["sap"]) == (1, 1, 1)
    assert (c.tenure_known, c.tenure_unknown, c.date_invalid, c.tenure_buckets["5y_plus"]) == (
        1,
        1,
        0,
        1,
    )
    assert c.by_priority == {"A": 3, "B": 0, "C": 1} and c.by_lane_in_scope["sap"] == 1
    assert c.fields_dropped == 2 and c.possible_duplicate == 0 and c.hostile == 0
    lines = render_count_sentences(c)
    assert lines[0] == "11 people found, 1 merged as duplicates, 10 distinct."
    assert lines[1] == "4 of 10 distinct people are in scope."
    assert "2 of 10 distinct people out of scope, reason below scope." in lines
    assert "1 of 10 distinct people for review, reason level unknown." in lines
    assert "1 of 10 distinct people excluded, reason security." in lines
    assert "1 of 10 distinct people have no profile link yet." in lines
    assert "2 of 3 profiles requested were read (1 partly)." in lines
    assert "1 of 2 profiles read list a prior employer on the reference list." in lines
    assert '1 of 1 profiles with a dated tenure are in tenure band "5y_plus".' in lines
    assert "1 of 2 profiles read have no usable tenure (0 with an invalid date)." in lines
    assert "1 of 1 profiles that list certifications hold one for sap." in lines
    assert "1 of 2 profiles read do not provide the certifications field." in lines
    none = compute_counts(
        [person("z", "unresolved")],
        {},
        [],
        reference_employers=None,
        alumni_aliases={},
        as_of=date(2026, 10, 3),
        buckets=cfg.tenure_buckets,
        lanes=cfg.lanes,
    )
    assert not any("no profile link" in s for s in render_count_sentences(none)[:1])
    zero = compute_counts(
        [person("y")],
        {},
        [],
        reference_employers=None,
        alumni_aliases={},
        as_of=date(2026, 10, 3),
        buckets=cfg.tenure_buckets,
        lanes=cfg.lanes,
    )
    assert not any("no profile link yet" in s for s in render_count_sentences(zero))


def test_counts_alias_and_failed_profiles(cfg: PeopleConfig) -> None:
    people = [person("a", profile_state="read"), person("b", profile_state="failed")]
    c = compute_counts(
        people,
        {"a": _profile("a", prior_employers=("RC Group",))},
        [],
        reference_employers=("Ref Co",),
        alumni_aliases={"Ref Co": ("RC Group",)},
        as_of=date(2026, 10, 3),
        buckets=cfg.tenure_buckets,
        lanes=cfg.lanes,
    )
    assert (c.alumni.count, c.profiles_failed, c.profiles_requested) == (1, 1, 2)


def test_counts_accumulate_not_overwrite(cfg: PeopleConfig) -> None:
    from people.models import Post

    people = [person(k, lanes=["sap"], profile_state="read") for k in ("a", "b", "c")]
    common = {
        "join_date": PartialDate(year=2010),
        "certifications": ("SAP Certified",),
        "prior_employers": ("Ref Co",),
    }
    profiles = {
        "a": _profile("a", **common),
        "b": _profile("b", **common),
        "c": _profile("c", hostile=True, **common),
    }
    posts = [
        Post(
            person_key="a",
            post_url="https://www.example.org/p/1",
            excerpt="",
            excerpt_sha256="0" * 64,
            raw_ref="r",
            call_ref="c",
            hostile=True,
        )
    ]
    c = compute_counts(
        people,
        profiles,
        posts,
        reference_employers=("Ref Co",),
        alumni_aliases={},
        as_of=date(2026, 10, 3),
        buckets=cfg.tenure_buckets,
        lanes=cfg.lanes,
    )
    assert c.tenure_buckets["5y_plus"] == 3 and c.certified_by_lane["sap"] == 3
    assert c.alumni.count == 3 and c.hostile == 2


def test_stable_plan_passes_the_threshold() -> None:
    t1, t2 = frozenset({"director", "crm"}), frozenset({"director", "crm", "ops"})
    items = [_i("a", U1, "Jo Bo", title_tokens=t1), _i("m", None, "Jo Bo", title_tokens=t2)]
    assert stable_plan(items, 0.6, 2).merged_into("m") == "a"
    assert stable_plan(items, 0.7, 2).merged_into("m") is None
