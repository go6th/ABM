"""When one file cannot be made, that file alone is left out and the research is delivered.

Until 2026-10-31 a Summary with no narrative, a file over its row cap, a renderer that failed on
one document, or a produced file the final scans refused, ended the whole build
(``build_failed``) after the research had been paid for, and none of the files was delivered. Now
the file is left out under a named rule (``files_withheld`` in the manifest and the result), the
other files and the workspace zip are built and stored, and the run ends PARTIAL
``files_withheld`` (``documents.gate``). What is STRUCTURAL still refuses the set: a package
that would be wrong (a lost row, a bad vocabulary, a private row) is never delivered.

Synthetic data only."""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import pytest
from pydantic import ValidationError

from exports.builder import ExportBuilder, FileHeld
from exports.config import ExportConfig
from exports.content import SnapshotContent
from exports.errors import RenderError
from exports.models import LaneRow
from exports.verify import docx_text
from exports.word.contract import DocumentSpec
from tests.exports.corpus import account
from tests.exports.degrade_kit import Make, build, kinds_of
from tests.fakes.exports import FakeReader, InProcessRenderer, MemoryStore

pytestmark = pytest.mark.asyncio
ALL = {"summary", "evidence", "people", "jobs", "tech", "workspace"}


def members(store: MemoryStore, result: Any) -> dict[str, bytes]:
    """The workspace zip's members."""
    d = next(x for x in result.deliverables if x.kind == "workspace")
    with zipfile.ZipFile(io.BytesIO(store.objects[d.object_key])) as z:
        return {n: z.read(n) for n in z.namelist()}


def without_narrative(c: SnapshotContent) -> SnapshotContent:
    """``c`` with no narrative section."""
    return c.model_copy(update={"sections": tuple(s for s in c.sections if s.kind != "narrative")})


def small_cfg(cfg: ExportConfig, **caps: int) -> ExportConfig:
    """``cfg`` with lower row caps."""
    limits = cfg.rules.limits.model_copy(
        update={"row_caps": cfg.rules.limits.row_caps.model_copy(update=caps)}
    )
    return cfg.model_copy(update={"rules": cfg.rules.model_copy(update={"limits": limits})})


# ---- a file the run did not or could not produce ---------------------------------------------


async def test_a_summary_with_no_narrative_is_held_back_alone(
    make_builder: Make, cfg: ExportConfig
) -> None:
    result, store = await build(make_builder, without_narrative(account()))
    # old code: status failed, reason build_failed, rule missing_narrative, nothing stored
    assert result.status == "complete", result.errors
    assert result.withheld_files == ("summary:missing_narrative",)
    assert kinds_of(result) == ALL - {"summary"}
    manifest = result.manifest
    assert manifest.files_withheld == ("summary:missing_narrative",)
    assert manifest.rows_per_file["summary"] == 0 and manifest.refs["summary"] == ()
    assert [f.kind for f in manifest.files] == ["evidence", "people", "jobs", "tech"]
    # the zip holds the four built files and their data, and does not list the fifth
    zipped = members(store, result)
    names = " ".join(zipped)
    assert "Account_Summary" not in names and "build/summary.json" not in zipped
    assert "build/evidence.json" in zipped and "RESTORE.md" in zipped
    assert "Account_Summary" not in zipped["RESTORE.md"].decode()
    embedded = json.loads(zipped["MANIFEST.json"])["build"]
    assert embedded["files_withheld"] == ["summary:missing_narrative"]
    # the stored rows carry the list too: the gate and the file listing read it from any row
    for row in store.rows:
        assert row[5]["files_withheld"] == ["summary:missing_narrative"], row[4].kind


async def test_every_narrative_block_withheld_by_a_rule_holds_back_the_summary_alone(
    make_builder: Make,
) -> None:
    c = account().model_copy(
        update={
            "sections": tuple(
                s.model_copy(update={"text": "The account has 17 synthetic teams."})
                if s.kind == "narrative"
                else s
                for s in account().sections
            )
        }
    )
    result, _ = await build(make_builder, c)
    assert result.status == "complete" and result.withheld_files == ("summary:missing_narrative",)
    assert any(b.endswith(":unregistered_number") for b in result.manifest.withheld_blocks)


async def test_a_file_over_its_row_cap_is_held_back_alone_not_truncated(
    make_builder: Make, cfg: ExportConfig
) -> None:
    result, store = await build(
        make_builder, account(n_postings=3), config=small_cfg(cfg, postings=2)
    )
    assert result.status == "complete", result.errors
    assert result.withheld_files == ("jobs:row_cap",)
    assert kinds_of(result) == ALL - {"jobs"}
    # nothing was truncated: the file that would have held the rows is not there at all
    assert "build/jobs.json" not in members(store, result)


async def test_a_run_without_a_coverage_table_holds_back_the_summary_alone(
    make_builder: Make,
) -> None:
    c = account().model_copy(update={"tracker": (), "tracker_tally": ()})
    result, _ = await build(make_builder, c)
    assert result.status == "complete", result.errors
    assert result.withheld_files == ("summary:missing_tracker",)
    assert kinds_of(result) == ALL - {"summary"}


class FailsOnTheSecondDocument(InProcessRenderer):
    """A renderer that fails on the second Word document (the Evidence file)."""

    def __init__(self) -> None:
        super().__init__()
        self.documents = 0

    def render(self, spec: DocumentSpec) -> bytes:
        self.documents += 1
        if self.documents == 2:
            raise RenderError("renderer exited with status 1")
        return super().render(spec)


async def test_a_renderer_that_fails_on_one_document_holds_back_that_file_alone(
    cfg: ExportConfig, grading: Any
) -> None:
    c = account()
    store = MemoryStore()
    builder = ExportBuilder(FakeReader(c), store, FailsOnTheSecondDocument(), cfg, grading)
    result = await builder.build_all(c.tenant_id, c.run_id)
    assert result.status == "complete", result.errors
    assert result.withheld_files == ("evidence:render_failed",)
    assert kinds_of(result) == ALL - {"evidence"}
    assert "summary" in kinds_of(result)  # rendered before the failure and stored
    assert "Account Summary" in docx_text(
        store.objects[next(d.object_key for d in result.deliverables if d.kind == "summary")]
    )


async def test_every_file_held_back_is_a_failed_build_naming_each_rule(
    make_builder: Make, monkeypatch: pytest.MonkeyPatch
) -> None:
    def always(self: ExportBuilder, kind: str, *args: Any, **kw: Any) -> Any:
        raise FileHeld(kind, "leak")  # type: ignore[arg-type]

    monkeypatch.setattr(ExportBuilder, "_produce", always)
    c = account()
    result, store = await build(make_builder, c)
    assert result.status == "failed" and result.reason == "build_failed"
    assert not store.rows and not store.objects
    assert set(result.codes) == {"leak"}
    assert any("every file was held back" in e for e in result.errors)


# ---- what is structural still refuses the set ----------------------------------------------


@pytest.mark.parametrize(
    ("what", "rule"),
    [
        ("grade", "grade_vocabulary"),
        ("private_fact", "non_public_row"),
        ("section_kind", "section_kind_vocabulary"),
        ("posting_status", "posting_status_vocabulary"),
        ("person_origin", "origin_vocabulary"),
    ],
)
async def test_structural_failures_still_refuse_the_whole_set(
    make_builder: Make, what: str, rule: str
) -> None:
    c = account()
    if what == "grade":
        c = c.model_copy(
            update={
                "facts": (c.facts[0].model_copy(update={"product_grade": "certain"}), *c.facts[1:])
            }
        )
    elif what == "private_fact":
        c = c.model_copy(
            update={
                "facts": (c.facts[0].model_copy(update={"visibility": "private"}), *c.facts[1:])
            }
        )
    elif what == "section_kind":
        c = c.model_copy(
            update={
                "sections": (
                    c.sections[0],
                    c.sections[1].model_copy(update={"kind": "rumour"}),
                    *c.sections[2:],
                )
            }
        )
    elif what == "posting_status":
        c = c.model_copy(
            update={
                "postings": (
                    c.postings[0].model_copy(update={"status": "dormant"}),
                    *c.postings[1:],
                )
            }
        )
    else:
        c = c.model_copy(
            update={"people": (c.people[0].model_copy(update={"origin": "guess"}), *c.people[1:])}
        )
    result, store = await build(make_builder, c)
    assert result.status == "failed" and rule in result.codes, (result.codes, result.errors)
    assert not store.rows and not store.objects


# ---- the zip and the rest ---------------------------------------------------------------


async def test_a_package_over_its_size_cap_is_built_again_without_the_archived_copies(
    make_builder: Make, cfg: ExportConfig
) -> None:
    from datetime import date

    from exports.content import ReleaseRow

    base, base_store = await build(make_builder, account())
    fits = sum(len(b) for b in members(base_store, base).values())  # the zip without copies
    releases = tuple(
        ReleaseRow(
            kind="page",
            url=f"https://alpha.example/r/{i}",
            title=f"Release {i}",
            day=date(2026, 9, 1),
            text="A line of the page. " * 4000,
        )
        for i in range(3)
    )
    ws = cfg.rules.workspace.model_copy(update={"max_total_bytes": fits + 500})
    tight = cfg.model_copy(update={"rules": cfg.rules.model_copy(update={"workspace": ws})})
    c = account().model_copy(update={"releases": releases})
    result, store = await build(make_builder, c, config=tight)
    assert result.status == "complete", result.errors  # old code: unsafe_package, build_failed
    assert not result.withheld_files
    assert "workspace.releases:all:size_cap" in result.manifest.withheld_blocks
    assert not [n for n in members(store, result) if n.startswith("data/releases/")]
    assert base.status == "complete"


async def test_a_render_model_that_fails_validation_is_a_typed_failure_not_a_crash(
    make_builder: Make, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: Any, **kw: Any) -> Any:
        LaneRow()  # type: ignore[call-arg]  # raises ValidationError: every field is missing

    monkeypatch.setattr("exports.builder.map_snapshot", broken)
    result, store = await build(make_builder, account())
    assert result.status == "failed" and "model_validation" in result.codes, result.errors
    assert not store.rows
    assert issubclass(ValidationError, Exception)


async def test_an_unexpected_exception_ends_the_build_as_a_failed_result_not_a_crash(
    make_builder: Make, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kw: Any) -> Any:
        raise RuntimeError("a message that could hold row text")

    monkeypatch.setattr(ExportBuilder, "assemble", boom)
    result, _ = await build(make_builder, account())
    assert result.status == "failed" and result.codes == ("internal_error",)
    assert all("row text" not in e for e in result.errors)
    assert any("RuntimeError" in e for e in result.errors)


def test_the_export_package_does_not_demand_a_table_whose_file_the_builder_left_out() -> None:
    """``POST /v1/exports`` compares each table with the file that prints it and blocked the whole
    package when a file was absent; a file the builder left out on purpose is not absent."""
    from views.parity import check_package
    from views.tables import specs

    table_specs = {str(k): v for k, v in specs().items()}
    kinds = {s.file_kind for s in table_specs.values()}
    missing = check_package(table_specs, {}, {})
    assert missing and all(not r.ok for r in missing.values())
    held = check_package(table_specs, {}, {}, kinds)
    assert held and all(r.ok for r in held.values())
    # only the left out kinds are excused
    one = check_package(table_specs, {}, {}, {"people"})
    assert {k for k, r in one.items() if r.ok} == {
        k for k, s in table_specs.items() if s.file_kind == "people"
    }
