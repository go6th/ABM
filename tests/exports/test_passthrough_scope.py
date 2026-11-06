"""The dash rule's scope is authored prose: every stored text a document prints is outside it,
whether or not the builder's list names the field (a lane row's reasoning sentence withheld a
whole Summary on staging, 2026-11-04). ``stored_texts`` walks the render model so the list
cannot fall behind the model."""

from __future__ import annotations

from account_intelligence.grading.config import GradingConfig
from exports.builder import ExportBuilder, stored_texts
from exports.config import ExportConfig
from exports.models import AuthoredBlock, LaneRow, LaneTable, SummaryModel
from exports.readers.common import MapContext
from exports.readers.summary import map_summary
from tests.exports.corpus import account


def _summary(cfg: ExportConfig, grading: GradingConfig) -> SummaryModel:
    model = map_summary(MapContext(account(), cfg, grading), None).model
    assert model is not None
    return model


def test_every_stored_text_is_outside_the_dash_rule(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    model = _summary(cfg, grading)
    row = model.lanes[0].rows[0]
    dashed = row.model_copy(update={"reasoning": "Named in the posting — read in full."})
    table = LaneTable(lane=model.lanes[0].lane, rows=(dashed, *model.lanes[0].rows[1:]))
    model = model.model_copy(update={"lanes": (table, *model.lanes[1:])})
    exempt = set(ExportBuilder._passthrough_texts("summary", model))
    assert "Named in the posting — read in full." in exempt
    # the listed fields are still there
    assert row.finding in exempt and model.none_found in exempt


def test_authored_prose_stays_inside_the_dash_rule(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    model = _summary(cfg, grading)
    exempt = set(ExportBuilder._passthrough_texts("summary", model))
    for block in (*model.narrative, *model.caveats, *model.first_call_questions):
        assert isinstance(block, AuthoredBlock)
        assert block.text not in exempt, block.text
    # an executive quote is verbatim, exempted by ``_verbatim_quotes``, not by the walk
    for quote in model.executive_voice:
        assert quote.text in set(ExportBuilder._verbatim_quotes("summary", model))


def test_stored_texts_walks_lines_and_skips_only_authored_prose() -> None:
    block = AuthoredBlock(row_id="row-1", text="Authored — prose.", codes=("S-0001",))
    row = LaneRow(
        ref="L-0001",
        row_id="l",
        finding="A finding",
        grade_label="Confirmed",
        use="State",
        may_state=True,
        question=None,
        codes=("S-0001",),
        reasoning="first line\n  second – line  \n",
    )
    got = list(stored_texts((block, row)))
    assert "Authored — prose." not in got
    assert "row-1" in got and "S-0001" in got  # a one-character text is never a dash
    assert got.count("first line") == 1 and "second – line" in got
    assert all(line == line.strip() and "\n" not in line for line in got)
