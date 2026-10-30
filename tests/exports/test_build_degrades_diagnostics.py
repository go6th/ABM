"""When something is withheld or a build stops, the manifest and the log say which file, which
part of it and which rule, by counts and neutral rule names, so the next failure can be read
without database access (the owner cannot run SQL on staging). They never carry the matched text,
another account's name or an address.

Staging 2026-10-31: ``exports.build_failed error_code="leak,tool_name"
reason="leak=2,leak=5,tool_name=1"`` named no file, and ``tool_name`` was the label of three
different rules."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from exports import obs
from exports.builder import ExportBuilder
from exports.config import ExportConfig
from exports.word.contract import DocumentSpec, TableBlock
from tests.exports.corpus import account
from tests.exports.degrade_kit import OTHER_DOMAIN, OTHER_NAME, Make, build, log_lines
from tests.exports.test_build_degrades_restricted import INJECTIONS, with_terms
from tests.fakes.exports import FakeReader, InProcessRenderer, MemoryStore

pytestmark = pytest.mark.asyncio


class DroppingRenderer(InProcessRenderer):
    """A renderer that silently loses the last row of every table (a structural failure)."""

    def render(self, spec: DocumentSpec) -> bytes:
        blocks = []
        for b in spec.blocks:
            if isinstance(b, TableBlock) and b.rows:
                b = b.model_copy(update={"rows": b.rows[:-1], "keys": b.keys[:-1]})
            blocks.append(b)
        return super().render(spec.model_copy(update={"blocks": tuple(blocks)}))


async def test_a_failed_build_logs_the_file_the_rule_and_the_count(
    cfg: ExportConfig, grading: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    c = account()
    builder = ExportBuilder(FakeReader(c), MemoryStore(), DroppingRenderer(), cfg, grading)
    result = await builder.build_all(c.tenant_id, c.run_id)
    assert result.status == "failed"
    (record,) = log_lines(caplog, "exports.build_failed")
    # old code: reason="row_conservation=2" (no file); now each file is named
    assert record.reason == "summary.row_conservation=2,evidence.row_conservation=2"  # type: ignore[attr-defined]
    assert record.error_code == "row_conservation"  # type: ignore[attr-defined]


async def test_withheld_rows_are_logged_by_file_part_and_rule(
    make_builder: Make, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    c = account()
    postings = (
        c.postings[0].model_copy(update={"url": None}),
        c.postings[1].model_copy(update={"title": " "}),
        *c.postings[2:],
    )
    result, _ = await build(make_builder, c.model_copy(update={"postings": postings}))
    assert result.status == "complete", result.errors
    (record,) = log_lines(caplog, "exports.withheld")
    assert record.error_code == "jobs:missing_url=1,jobs:text_missing=1"  # type: ignore[attr-defined]


async def test_a_file_left_out_is_logged_as_a_warning_with_kind_and_rule(
    make_builder: Make, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    c = account().model_copy(
        update={"sections": tuple(s for s in account().sections if s.kind != "narrative")}
    )
    result, _ = await build(make_builder, c)
    assert result.withheld_files == ("summary:missing_narrative",)
    (record,) = log_lines(caplog, "exports.file_withheld")
    assert record.levelno == logging.WARNING
    assert record.error_code == "summary:missing_narrative"  # type: ignore[attr-defined]


async def test_texts_replaced_by_the_withheld_note_are_logged_by_part_and_rule(
    make_builder: Make, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    c = INJECTIONS["posting_description"](with_terms(account(), OTHER_NAME))
    result, _ = await build(make_builder, c)
    assert result.status == "complete", result.errors
    lines = log_lines(caplog, "exports.leaks_withheld")
    assert lines and "jobs:leak=" in lines[0].error_code  # type: ignore[attr-defined]


async def test_nothing_the_logs_or_the_manifest_say_carries_the_matched_text(
    make_builder: Make, caplog: pytest.LogCaptureFixture
) -> None:
    """Canary: another account's name and domain are planted in a dozen places. The result is
    delivered, and neither the log nor the manifest (the zip embeds it) nor the result's errors
    mention either."""
    caplog.set_level(logging.DEBUG)
    c = with_terms(account(), OTHER_NAME, OTHER_DOMAIN)
    for path in (
        "fact",
        "caveat",
        "person_name",
        "posting_title",
        "posting_description",
        "detection",
        "stack",
        "finding_claim",
        "source_title",
        "jobs_count_label",
        "pull_query",
        "lookalike",
    ):
        c = INJECTIONS[path](c)
    result, _ = await build(make_builder, c)
    assert result.status == "complete", result.errors
    heard = (caplog.text + result.manifest.model_dump_json() + " ".join(result.errors)).casefold()
    assert OTHER_NAME.casefold() not in heard and OTHER_DOMAIN not in heard
    # and the planted account's name is not in the leaks's own words either
    assert result.manifest.texts_withheld and result.manifest.leaks_withheld


def test_where_labels_lose_their_row_ids_and_counts_are_sorted() -> None:
    sid = "5b0c2d1e-1111-4222-8333-444455556666"
    assert obs.where_part(f"summary.sections.narrative.{sid}") == "summary.sections.narrative"
    assert obs.where_part("jobs") == "jobs"
    lines = obs.count_lines({("jobs", "leak"): 2, ("evidence", "cost_word"): 1})
    assert lines == ("evidence:cost_word=1", "jobs:leak=2")


def test_a_long_list_is_logged_in_pieces_that_fit_one_field() -> None:
    lines = tuple(f"summary.lanes.part{i}:aggregator_not_lead={i}" for i in range(40))
    pieces = list(obs._chunks(lines))
    assert len(pieces) > 1 and all(len(p) <= obs.LOG_FIELD for p in pieces)
    assert ",".join(pieces) == ",".join(lines)  # nothing cut, nothing lost
