"""Summary section 1 in the reference shape (2026-10-26): the account picture, what stands out,
what it likely means for each lane (interpretation labeled), how this could be wrong and the
simplest way to sharpen it. A run distilled before the two new kinds still builds, with the
none-found note under the empty subsections; the new kinds are withheld by the narrative rules."""

from __future__ import annotations

from account_intelligence.grading.config import GradingConfig
from exports.config import ExportConfig
from exports.content import SectionRow, SnapshotContent
from exports.readers.common import MapContext
from exports.readers.summary import map_summary
from exports.word.compose import compose_summary
from exports.word.contract import BulletsBlock, HeadingBlock, ParagraphBlock
from tests.exports.corpus import account


def _narrative(c: SnapshotContent) -> SectionRow:
    return next(s for s in c.sections if s.kind == "narrative")


def _with(c: SnapshotContent, *rows: SectionRow) -> SnapshotContent:
    return c.model_copy(update={"sections": (*c.sections, *rows)})


def _stands_out(c: SnapshotContent, **update: object) -> SectionRow:
    n = _narrative(c)
    row = SectionRow(
        id="s-out-1",
        kind="stands_out",
        position=0,
        codes=n.codes,
        fact_ids=n.fact_ids,
        text="A new technology leader took office this quarter.",
    )
    return row.model_copy(update=update)


def _lane(c: SnapshotContent, **update: object) -> SectionRow:
    n = _narrative(c)
    row = SectionRow(
        id="s-lane-1",
        kind="lane_meaning",
        position=0,
        codes=n.codes,
        fact_ids=n.fact_ids,
        lane="salesforce",
        text="The platform runs the service desk. Our interpretation: the team is growing.",
    )
    return row.model_copy(update=update)


def _section1(cfg: ExportConfig, c: SnapshotContent, grading: GradingConfig) -> list[object]:
    model = map_summary(MapContext(c, cfg, grading), None).model
    assert model is not None
    blocks = compose_summary(model, cfg, "Account Summary").blocks
    h = cfg.rules.headings.summary
    start = next(i for i, b in enumerate(blocks) if getattr(b, "text", None) == h.narrative)
    end = next(i for i, b in enumerate(blocks) if getattr(b, "text", None) == h.news)
    return list(blocks[start:end])


def test_the_section_follows_the_reference_order(cfg: ExportConfig, grading: GradingConfig) -> None:
    h = cfg.rules.headings.summary
    blocks = _section1(cfg, account(), grading)
    subs = [b.text for b in blocks if isinstance(b, HeadingBlock) and b.level == 2]
    assert subs[:5] == [
        h.narrative_picture,
        h.stands_out,
        h.lane_meaning,
        h.caveats,
        h.decision_changes,
    ]
    assert h.stands_out == "What stands out"
    assert h.lane_meaning == "What it likely means for each lane (interpretation labeled)"


def test_a_run_distilled_before_the_new_kinds_still_builds(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    c = account()
    assert not {s.kind for s in c.sections} & {"stands_out", "lane_meaning"}
    ctx = MapContext(c, cfg, grading)
    model = map_summary(ctx, None).model
    assert model is not None and model.stands_out == () and model.lane_meanings == ()
    h = cfg.rules.headings.summary
    blocks = _section1(cfg, c, grading)
    for heading in (h.stands_out, h.lane_meaning):
        i = next(i for i, b in enumerate(blocks) if getattr(b, "text", None) == heading)
        nxt = blocks[i + 1]
        assert isinstance(nxt, ParagraphBlock) and nxt.style == "note"
        assert nxt.text == cfg.rules.none_found.text


def test_stands_out_is_bulleted_and_a_lane_meaning_carries_its_lane_label(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    c = account()
    c = _with(c, _stands_out(c), _lane(c))
    ctx = MapContext(c, cfg, grading)
    mapped = map_summary(ctx, None)
    model = mapped.model
    assert model is not None and not ctx.withheld_blocks, ctx.withheld_blocks
    assert [b.row_id for b in model.stands_out] == ["s-out-1"]
    label = cfg.lane_label("salesforce")
    assert label and [b.lane for b in model.lane_meanings] == [label]
    # both are keyed rows of the file, and their codes are in the source links
    assert {"s-out-1", "s-lane-1"} <= set(mapped.row_ids)
    blocks = _section1(cfg, c, grading)
    items = [i for b in blocks if isinstance(b, BulletsBlock) for i in b.items]
    assert any(i.startswith("A new technology leader took office") for i in items)
    lane_para = [b.text for b in blocks if isinstance(b, ParagraphBlock)]
    assert any(t.startswith(f"{label}. The platform runs") for t in lane_para), lane_para


def test_the_new_kinds_follow_the_narrative_withhold_rules(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    c = account()
    unconfirmed = next(f for f in c.facts if f.product_grade != "confirmed")
    rows = (
        _lane(c, id="s-lane-x", lane="not_a_lane"),
        _stands_out(c, id="s-out-x", fact_ids=(unconfirmed.id,), position=1),
        _stands_out(c, id="s-out-dash", text="Hiring grew — fast.", position=2),
    )
    ctx = MapContext(_with(c, *rows), cfg, grading)
    model = map_summary(ctx, None).model
    assert model is not None
    held = {row: rule for _w, row, rule in ctx.withheld_blocks}
    assert held["s-lane-x"] == "lane_unknown"
    assert held["s-out-dash"] == "banned_dash"
    # a statement resting on an unconfirmed fact is withheld (or the fact is not in the tables)
    assert held["s-out-x"] in ("unconfirmed_stated", "section_fact_not_rendered")
    assert model.stands_out == () and model.lane_meanings == ()


def test_an_uncited_stands_out_block_is_withheld_like_narrative(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    """An authored block with no source code is never printed; like narrative it is withheld
    and listed (it no longer fails the build)."""
    c = account()
    ctx = MapContext(_with(c, _stands_out(c, codes=())), cfg, grading)
    model = map_summary(ctx, None).model
    assert "narrative_without_code" in {rule for _w, _row, rule in ctx.withheld_blocks}
    assert model is not None and model.stands_out == ()
