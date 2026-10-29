"""The Account Summary's sections 5 and 6 in the reference BD Summary's shape: one Platform
picture table (Item, What we found, Grade, the statement policy, reasoning and sources), the
platform hiring table dated on the as-of day, key people with lane, priority and note (priority A
and the executives), findings from profiles, and people whose status changed (the profile's
current title against the title search). Mapped and composed directly, without a render."""

from __future__ import annotations

from account_intelligence.grading.config import GradingConfig
from exports.config import ExportConfig
from exports.content import SnapshotContent
from exports.readers.common import MapContext
from exports.readers.jobs import map_jobs
from exports.readers.people import map_people
from exports.readers.summary import map_summary
from exports.word.compose import compose_summary, picture_cells
from exports.word.contract import Run, TableBlock
from tests.exports.corpus import account
from views.rowsources.facts import facts_rows
from views.tables import specs


def _summary(content: SnapshotContent, cfg: ExportConfig, grading: GradingConfig) -> object:
    ctx = MapContext(content, cfg, grading)
    people = map_people(ctx)
    jobs = map_jobs(ctx)
    mapped = map_summary(ctx, people.model, jobs.model)
    assert mapped.model is not None, ctx.violations
    return mapped, ctx


def _tables(cfg: ExportConfig, model: object) -> dict[str, list[TableBlock]]:
    spec = compose_summary(model, cfg, "Account Summary")  # type: ignore[arg-type]
    out: dict[str, list[TableBlock]] = {}
    for b in spec.blocks:
        if isinstance(b, TableBlock):
            out.setdefault(b.name, []).append(b)
    return out


def _with_attributes(content: SnapshotContent) -> SnapshotContent:
    facts = list(content.facts)
    facts[0] = facts[0].model_copy(update={"attribute_key": "platform_selection"})
    facts[1] = facts[1].model_copy(update={"attribute_key": "incumbent_partner"})
    return content.model_copy(update={"facts": tuple(facts)})


def test_the_platform_picture_is_one_table_with_item_and_reasoning(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    content = _with_attributes(account())
    mapped, _ = _summary(content, cfg, grading)
    model = mapped.model
    rows = {r.row_id: r for t in model.lanes for r in t.rows}
    first, second = rows[content.facts[0].id], rows[content.facts[1].id]
    # the item names the lane and the attribute from the skill vocabulary
    assert first.item == f"{cfg.lane_label('salesforce')}: Platform selection"
    assert second.item == f"{cfg.lane_label('servicenow')}: Incumbent partner"
    # a key outside the vocabulary is named by its own last part; no lane, no lane prefix
    no_lane = rows[content.facts[3].id]
    assert no_lane.item == "Synthetic 3"
    labels = cfg.rules.documents.labels
    assert first.read == labels.read_full and second.read == labels.read_headline
    tables = _tables(cfg, model)
    assert "lane" not in tables  # the per-lane tables are now one table
    (picture,) = tables["platform_picture"]
    assert picture.headers == tuple(cfg.rules.documents.tables["platform_picture"])
    assert picture.headers[1] == "Item" and picture.headers[-1] == "Reasoning and sources"
    assert [r[0] for r in picture.rows] == [r.ref for t in model.lanes for r in t.rows]
    by_ref = {r[0]: r for r in picture.rows}
    # Confirmed: stated, no question; below Confirmed: asked, with the confirming question
    cells = by_ref[first.ref]
    assert cells[4] == cfg.rules.statement_text.may_state
    assert cells[5] == f"{labels.read_full}; {', '.join(first.codes)}"
    cells = by_ref[second.ref]
    assert cells[4] == cfg.rules.statement_text.ask
    assert cells[5].endswith(f"{labels.question_prefix}: {second.question}")
    assert ", ".join(second.codes) in cells[5]
    # the client's Facts by grade table shows the printed cells (VW1 parity)
    assert picture_cells(first) == by_ref[first.ref]
    view = facts_rows({"summary": mapped}, specs()["facts"], {})
    assert [v.cells["reasoning"] for v in view] == [r[5] for r in picture.rows]
    assert [c.header for c in specs()["facts"].exported_columns] == list(picture.headers)


def _people(content: SnapshotContent) -> SnapshotContent:
    people = list(content.people)
    # person 2 is an executive with priority B; person 3 an ordinary priority B director
    people[2] = people[2].model_copy(
        update={"level": "evp", "title": "Synthetic Executive Vice President"}
    )
    people[0] = people[0].model_copy(update={"profile_title": "Synthetic Vice President 0"})
    # the same title spelt with other punctuation is not a change
    people[1] = people[1].model_copy(update={"profile_title": "synthetic director, 1"})
    return content.model_copy(update={"people": tuple(people)})


def test_key_people_are_priority_a_and_the_executives_with_priority_and_note(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    content = _people(account())
    mapped, ctx = _summary(content, cfg, grading)
    model = mapped.model
    ids = [p.row_id for p in model.key_people]
    assert set(ids) == {"pk-0000", "pk-0001", "pk-0002"}
    executive = next(p for p in model.key_people if p.row_id == "pk-0002")
    assert executive.priority == "B"
    assert executive.level == cfg.rules.labels.people_level_order[0]
    (table,) = _tables(cfg, model)["key_people"]
    assert table.headers == tuple(cfg.rules.documents.tables["key_people"])
    row = next(r for r in table.rows if r[0] == executive.ref)
    assert row[1:5] == (executive.name, executive.title, executive.lanes, "B")
    # the Link cell is a "Profile" hyperlink to the profile, as the reference prints it
    profile = Run(text=cfg.rules.documents.labels.profile_link, href=executive.profile_url)
    assert row[5] == (profile,)
    assert row[-1] == executive.source_code
    assert table.label_column == 1  # the name column prints bold, after the row key
    registered = {n.key: n.value for n in ctx.registry.items()}
    assert registered["summary.key_people"] == 3


def test_status_changes_are_profile_titles_that_differ_from_the_search(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    content = _people(account())
    mapped, _ = _summary(content, cfg, grading)
    model = mapped.model
    assert [(s.ref, s.row_id, s.search_title, s.profile_title) for s in model.status_changes] == [
        ("SC-0001", "status:pk-0000", "Synthetic Director 0", "Synthetic Vice President 0")
    ]
    change = model.status_changes[0]
    assert change.source_code in mapped.cited
    assert "SC-0001" in mapped.refs and "status:pk-0000" in mapped.row_ids
    (table,) = _tables(cfg, model)["status_changed"]
    assert table.rows == (
        (
            "SC-0001",
            change.person,
            "Synthetic Director 0",
            "Synthetic Vice President 0",
            change.source_code,
        ),
    )
    # no profile title, no change; the base account lists none
    plain, _ = _summary(account(), cfg, grading)
    assert plain.model.status_changes == ()


def test_findings_from_profiles_print_claim_quote_and_source(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    mapped, _ = _summary(account(), cfg, grading)
    model = mapped.model
    tables = _tables(cfg, model)
    if not model.linkedin_findings:
        return
    (table,) = tables["linkedin"]
    assert table.headers == tuple(cfg.rules.documents.tables["linkedin"])
    f = model.linkedin_findings[0]
    assert table.rows[0] == (f.ref, f.person, f.claim, f.sentence, f.source_code)


def test_people_method_opens_the_people_section(cfg: ExportConfig, grading: GradingConfig) -> None:
    mapped, _ = _summary(account(), cfg, grading)
    spec = compose_summary(mapped.model, cfg, "Account Summary")
    h = cfg.rules.headings.summary
    texts = [getattr(b, "text", None) or getattr(b, "name", "") for b in spec.blocks]
    at = texts.index(h.people)
    assert texts[at + 1] == h.people_method
    assert texts.index(h.people_method) < texts.index(h.key_people)
    assert texts.index(h.key_people) < texts.index(h.linkedin_findings)
    assert texts.index(h.linkedin_findings) < texts.index(h.status_changed)
