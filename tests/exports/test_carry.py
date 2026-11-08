"""A refresh of part of the checklist is built on the previous delivered research
(``exports.carry``; staging 2026-11-07: a topic refresh replaced Olympus's 356 people and 144
postings with an empty file set)."""

from __future__ import annotations

from account_intelligence.grading.config import GradingConfig
from exports.builder import map_snapshot
from exports.carry import carry_forward, scope_of, searched
from exports.config import ExportConfig
from exports.content import SectionRow, SnapshotContent
from tests.exports.corpus import account

TOPIC = frozenset(
    {
        "company_news",
        "competitor_moves",
        "technology_detections",
        "vendor_customer_stories",
        "executive_speaking",
    }
)
JOBS = frozenset({"job_board_by_lane", "job_postings_12_months", "job_postings_read_classified"})


def _previous() -> SnapshotContent:
    return account("Synthetic Alpha Ltd", n_facts=6, n_people=4, n_postings=3, n_detections=2)


def _refresh(previous: SnapshotContent) -> SnapshotContent:
    """What a topic refresh's own rows read: one fact found again, one new fact, a section of
    its own, no people record, no posting record, no stack."""
    base = account(
        "Synthetic Alpha Ltd",
        run="00000000-0000-4000-8000-00000000a002",
        n_facts=0,
        n_people=0,
        n_postings=0,
        n_detections=0,
        n_stack=0,
    )
    again, new = previous.facts[0], previous.facts[1]
    facts = (
        again.model_copy(update={"id": "50000000-0000-4000-8000-000000000001"}),
        new.model_copy(
            update={
                "id": "50000000-0000-4000-8000-000000000002",
                "attribute_key": "news.synthetic_new",
                "value_text": "A new synthetic finding of the refresh",
            }
        ),
    )
    evidence = tuple(
        e.model_copy(update={"id": f"6{e.id[1:]}", "fact_id": f.id})
        for f, e in zip(
            facts,
            (
                next(e for e in previous.evidence if e.fact_id == again.id),
                next(e for e in previous.evidence if e.fact_id == new.id),
            ),
            strict=True,
        )
    )
    return base.model_copy(
        update={
            "facts": facts,
            "evidence": evidence,
            "people_counts": None,
            "jobs_counts": None,
            "people_names": (),
            "sections": (SectionRow(id="s-topic-1", kind="narrative", position=0, text="x"),),
        }
    )


def test_scope_of_names_only_a_partial_run() -> None:
    assert scope_of({"mode": "full", "scope": "full"}) is None
    assert scope_of({"scope_rows": []}) is None
    assert scope_of(None) is None
    assert scope_of({"scope_rows": sorted(TOPIC)}) == TOPIC
    assert not searched(TOPIC, "people") and not searched(TOPIC, "jobs")
    assert searched(JOBS, "jobs") and not searched(JOBS, "people")


def test_a_topic_refresh_keeps_the_people_postings_and_findings_of_the_research() -> None:
    previous = _previous()
    current = _refresh(previous)
    merged = carry_forward(current, previous, TOPIC)

    assert merged.run_id == current.run_id
    assert merged.people == previous.people and merged.people_counts == previous.people_counts
    assert merged.findings == previous.findings
    assert merged.postings == previous.postings and merged.jobs_counts == previous.jobs_counts
    assert merged.stack == previous.stack
    # the refresh's facts first; the fact it found again is not carried twice
    ids = [f.id for f in merged.facts]
    assert ids[:2] == [f.id for f in current.facts]
    assert previous.facts[0].id not in ids
    assert len(merged.facts) == len(previous.facts) + 1
    assert {e.fact_id for e in merged.evidence} == set(ids)
    # the Summary's written sections describe the whole account: the research's stand
    assert [s.id for s in merged.sections] == [s.id for s in previous.sections]
    assert merged.snapshot_counts.get("facts", len(merged.facts)) == len(merged.facts)


def test_a_jobs_refresh_keeps_its_own_postings_even_when_it_found_none() -> None:
    previous = _previous()
    current = _refresh(previous)
    merged = carry_forward(current, previous, JOBS)
    assert merged.postings == () and merged.jobs_counts is None
    assert merged.people == previous.people


def test_names_stay_listed_when_either_run_listed_them() -> None:
    previous = _previous().model_copy(update={"people_names": (("Ann Example", True),)})
    current = _refresh(previous).model_copy(
        update={"people_names": (("Ann Example", False), ("Bob Example", False))}
    )
    merged = carry_forward(current, previous, TOPIC)
    assert dict(merged.people_names) == {"Ann Example": True, "Bob Example": False}


def test_the_merged_content_maps_to_every_file(cfg: ExportConfig, grading: GradingConfig) -> None:
    previous = _previous()
    merged = carry_forward(_refresh(previous), previous, TOPIC)
    mapped, ctx = map_snapshot(merged, cfg, grading)
    assert not ctx.violations, ctx.violations
    assert len(mapped["people"].model.rows) == len(previous.people)  # type: ignore[attr-defined]
