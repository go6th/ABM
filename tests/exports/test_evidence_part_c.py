"""Sources and Evidence Part C, "Postings read one by one, by lane" (the owner's reference file
set, BD, 2026-10-19): one sub-heading per lane platform a posting names, worded with the postings
read in full, the postings naming it and the open ones; a table of the read postings (Ref,
Posting, Class, Status, Classification reasoning) or a note when none was read."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from account_intelligence.grading.config import GradingConfig
from exports.builder import ExportBuilder, map_snapshot
from exports.config import ExportConfig
from exports.content import SnapshotContent
from exports.verify import docx_text, keys_once_in_order
from tests.exports.corpus import AS_OF, account
from tests.fakes.exports import MemoryStore

Make = Callable[..., ExportBuilder]


def evidence_of(content: SnapshotContent, cfg: ExportConfig, grading: GradingConfig) -> Any:
    mapped, ctx = map_snapshot(content, cfg, grading)
    assert not ctx.violations, ctx.violations
    return mapped["evidence"]


def with_postings(c: SnapshotContent, **changes: dict[str, Any]) -> SnapshotContent:
    """``changes`` maps a posting key to the fields it changes."""
    postings = tuple(
        p.model_copy(update=changes[p.posting_key]) if p.posting_key in changes else p
        for p in c.postings
    )
    return c.model_copy(update={"postings": postings})


def test_part_c_groups_the_full_reads_by_lane_platform(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    c = account()
    ev = evidence_of(c, cfg, grading)
    (g,) = ev.model.lane_reads
    labels = cfg.rules.labels
    # three postings name Salesforce; 0 and 2 were read in full; 0 is open
    assert (g.platform, g.read, g.named, g.open_now) == ("Salesforce", 2, 3, 1)
    on = f"{AS_OF.day} {AS_OF:%b %Y}"
    assert g.heading == (
        f"Salesforce: 2 postings read in full (3 {c.account_name} postings name it, 1 open on {on})"
    )
    assert [(r.ref, r.posting_ref) for r in g.rows] == [
        ("LP-0001", "J-0001"),
        ("LP-0002", "J-0003"),
    ]
    first = g.rows[0]
    assert first.status == labels.posting_status["open"]
    assert first.posting_class == labels.posting_class["admin_owner"]
    assert first.first_seen == "2026-01-01"
    assert first.reasoning == "Synthetic sentence naming Salesforce 0."
    # the rows sit in document order: one block right after the rows of parts A and B
    refs = list(ev.refs)
    at = refs.index("LP-0001")
    assert refs[at : at + 2] == ["LP-0001", "LP-0002"]
    before = {s.ref for s in ev.model.sources} | {j.ref for j in ev.model.job_descriptions}
    assert set(refs[:at]) == before and not before & set(refs[at:])
    assert [r for r in ev.row_ids if r.startswith("lane:")] == [
        "lane:Salesforce:post-00000",
        "lane:Salesforce:post-00002",
    ]


def test_part_c_reasoning_prefers_the_stored_class_sentence(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    c = with_postings(account(), **{"post-00000": {"class_sentence": "Synthetic class reason."}})
    (g,) = evidence_of(c, cfg, grading).model.lane_reads
    assert g.rows[0].reasoning == "Synthetic class reason."
    assert g.rows[1].reasoning == "Synthetic sentence naming Salesforce 2."


def test_part_c_a_posting_naming_two_platforms_is_listed_under_each(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    c = with_postings(
        account(),
        **{
            "post-00002": {
                "platforms": ("Salesforce", "MuleSoft"),
                "platform_sentences": (
                    "Synthetic sentence naming Salesforce 2.",
                    "Synthetic sentence naming MuleSoft 2.",
                ),
            }
        },
    )
    groups = evidence_of(c, cfg, grading).model.lane_reads
    assert [g.platform for g in groups] == ["Salesforce", "MuleSoft"]
    sf, mule = groups
    assert [r.posting_ref for r in mule.rows] == ["J-0003"]
    assert (mule.read, mule.named, mule.open_now) == (1, 1, 0)
    # one row per lane, each with its own key; the reasoning keeps that platform's sentence
    assert mule.rows[0].ref == "LP-0003" and sf.rows[-1].ref == "LP-0002"
    assert mule.rows[0].reasoning == "Synthetic sentence naming MuleSoft 2."


def test_part_c_without_a_full_read_keeps_the_heading_and_a_note(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    c = with_postings(
        account(), **{k: {"full_text_read": False} for k in ("post-00000", "post-00002")}
    )
    (g,) = evidence_of(c, cfg, grading).model.lane_reads
    assert (g.read, g.named, g.rows) == (0, 3, ())


@pytest.mark.asyncio
async def test_part_c_renders_a_heading_and_table_per_lane(
    make_builder: Make, cfg: ExportConfig, grading: GradingConfig
) -> None:
    c = with_postings(
        account(),
        **{
            "post-00001": {"platforms": ("ServiceNow",)},
            "post-00002": {"platforms": ("Salesforce", "MuleSoft")},
        },
    )
    store = MemoryStore()
    result = await make_builder(c, store).build_all(c.tenant_id, c.run_id)
    assert result.status == "complete", result.errors
    d = next(x for x in result.deliverables if x.kind == "evidence")
    text = docx_text(store.objects[d.object_key])
    model = evidence_of(c, cfg, grading).model
    rules = cfg.rules.evidence
    part_c = text.index(cfg.rules.headings.evidence.careers_postings)
    part_d = text.index(cfg.rules.headings.evidence.post_extracts)
    for g in model.lane_reads:
        assert part_c < text.index(g.heading) < part_d
    # ServiceNow is named by one posting that was not read in full: a note, no table
    assert "ServiceNow: 0 postings read in full" in text
    assert text.count(rules.lane_read_none) == 1
    assert part_c < text.index(rules.lane_read_note) < part_d
    for header in cfg.rules.documents.tables["lane_reads"]:
        assert header in text
    keys = [r.ref for g in model.lane_reads for r in g.rows]
    assert keys == ["LP-0001", "LP-0002", "LP-0003"]
    assert keys_once_in_order(text, keys) == ()
