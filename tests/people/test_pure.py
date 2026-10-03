"""Pure-logic tests (spec 9.2): URLs, names, merge, level, lane, exclusion, priority, scope,
tenure, certifications, alumni. Synthetic values only."""

from __future__ import annotations

from datetime import date

import pytest

from people.alumni import matched_employer, reference_set
from people.certs import certified_lanes
from people.config import PeopleConfig, ProfileUrlRule, TenureBucket
from people.cues import clean, find_cue
from people.errors import MergeError
from people.exclusion import check_exclusion
from people.lane import lanes_for
from people.level import classify_level
from people.merge import (
    MergeItem,
    MergePlan,
    check_plan,
    plan_merge,
    stable_plan,
    unmerge,
)
from people.models import Classification, PartialDate
from people.names import names_equal, normalise_name, title_tokens, titles_similar
from people.priority import assign_priority
from people.scope import ScopeInput, classify
from people.tenure import bucket_of, compute_tenure, months_between
from people.urls import canonical_profile_url, is_profile_host

RULE = ProfileUrlRule("www.profiles.example", "profiles.example", True)
DOMAIN = "synthetic-acme.example.com"


# ---- urls -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "variant",
    [
        "https://www.profiles.example/in/Jane-Doe",
        "https://www.profiles.example/in/jane-doe/",
        "http://WWW.PROFILES.EXAMPLE/in/jane-doe?utm_source=x&trk=abc",
        "https://uk.profiles.example/in/jane-doe#about",
        "https://profiles.example/in/JANE-DOE//",
    ],
)
def test_profile_url_variants_fold_to_one(variant: str) -> None:
    assert canonical_profile_url(variant, RULE) == "https://www.profiles.example/in/jane-doe"


def test_profile_url_rejects_and_keeps() -> None:
    assert canonical_profile_url(None, RULE) is None
    assert canonical_profile_url("", RULE) is None
    assert canonical_profile_url("not a url", RULE) is None
    assert canonical_profile_url("ftp://www.profiles.example/in/x", RULE) is None
    assert canonical_profile_url("https://www.profiles.example/", RULE) is None
    other = canonical_profile_url("https://Elsewhere.example/People/Ann/?utm_x=1", RULE)
    assert other == "https://elsewhere.example/People/Ann"  # non-profile host: path case kept
    keep = ProfileUrlRule("www.profiles.example", "profiles.example", False)
    assert canonical_profile_url("https://profiles.example/in/Ann", keep) == (
        "https://www.profiles.example/in/Ann"
    )
    assert is_profile_host("profiles.example", RULE) and not is_profile_host(
        "xprofiles.example", RULE
    )


# ---- names ----------------------------------------------------------------------------------


def test_names() -> None:
    assert normalise_name("  José  O'Neil-Smith ") == "jose o neil smith"
    assert normalise_name(None) == "" and normalise_name("___") == ""
    assert (
        names_equal("Jane DOE", "jane  doe")
        and not names_equal("Jane", "")
        and not names_equal(None, None)
    )
    assert not names_equal("Dr Jane Doe", "Jane Doe")  # no honorific list (D-21)
    a, b = title_tokens("Director, CRM Platforms"), title_tokens("CRM Platforms Director")
    assert titles_similar(a, b, 0.5)
    assert not titles_similar(a, title_tokens("Chief Financial Officer"), 0.5)
    assert not titles_similar(frozenset(), a, 0.5)


def test_cues() -> None:
    assert clean(None) == "" and clean("a\x00  b") == "a b"
    assert find_cue("Head of IT  Audit", ["it audit"]) is None  # caller must clean first
    assert find_cue(clean("Head of IT  Audit"), ["it audit"]) == "IT Audit"
    assert find_cue("Headcount", ["head"]) is None


# ---- merge ----------------------------------------------------------------------------------


def _item(key: str, url: str | None, name: str, **kw: object) -> MergeItem:
    return MergeItem(key=key, url=url, name=normalise_name(name), **kw)  # type: ignore[arg-type]


U1, U2 = "https://www.profiles.example/in/jane", "https://www.profiles.example/in/jane-2"


def test_merge_by_url_first() -> None:
    plan = plan_merge([_item("b", U1, "Jane Doe"), _item("a", U1, "J. Doe")], 0.5)
    assert plan.canonical_of == {"a": "a", "b": "a"} and plan.merged_into("b") == "a"
    assert plan.merged_into("a") is None and plan.merged_away == 1
    assert [e.rule_id for e in plan.events] == ["merge.url"]


def test_two_people_same_name_different_urls_are_not_merged() -> None:
    plan = plan_merge(
        [
            _item("a", U1, "Jane Doe", employer_domain=DOMAIN),
            _item("b", U2, "Jane Doe", employer_domain=DOMAIN),
        ],
        0.5,
    )
    assert plan.merged_away == 0 and plan.possible_duplicates == {"a", "b"}


def test_name_only_duplicate_with_and_without_corroboration() -> None:
    with_ = plan_merge(
        [
            _item("a", U1, "Jane Doe", lanes=frozenset({"sap"})),
            _item("m", None, "jane doe", lanes=frozenset({"sap"})),
        ],
        0.5,
    )
    assert with_.merged_into("m") == "a" and with_.events[0].rule_id == "merge.name"
    without = plan_merge([_item("a", U1, "Jane Doe"), _item("m", None, "Jane Doe")], 0.5)
    assert without.merged_away == 0 and without.possible_duplicates == {"m"}


def test_name_only_record_matching_two_url_groups_is_flagged_not_merged() -> None:
    items = [
        _item("a", U1, "Jane Doe", employer_domain=DOMAIN),
        _item("b", U2, "Jane Doe", employer_domain=DOMAIN),
        _item("m", None, "Jane Doe", employer_domain=DOMAIN),
    ]
    plan = plan_merge(items, 0.5)
    assert plan.merged_away == 0 and "m" in plan.possible_duplicates


def test_url_less_records_merge_among_themselves_only_when_corroborated() -> None:
    title = frozenset({"director", "crm"})
    plan = plan_merge(
        [
            _item("m1", None, "Ann Lee", title_tokens=title),
            _item("m2", None, "Ann Lee", title_tokens=title),
            _item("m3", None, "Ann Lee"),
            _item("m4", None, ""),
        ],
        0.5,
    )
    assert plan.merged_into("m2") == "m1" and plan.merged_into("m3") is None
    assert "m3" in plan.possible_duplicates and plan.merged_into("m4") is None


def test_merge_is_order_independent_and_stable() -> None:
    items = [
        _item("c", U1, "Jane Doe"),
        _item("a", U1, "Jane"),
        _item("d", None, "Jane Doe", employer_domain=DOMAIN),
        _item("b", U2, "Bob Roe", employer_domain=DOMAIN),
    ]
    forward = plan_merge(items, 0.5)
    assert plan_merge(list(reversed(items)), 0.5).canonical_of == forward.canonical_of
    assert stable_plan(items, 0.5, 2).canonical_of == forward.canonical_of
    with pytest.raises(MergeError):
        plan_merge([_item("a", U1, "x"), _item("a", U2, "y")], 0.5)


def test_unmerge_and_check_plan() -> None:
    plan = plan_merge([_item("a", U1, "Jane"), _item("b", U1, "Jane")], 0.5)
    back = unmerge(plan, "b")
    assert back.merged_into("b") is None and back.events[-1].kind == "unmerge"
    with pytest.raises(MergeError):
        unmerge(back, "a")
    with pytest.raises(MergeError):
        check_plan(MergePlan({"a": "b", "b": "c", "c": "c"}, frozenset()))
    with pytest.raises(MergeError):
        check_plan(MergePlan({"a": "z"}, frozenset()))


def test_unstable_plan_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import people.merge as merge

    calls = {"n": 0}
    real = merge.plan_merge

    def flaky(items: list[MergeItem], threshold: float) -> MergePlan:
        calls["n"] += 1
        plan = real(items, threshold)
        return (
            plan if calls["n"] == 1 else MergePlan({k: k for k in plan.canonical_of}, frozenset())
        )

    monkeypatch.setattr(merge, "plan_merge", flaky)
    with pytest.raises(MergeError):
        merge.stable_plan([_item("a", U1, "J"), _item("b", U1, "J")], 0.5, 2)


# ---- level, lane, exclusion, priority -------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "level"),
    [
        ("Senior Manager, CRM", "senior_manager"),
        ("Head of Platforms", "head"),
        ("Director, Field Service", "director"),
        ("VP Business Applications", "vp"),
        ("Senior Vice President, IT", "svp"),
        ("Chief Information Officer", "c_level"),
        ("Manager, CRM", "manager"),
        ("Program Lead", None),
        (None, None),
    ],
)
def test_level(cfg: PeopleConfig, title: str | None, level: str | None) -> None:
    hit = classify_level(title, cfg.level_rules, cfg.level_order)
    assert (hit.level if hit else None) == level


def test_lane(cfg: PeopleConfig) -> None:
    hits = lanes_for("Director, Salesforce and SAP", None, cfg.lanes)
    assert [h.lane for h in hits] == ["salesforce", "sap"]
    assert [h.lane for h in lanes_for("Director", "Leads the ServiceNow team", cfg.lanes)] == [
        "servicenow"
    ]
    assert lanes_for("Director of Data and AI", None, cfg.lanes) == ()  # no terms until supplied


def test_exclusion_beats_inclusion_and_review(cfg: PeopleConfig) -> None:
    v = check_exclusion(
        "Director, Salesforce Security and Compliance", cfg.exclusion_rules, cfg.review_rules
    )
    assert v.outcome == "excluded" and v.reason == "security" and v.evidence == "Security"
    v = check_exclusion("Head of IT Audit", cfg.exclusion_rules, cfg.review_rules)
    assert (v.outcome, v.reason, v.rule_id) == ("excluded", "it_audit", "excl.it_audit")
    v = check_exclusion("Director, IT Compliance", cfg.exclusion_rules, cfg.review_rules)
    assert v.reason == "it_compliance"
    v = check_exclusion("Director, Compliance Platforms", cfg.exclusion_rules, cfg.review_rules)
    assert (v.outcome, v.reason) == ("review", "exclusion_ambiguous")
    assert check_exclusion(None, cfg.exclusion_rules, cfg.review_rules).outcome == "include"


def test_priority(cfg: PeopleConfig) -> None:
    assert assign_priority("senior", True, cfg.priority_rules).priority == "A"  # type: ignore[union-attr]
    assert assign_priority("senior", False, cfg.priority_rules).priority == "B"  # type: ignore[union-attr]
    assert assign_priority("manager", True, cfg.priority_rules).priority == "B"  # type: ignore[union-attr]
    assert assign_priority("manager", False, cfg.priority_rules).priority == "C"  # type: ignore[union-attr]
    assert assign_priority("other", False, cfg.priority_rules) is None


# ---- scope ----------------------------------------------------------------------------------


def _scope(**kw: object) -> ScopeInput:
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


@pytest.mark.parametrize(
    ("kw", "regions", "status", "reason"),
    [
        ({}, (), "in_scope", None),
        ({"merged": True}, (), "merged", None),
        ({"is_current": False}, (), "out_of_scope", "not_current"),
        ({"employer_domain": "other.example"}, (), "out_of_scope", "not_current"),
        ({"employer_domain": "www.eu." + DOMAIN, "search_domain": None}, (), "in_scope", None),
        ({"title": "Director, Information Security"}, (), "excluded", "security"),
        ({"title": "Director, Information Security", "has_url": False}, (), "excluded", "security"),
        ({"has_url": False}, (), "unresolved", None),
        ({"search_domain": None}, (), "review", "employment_unknown"),
        ({"search_domain": None, "is_current": True}, (), "in_scope", None),
        ({"title": "Program Lead"}, (), "review", "level_unknown"),
        ({"title": "Manager, Salesforce"}, (), "out_of_scope", "below_scope"),
        ({"location": None}, ("Germany",), "review", "location_unknown"),
        ({"location": "Pune, India"}, ("Germany",), "out_of_scope", "outside_geography"),
        ({"location": "Berlin, Germany"}, ("Germany",), "in_scope", None),
        ({"title": "Director, Compliance Systems"}, (), "review", "exclusion_ambiguous"),
    ],
)
def test_scope_rules(
    cfg: PeopleConfig,
    kw: dict[str, object],
    regions: tuple[str, ...],
    status: str,
    reason: str | None,
) -> None:
    c = classify(_scope(**kw), cfg, DOMAIN, regions)
    assert (c.status, c.reason) == (status, reason)
    assert c.rule_id


def test_scope_priority_unmatched(cfg: PeopleConfig) -> None:
    from dataclasses import replace

    narrowed = replace(cfg, priority_rules=())
    assert classify(_scope(), narrowed, DOMAIN).reason == "priority_unmatched"


def test_in_scope_carries_level_lane_priority_and_evidence(cfg: PeopleConfig) -> None:
    c = classify(_scope(title="VP, SAP Platforms"), cfg, DOMAIN)
    assert (c.level, c.lanes, c.priority, c.evidence, c.priority_rule) == (
        "vp",
        ("sap",),
        "A",
        "VP",
        "prio.a.senior_lane",
    )


def test_classification_rejects_inconsistent_values() -> None:
    with pytest.raises(ValueError):
        Classification(status="excluded")
    with pytest.raises(ValueError):
        Classification(status="in_scope", level="director")
    with pytest.raises(ValueError):
        Classification(status="review", reason="x", priority="A")


# ---- dates and tenure -----------------------------------------------------------------------


def test_partial_date() -> None:
    assert PartialDate.parse("2019").precision == "year"  # type: ignore[union-attr]
    month = PartialDate.parse("2019-02")
    assert month is not None and month.precision == "month" and month.latest() == date(2019, 2, 28)
    assert PartialDate.parse("2020-12").latest() == date(2020, 12, 31)  # type: ignore[union-attr]
    day = PartialDate.parse("2019-02-03T10:00:00Z")
    assert day is not None and day.iso() == "2019-02-03" and day.latest() == day.earliest()
    assert PartialDate.parse("2019").iso() == "2019" and month.iso() == "2019-02"  # type: ignore[union-attr]
    assert PartialDate.parse("2019").latest() == date(2019, 12, 31)  # type: ignore[union-attr]
    for bad in (None, 2019, "", "19-02", "2019-13", "2019-02-30", "0000"):
        assert PartialDate.parse(bad) is None
    with pytest.raises(ValueError):
        PartialDate(year=2019, day=3)


BUCKETS = (
    TenureBucket("under_1y", 0, 12),
    TenureBucket("1_to_3y", 12, 36),
    TenureBucket("3_to_5y", 36, 60),
    TenureBucket("5y_plus", 60, None),
)
AS_OF = date(2026, 10, 3)


def test_months_and_buckets() -> None:
    assert months_between(date(2025, 10, 3), AS_OF) == 12
    assert months_between(date(2025, 10, 4), AS_OF) == 11
    assert months_between(date(2027, 1, 1), AS_OF) == 0
    assert bucket_of(0, BUCKETS) == "under_1y" and bucket_of(60, BUCKETS) == "5y_plus"
    with pytest.raises(ValueError):
        bucket_of(-1, BUCKETS)


@pytest.mark.parametrize(
    ("join", "role", "status", "bucket", "reason"),
    [
        ("2020-01-15", None, "known", "5y_plus", None),
        ("2024-03", None, "known", "1_to_3y", None),
        ("2023", None, "unknown", None, "precision"),  # 33 to 45 months: two buckets
        ("2018", None, "known", "5y_plus", None),
        (None, "2026-01", "known", "under_1y", None),
        (None, None, "unknown", None, "no_date"),
        ("2027-01", None, "date_invalid", None, "future_date"),
        (None, "2026-11", "date_invalid", None, "future_date"),
        ("2022-05", "2021-02", "date_invalid", None, "role_start_before_join"),
        ("2026", None, "known", "under_1y", None),
    ],
)
def test_tenure(
    join: str | None, role: str | None, status: str, bucket: str | None, reason: str | None
) -> None:
    t = compute_tenure(PartialDate.parse(join), PartialDate.parse(role), AS_OF, BUCKETS)
    assert (t.status, t.bucket, t.reason) == (status, bucket, reason)


def test_tenure_basis() -> None:
    t = compute_tenure(None, PartialDate.parse("2025-01"), AS_OF, BUCKETS)
    assert (t.basis, t.months_min, t.months_max) == ("role_start", 20, 21)  # Jan 1 to Jan 31


# ---- certifications and alumni --------------------------------------------------------------


def test_certifications_match_lane_lexicon(cfg: PeopleConfig) -> None:
    assert certified_lanes(
        ["Salesforce Certified Administrator", "SAP S/4 Consultant"], cfg.lanes
    ) == ("salesforce", "sap")
    assert certified_lanes(["Project Management Professional"], cfg.lanes) == ()
    assert certified_lanes([], cfg.lanes) == ()


def test_alumni_matching() -> None:
    refs = reference_set(["Synthetic Client Co"], {"Synthetic Client Co": ["SCC Group"]})
    assert matched_employer(["Other Org", "synthetic client co."], refs) == "synthetic client co."
    assert matched_employer(["SCC  Group"], refs) == "SCC  Group"
    assert matched_employer(["Unrelated"], refs) is None
    assert reference_set(["  "], {}) == frozenset()
    assert reference_set(["A"], {"B": ["C"]}) == {"a"}


def test_person_key_is_stable_and_attribute_safe() -> None:
    from people.pipeline import person_key

    key = person_key("r01")
    assert key == person_key("r01") and key != person_key("r02")
    assert key.startswith("p") and len(key) == 25 and key.isalnum()
