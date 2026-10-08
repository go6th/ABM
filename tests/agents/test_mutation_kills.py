"""Tests written from mutmut survivors on the guard modules (guards, modes, manifest, lessons):
exact codes and messages, every plan field per branch, shelf-life boundaries and pinned digests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from agents.errors import GuardViolation, InvalidMode, NoPulledData, StalePullsNeedDecision
from agents.guards import (
    DropDecision,
    facts_digest,
    internal_history_allowed,
    lead_check,
    lead_drop_decision,
    renderable,
    require_sense_check,
)
from agents.lessons import (
    generate_entries,
    lessons_check,
    parse_lessons_md,
    require_guard,
    stub_tests,
)
from agents.manifest import (
    ManifestRow,
    PullManifest,
    QueryRecord,
    check_build_precondition,
    stale_rows,
)
from agents.modes import RunMode, RunPlan, plan_run, resolve_mode
from agents.ports import InternalHistorySettings, SenseCheckRecord
from agents.schemas import StoredFact

T0 = datetime(2026, 10, 8, tzinfo=UTC)
TENANT, PARENT = uuid4(), uuid4()
PINNED = "3ae6ae74a6b7001d96737f6233f259232394bfa0bcf4800ac201020e1ca41f4c"


def raises(fn, guard: str, code: str, detail: str = "") -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(GuardViolation) as error:
        fn()
    assert (error.value.guard, error.value.code, error.value.detail) == (guard, code, detail)


# guards ----------------------------------------------------------------------------------------


def test_guard_ids_codes_and_details_are_exact() -> None:
    raises(
        lambda: lead_check("date_range", False, "probable"),
        "G2",
        "unread_match_promoted",
        "probable",
    )
    rec = SenseCheckRecord(uuid4(), "m", "f", 0, 0)
    raises(lambda: require_sense_check(None, "m", "f"), "G6", "sense_check_missing")
    raises(lambda: require_sense_check(rec, "x", "f"), "G6", "sense_check_stale")
    raises(lambda: renderable("private", "public"), "G10", "private_to_public_renderer")
    raises(lambda: renderable("odd", "internal"), "G10", "unknown_visibility", "odd")
    raises(
        lambda: internal_history_allowed(InternalHistorySettings(), True),
        "G10",
        "internal_history_default_on",
    )


def q(text: str, tool: str) -> QueryRecord:
    return QueryRecord(text=text, tool_category=tool, result_count=0)


def test_drop_decisions_carry_exact_counts() -> None:
    two = [q("a b", "web"), q("c d", "news")]
    assert lead_drop_decision(two, 3, 2) == DropDecision(False, "too_few_phrasings", 2, 2)
    one_tool = [q("a", "web"), q("b", "web"), q("c", "web")]
    assert lead_drop_decision(one_tool, 3, 2) == DropDecision(
        False, "too_few_tool_categories", 3, 1
    )
    ok = [q("a", "web"), q("b", "news"), q("c", "web")]
    assert lead_drop_decision(ok, 3, 2) == DropDecision(True, None, 3, 2)


def test_facts_digest_is_pinned() -> None:
    fact = StoredFact(fact_id="f1", row_key="r", attribute="a", value="v", grade="lead")
    other = fact.model_copy(update={"fact_id": "f2", "grade": "probable"})
    import hashlib

    assert facts_digest([fact, other]) == facts_digest([other, fact])

    assert facts_digest([fact, other]) == hashlib.sha256(b"f1:lead\nf2:probable").hexdigest()


# modes -----------------------------------------------------------------------------------------


def test_invalid_mode_messages_are_exact() -> None:
    with pytest.raises(InvalidMode) as error:
        resolve_mode("both")
    assert error.value.detail == "mode must be omitted or one of full, pull, build"
    cases = [
        (
            dict(parent_run_id=None, parent_mode=RunMode.PULL, parent_config_version="v"),
            "a build run needs the pull run it builds on",
        ),
        (
            dict(parent_run_id=PARENT, parent_mode=None, parent_config_version="v"),
            "a build run needs the pull run it builds on",
        ),
        (
            dict(parent_run_id=PARENT, parent_mode=RunMode.PULL, parent_config_version=None),
            "a build run needs the pull run it builds on",
        ),
        (
            dict(parent_run_id=PARENT, parent_mode=RunMode.BUILD, parent_config_version="v"),
            "a build run's parent must be a pull or full run",
        ),
    ]
    for kwargs, message in cases:
        with pytest.raises(InvalidMode) as error:
            plan_run(
                RunMode.BUILD, requested_config_version=None, current_config_version="c", **kwargs
            )  # type: ignore[arg-type]
        assert error.value.detail == message
    with pytest.raises(InvalidMode) as error:
        plan_run(
            RunMode.FULL,
            parent_run_id=PARENT,
            parent_mode=None,
            parent_config_version=None,
            requested_config_version=None,
            current_config_version="c",
        )
    assert error.value.detail == "a full run has no parent run"


def test_every_plan_field_per_branch() -> None:
    common = dict(parent_mode=None, parent_config_version=None, current_config_version="cur")
    assert plan_run(
        RunMode.PULL, parent_run_id=None, requested_config_version=None, **common
    ) == RunPlan(RunMode.PULL, True, False, None, "cur", "current")  # type: ignore[arg-type]
    assert plan_run(
        RunMode.FULL, parent_run_id=None, requested_config_version="new", **common
    ) == RunPlan(
        RunMode.FULL,
        True,
        True,
        None,
        "new",  # type: ignore[arg-type]
        "explicit_override",
    )
    assert plan_run(
        RunMode.BUILD,
        parent_run_id=PARENT,
        parent_mode=RunMode.FULL,
        parent_config_version="old",
        requested_config_version=None,
        current_config_version="cur",
    ) == RunPlan(RunMode.BUILD, False, True, PARENT, "old", "inherited")


# manifest --------------------------------------------------------------------------------------


def row(key: str, status: str = "stored", at: datetime = T0) -> ManifestRow:
    if status == "stored":
        return ManifestRow(
            row_key=key, status="stored", payload_refs=("p",), call_refs=("c",), pulled_at=at
        )
    return ManifestRow(row_key=key, status="blocked", blocked_reason="x", pulled_at=at)


def manifest(*rows: ManifestRow) -> PullManifest:
    return PullManifest(
        tenant_id=TENANT,
        run_id=uuid4(),
        account_key="a",
        config_version="v",
        created_at=T0,
        rows=rows,
    )


def test_stale_rows_boundaries() -> None:
    m = manifest(
        row("news", at=T0 - timedelta(days=7)),
        row("old", "blocked", T0 - timedelta(days=99)),
        row("tech", at=T0 - timedelta(days=7, seconds=1)),
        row("free"),
    )
    shelf = {"news": 7, "old": 7, "tech": 7, "free": None}
    assert stale_rows(m, shelf, T0) == ("tech",)  # exactly at the limit is fresh; blocked never
    assert stale_rows(m, {}, T0) == ()


def test_build_precondition_details() -> None:
    m = manifest(row("a", at=T0 - timedelta(days=10)), row("b", "blocked"), row("c"))
    shelf = {"a": 7, "b": 7, "c": 7}

    def check(required=("a", "b", "c"), ack=frozenset(), **kw):  # type: ignore[no-untyped-def]
        return check_build_precondition(
            kw.get("m", m),
            tenant_id=TENANT,
            account_key="a",
            required_rows=required,
            shelf_life_days=shelf,
            now=T0,
            acknowledged_stale=ack,
        )

    with pytest.raises(NoPulledData) as error:
        check(m=None)
    assert error.value.detail == "no pull manifest for this tenant and account"
    with pytest.raises(NoPulledData) as error:
        check(required=("a", "z", "y"))
    assert error.value.missing == ("y", "z") and error.value.detail == "2 row(s) missing"
    with pytest.raises(StalePullsNeedDecision) as stale:
        check()
    assert stale.value.rows == ("a",) and stale.value.detail == "1 row(s) past shelf life"
    pre = check(ack=frozenset({"a"}))
    assert (pre.manifest, pre.acknowledged_stale, pre.blocked_rows) == (m, ("a",), ("b",))


def test_manifest_digest_and_cost() -> None:
    m = manifest(row("a"))
    assert m.digest() == m.model_copy().digest() and len(m.digest()) == 64
    assert m.digest() != manifest(row("b")).digest()
    costly = manifest(
        ManifestRow(
            row_key="a",
            status="stored",
            payload_refs=("p",),
            call_refs=("c",),
            pulled_at=T0,
            cost_micros=7,
        ),
        ManifestRow(
            row_key="b",
            status="stored",
            payload_refs=("q",),
            call_refs=("c",),
            pulled_at=T0,
            cost_micros=5,
        ),
    )
    assert costly.total_cost_micros() == 12


# lessons ---------------------------------------------------------------------------------------

MD = "- before any heading\n## 2026-09-21\n- one rule\n"


def test_generated_entries_and_stub_text_are_exact() -> None:
    entries = generate_entries(parse_lessons_md(MD))
    assert entries == [
        {
            "id": "L-20260921-1",
            "date": "2026-09-21",
            "source": "lessons.md:3",
            "rule": "one rule",
            "enforcement": "process",
            "guard": None,
            "test": "test_lesson_l_20260921_1",
        }
    ]
    assert stub_tests(entries) == (
        '"""Generated by scripts/abm_lessons.py: one test per lesson (D-99). Replace each body\n'
        'with a test of the lesson\'s guard; a lesson left as ``process`` fails here."""\n'
        "\nfrom agents.lessons import require_guard\n\n\n"
        "def test_lesson_l_20260921_1() -> None:\n"
        "    require_guard('L-20260921-1')\n"
    )


def good() -> dict:  # type: ignore[type-arg]
    return {
        "id": "L-20260921-1",
        "source": "lessons.md:3",
        "rule": "r",
        "enforcement": "code",
        "guard": "G1",
        "test": "test_guard_ids_codes_and_details_are_exact",
    }


def test_lessons_check_states_are_exact(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from pathlib import Path

    tests, prompts = Path("tests/agents"), Path("src/agents")
    ok = lessons_check([good()], "present", tests, prompts)
    assert (ok.status, ok.problems, ok.checked) == ("ok", (), 1)
    absent = lessons_check([good()], "absent", tests, prompts)
    assert (absent.status, absent.problems, absent.checked) == (
        "incomplete",
        (("*", "archive_absent"),),
        1,
    )
    empty = lessons_check([], "present", tests, prompts)
    assert (empty.status, empty.checked) == ("incomplete", 0)
    failed = lessons_check([{**good(), "guard": None}], "present", tests, prompts)
    assert (failed.status, failed.checked) == ("failed", 1)
    for missing in ("id", "source"):
        entry = {k: v for k, v in good().items() if k != missing}
        assert lessons_check([entry], "present", tests, prompts).status == "failed"
    require_guard("L-20260921-1", [good()])


def test_manifest_digest_is_pinned_across_versions() -> None:
    # Stored digests bind sense checks and builds (G6): the canonical form must never drift.
    from uuid import UUID

    m = PullManifest(
        tenant_id=UUID(int=1),
        run_id=UUID(int=2),
        account_key="a",
        config_version="v",
        created_at=T0,
        rows=(row("a"),),
    )
    assert m.digest() == PINNED


def test_routing_check_continues_past_code_only_agents() -> None:
    from agents.guards import routing_violations

    tiers = {"t": "planner"}
    assert routing_violations({"a_code": None, "pass2": "t"}, tiers, "extractor", "planner") == (
        "pass2:t->planner",
    )
