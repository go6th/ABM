"""The Account Summary's opening and its sections 7 to 10 read as the owner's reference file set
(the skill-built BD Account Summary, build_summary.js): the title "<account> (<legal name>):
Account Summary" and one opening paragraph naming the research date and the other four files;
hypotheses and first-call questions as numbered lists (each hypothesis with its confirming
question under it, numbering restarting per list); the sensitive caution and the waiting
decisions as bullets; the tracker proof cells, "None" left on finished rows and one tally line;
the source links followed by the line on where the postings read are listed."""

from __future__ import annotations

import io
import zipfile
from typing import Any

from lxml import etree

from account_intelligence.grading.config import GradingConfig
from account_intelligence.tracker.render import STATUS_TEXT
from exports.builder import map_snapshot
from exports.config import ExportConfig
from exports.word.compose import compose_summary
from exports.word.contract import BulletsBlock, HeadingBlock, ParagraphBlock, TitleBlock
from exports.word.render_cli import render
from tests.exports.corpus import account, outage

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _summary(content: Any, cfg: ExportConfig, grading: GradingConfig) -> Any:
    mapped, ctx = map_snapshot(content, cfg, grading)
    model = mapped["summary"].model
    assert model is not None
    return model, ctx


def _after(blocks: tuple[Any, ...], heading: str) -> list[Any]:
    """The blocks under a heading, up to the next level 1 heading."""
    start = next(
        i for i, b in enumerate(blocks) if isinstance(b, HeadingBlock) and b.text == heading
    )
    out = []
    for b in blocks[start + 1 :]:
        if isinstance(b, HeadingBlock) and b.level == 1:
            break
        out.append(b)
    return out


def test_title_and_opening_read_as_the_reference(cfg: ExportConfig, grading: GradingConfig) -> None:
    content = account().model_copy(
        update={"entity_lock": {"legal_name": "Synthetic Alpha Holdings Limited"}}
    )
    model, _ = _summary(content, cfg, grading)
    spec = compose_summary(model, cfg, "Synthetic Alpha Ltd Account Summary")
    title = spec.blocks[0]
    assert isinstance(title, TitleBlock)
    assert title.text == "Synthetic Alpha Ltd (Synthetic Alpha Holdings Limited): Account Summary"
    d = content.as_of
    assert title.subtitle == (
        f"Research date {d.day} {d:%B %Y}. Evidence, postings and source extracts are in"
        " Synthetic_Alpha_Ltd_Sources_and_Evidence.docx. People are in"
        " Synthetic_Alpha_Ltd_People_List.xlsx, postings in Synthetic_Alpha_Ltd_Job_Postings.xlsx,"
        " stack and teams in Synthetic_Alpha_Ltd_Tech_Stack_and_Teams.xlsx. Research by 6th agents."
    )
    # no version, domain or files heading between the opening and How to read this file
    assert isinstance(spec.blocks[1], HeadingBlock)
    assert spec.blocks[1].text == cfg.rules.headings.summary.how_to_read


def test_a_legal_name_equal_to_the_account_name_is_not_repeated(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    content = account().model_copy(update={"entity_lock": {"legal_name": "synthetic alpha ltd"}})
    model, _ = _summary(content, cfg, grading)
    assert model.legal_name is None
    spec = compose_summary(model, cfg, "Synthetic Alpha Ltd Account Summary")
    assert spec.blocks[0].text == "Synthetic Alpha Ltd: Account Summary"


def test_hypotheses_and_questions_are_numbered_lists(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    model, _ = _summary(account(), cfg, grading)
    assert model.hypotheses and model.first_call_questions
    spec = compose_summary(model, cfg, "Synthetic Alpha Ltd Account Summary")
    h = cfg.rules.headings.summary
    prefix = cfg.rules.documents.labels.question_prefix
    seven = _after(spec.blocks, h.hypotheses)
    hyps = seven[0]
    assert isinstance(hyps, BulletsBlock) and hyps.numbered
    assert len(hyps.items) == len(model.hypotheses)
    assert hyps.notes == tuple(f"{prefix}: {x.question}" for x in model.hypotheses)
    assert not any(item.startswith("1. ") for item in hyps.items)  # Word numbers the list
    caution_heading = HeadingBlock(type="heading", level=2, text=h.sensitive_cautions)
    cautions = seven[seven.index(caution_heading) + 1]
    assert cautions == BulletsBlock(type="bullets", items=(model.sensitive_sentence,))
    eight = _after(spec.blocks, h.first_call_questions)
    assert isinstance(eight[0], BulletsBlock) and eight[0].numbered and not eight[0].notes


def test_each_numbered_list_restarts_at_one(cfg: ExportConfig, grading: GradingConfig) -> None:
    model, _ = _summary(account(), cfg, grading)
    body = render(compose_summary(model, cfg, "Synthetic Alpha Ltd Account Summary"))
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        doc = etree.fromstring(z.read("word/document.xml"))
        numbering = etree.fromstring(z.read("word/numbering.xml"))
    used = []
    for p in doc.iter(f"{W}p"):
        num = p.find(f"{W}pPr/{W}numPr/{W}numId")
        if num is not None and num.get(f"{W}val") not in used:
            used.append(num.get(f"{W}val"))
    assert len(used) == 2  # the hypotheses and the questions, one numbering each
    for num_id in used:
        num = next(n for n in numbering.iter(f"{W}num") if n.get(f"{W}numId") == num_id)
        start = num.find(f"{W}lvlOverride/{W}startOverride")
        assert start is not None and start.get(f"{W}val") == "1"
    text = "".join(t.text or "" for t in doc.iter(f"{W}t"))
    assert f"{cfg.rules.documents.labels.question_prefix}: " in text


def test_tracker_cells_waiting_bullets_and_tally_line(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    model, ctx = _summary(outage(), cfg, grading)
    labels = cfg.rules.documents.labels
    rows = model.tracker
    # finished rows leave "None"; an open row with no search recorded reads "Not started", a
    # finished one never does
    finished = (STATUS_TEXT["done"], STATUS_TEXT["none"])
    for t in rows:
        if t.status in finished:
            assert t.remains == "None"
        if not t.tools:
            want = labels.done_unrecorded if t.status in finished else labels.done_nothing
            assert t.done == want
    assert any(t.done == "Not started" for t in rows)
    # one tally line, the counts in the order the statuses first appear in the printed rows
    counts: dict[str, int] = {}
    for t in rows:
        counts[t.status] = counts.get(t.status, 0) + 1
    expected = f"Tally, computed from the rows above ({len(rows)} rows): " + ", ".join(
        f"{k} {v}" for k, v in counts.items()
    )
    assert model.tally_line == expected + "."
    assert model.tally_line in ctx.registry.sentences()
    spec = compose_summary(model, cfg, "Synthetic Outage Co Account Summary")
    nine = _after(spec.blocks, cfg.rules.headings.summary.coverage_tracker)
    waiting = nine[1]
    assert isinstance(waiting, BulletsBlock) and not waiting.numbered
    blocked = [t for t in rows if t.status == STATUS_TEXT["blocked"]]
    assert len(waiting.items) == len(blocked) == len(model.waiting)
    for item, t in zip(waiting.items, blocked, strict=True):
        assert item.startswith(f"{t.row}: blocked")
        assert item.endswith(".")
    assert nine[-1] == ParagraphBlock(type="paragraph", style="body", text=model.tally_line)
    # the waiting decisions are bullets: their refs are not row keys of the file
    summary = map_snapshot(outage(), cfg, grading)[0]["summary"]
    assert not any(r.startswith("W-") for r in summary.refs)


def test_source_links_end_with_the_postings_line(cfg: ExportConfig, grading: GradingConfig) -> None:
    model, _ = _summary(account(), cfg, grading)
    spec = compose_summary(model, cfg, "Synthetic Alpha Ltd Account Summary")
    ten = _after(spec.blocks, cfg.rules.headings.summary.source_links)
    assert ten[0].name == "source_links"
    assert ten[-1].text == cfg.rules.documents.labels.source_links_note
