"""Counts from rows, invariants, rendered sentences (P6, P7, P8) and properties (spec 9.4)."""

from __future__ import annotations

import inspect
import re
from datetime import date
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from people import counts as counts_mod
from people.config import PeopleConfig
from people.counts import compute_counts, render_count_sentences
from people.errors import InvariantViolation
from people.merge import MergeItem, plan_merge
from people.models import PartialDate, Post, Profile
from people.tenure import compute_tenure
from tests.people.conftest import person

AS_OF = date(2026, 10, 3)


def _profile(key: str, **kw: Any) -> Profile:
    base: dict[str, Any] = {
        "person_key": key,
        "profile_url": f"https://www.example.org/in/{key}",
        "provider_code": "p_people",
        "raw_ref": f"raw-{key}",
        "call_ref": f"call-{key}",
    }
    base.update(kw)
    return Profile(**base)


def _counts(
    cfg: PeopleConfig,
    people: list[Any],
    profiles: dict[str, Profile],
    posts: list[Post] | None = None,
    refs: Any = None,
) -> Any:
    return compute_counts(
        people,
        profiles,
        posts or [],
        reference_employers=refs,
        alumni_aliases=cfg.alumni_aliases,
        as_of=AS_OF,
        buckets=cfg.tenure_buckets,
        lanes=cfg.lanes,
    )


def test_counts_from_rows_with_bases(cfg: PeopleConfig) -> None:
    people = [
        person("a", lanes=["salesforce"], profile_state="read"),
        person("b", lanes=["salesforce", "sap"], profile_state="partial", priority="B"),
        person("c", profile_state="failed"),
        person("d", "merged", merged_into="a"),
        person("e", "excluded", reason="security"),
        person("f", "out_of_scope", reason="below_scope", level="manager"),
        person("g", "review", reason="level_unknown", possible_duplicate=True),
        person("h", "unresolved", fields_dropped=2),
        person("i", activity_unfiltered=True, profile_state="reused"),
    ]
    profiles = {
        "a": _profile(
            "a",
            join_date=PartialDate.parse("2020-01"),
            certifications=("Salesforce Certified Administrator",),
            prior_employers=("Synthetic Client Co",),
        ),
        "b": _profile("b", certifications=(), fields_dropped=3),
        "i": _profile("i", join_date=PartialDate.parse("2027-01"), hostile=True),
    }
    posts = [
        Post(
            person_key="a",
            post_url="https://www.example.org/p/1",
            excerpt="x",
            excerpt_sha256="0" * 64,
            raw_ref="r",
            call_ref="c",
        ),
        Post(
            person_key="a",
            post_url="https://www.example.org/p/2",
            excerpt="y",
            excerpt_sha256="1" * 64,
            raw_ref="r",
            call_ref="c",
        ),
    ]
    c = _counts(cfg, people, profiles, posts, ["Synthetic Client Co"])
    assert (c.found, c.distinct, c.merged_away, c.in_scope, c.unresolved) == (9, 8, 1, 4, 1)
    assert c.excluded == {"security": 1} and c.out_of_scope == {"below_scope": 1}
    assert c.review == {"level_unknown": 1} and c.possible_duplicate == 1
    assert (c.profiles_requested, c.profiles_read, c.profiles_partial, c.profiles_failed) == (
        4,
        3,
        1,
        1,
    )
    assert (c.tenure_known, c.tenure_unknown, c.date_invalid) == (1, 1, 1)
    assert c.tenure_buckets["5y_plus"] == 1
    assert (c.cert_field_provided, c.cert_field_absent) == (2, 1)
    assert c.certified_by_lane["salesforce"] == 1 and c.certified_by_lane["sap"] == 0
    assert c.alumni.computed and (c.alumni.count, c.alumni.base) == (1, 3)
    assert (c.posts_people, c.posts_pulled, c.activity_unfiltered) == (1, 2, 1)
    assert (c.hostile, c.fields_dropped) == (1, 5)
    assert c.by_lane_in_scope["salesforce"] == 2 and c.by_lane_in_scope["sap"] == 1
    assert c.by_priority == {"A": 3, "B": 1, "C": 0}
    lines = render_count_sentences(c)
    assert "1 of 3 profiles read list a prior employer on the reference list." in lines
    assert "4 of 8 distinct people are in scope." in lines


def test_alumni_not_computed_without_reference_list(cfg: PeopleConfig) -> None:
    c = _counts(cfg, [person("a", profile_state="read")], {"a": _profile("a")})
    assert not c.alumni.computed and c.alumni.count is None
    assert c.alumni.not_computed_reason == "waiting_on_account_owner"
    assert any("waiting on the account owner" in s for s in render_count_sentences(c))


@pytest.mark.parametrize(
    "people",
    [
        [person("a"), person("a")],
        [person("a", "merged")],
        [person("a", "merged", merged_into="zz")],
        [person("a", "merged", merged_into="a")],
        [person("a", merged_into="b"), person("b")],
        [person("a", profile_url=None)],
    ],
)
def test_invariants_refuse_inconsistent_rows(cfg: PeopleConfig, people: list[Any]) -> None:
    with pytest.raises(InvariantViolation):
        _counts(cfg, people, {})


def test_no_function_in_counts_accepts_a_count() -> None:
    count_names = re.compile(
        r"^(found|distinct|merged_away|in_scope|profiles_read|count|n|total|base)$"
    )
    for name, fn in inspect.getmembers(counts_mod, inspect.isfunction):
        if fn.__module__ != counts_mod.__name__:
            continue
        params = inspect.signature(fn).parameters
        assert not [p for p in params if count_names.match(p)], name
        for p in params.values():
            assert p.annotation not in ("int", int), (name, p.name)


def test_every_number_in_a_sentence_is_a_count(cfg: PeopleConfig) -> None:
    people = [person(f"k{i}", profile_state="read") for i in range(5)] + [
        person("x", "excluded", reason="it_audit")
    ]
    c = _counts(
        cfg,
        people,
        {f"k{i}": _profile(f"k{i}", join_date=PartialDate.parse("2019-05")) for i in range(5)},
    )
    values = {str(v) for v in _ints(c.model_dump())}
    for line in render_count_sentences(c):
        for number in re.findall(r"\d+", re.sub(r'"[^"]*"', "", line)):
            assert number in values, (number, line)


def _ints(value: Any) -> list[int]:
    if isinstance(value, bool):
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, dict):
        return [i for v in value.values() for i in _ints(v)]
    return []


# ---- properties ------------------------------------------------------------------------------

STATUS = st.sampled_from(["in_scope", "out_of_scope", "excluded", "review", "unresolved"])


@settings(max_examples=150, deadline=None)
@given(st.lists(st.tuples(STATUS, st.booleans(), st.booleans()), min_size=0, max_size=25))
def test_counts_invariants_hold_for_any_people_set(rows: list[tuple[str, bool, bool]]) -> None:
    from people.config import load_people_config

    cfg = load_people_config()
    people, profiles = [], {}
    canon = [i for i, (s, _, _) in enumerate(rows)]
    for i, (status, merged, read) in enumerate(rows):
        if merged and i > 0:
            people.append(person(f"p{i}", "merged", merged_into=f"p{canon[0]}"))
            continue
        state = "read" if read and status == "in_scope" else "not_requested"
        people.append(person(f"p{i}", status, profile_state=state))
        if state == "read":
            profiles[f"p{i}"] = _profile(f"p{i}", join_date=PartialDate(year=2000 + i % 30))
    if people and people[0].status == "merged":  # pragma: no cover - index 0 is never merged
        return
    c = _counts(cfg, people, profiles)
    assert c.found == c.distinct + c.merged_away
    assert (
        c.distinct
        == c.in_scope
        + sum(c.out_of_scope.values())
        + sum(c.excluded.values())
        + sum(c.review.values())
        + c.unresolved
    )
    assert c.tenure_known + c.tenure_unknown + c.date_invalid == c.profiles_read
    assert c.cert_field_provided + c.cert_field_absent == c.profiles_read
    assert sum(c.by_lane_in_scope.values()) >= 0


URLS = st.sampled_from([None, "https://www.example.org/in/a", "https://www.example.org/in/b"])
NAMES = st.sampled_from(["", "jane doe", "bob roe"])
DOMS = st.sampled_from([None, "acme.example", "other.example"])


@settings(max_examples=200, deadline=None)
@given(st.lists(st.tuples(URLS, NAMES, DOMS), max_size=12), st.randoms())
def test_merge_order_independent_acyclic(rows: list[tuple[Any, str, Any]], rnd: Any) -> None:
    items = [MergeItem(f"k{i:02d}", u, n, d) for i, (u, n, d) in enumerate(rows)]
    plan = plan_merge(items, 0.5)
    shuffled = list(items)
    rnd.shuffle(shuffled)
    again = plan_merge(shuffled, 0.5)
    assert again.canonical_of == plan.canonical_of
    assert again.possible_duplicates == plan.possible_duplicates
    for target in plan.canonical_of.values():
        assert plan.canonical_of[target] == target  # points at a canonical row, no chain
    by_url: dict[str, set[str]] = {}
    for item in items:
        if item.url:
            by_url.setdefault(plan.canonical_of[item.key], set()).add(item.url)
    assert all(len(u) == 1 for u in by_url.values())  # never two URLs in one person


@settings(max_examples=200, deadline=None)
@given(
    st.integers(min_value=1990, max_value=2026),
    st.one_of(st.none(), st.integers(1, 12)),
    st.dates(min_value=date(2026, 1, 1), max_value=date(2030, 12, 31)),
    st.integers(min_value=0, max_value=800),
)
def test_tenure_monotonic_in_as_of(year: int, month: int | None, as_of: date, extra: int) -> None:
    from datetime import timedelta

    from people.config import load_people_config

    buckets = load_people_config().tenure_buckets
    start = PartialDate(year=year, month=month)
    early = compute_tenure(start, None, as_of, buckets)
    late = compute_tenure(start, None, as_of + timedelta(days=extra), buckets)
    if early.status == "date_invalid":
        return
    assert late.status != "date_invalid"
    assert (late.months_min or 0) >= (early.months_min or 0)
    assert (late.months_max or 0) >= (early.months_max or 0)
