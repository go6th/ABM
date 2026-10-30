"""Lying content, one or more tests per B-rule (spec 9.6). Every case ends in its typed error (and
nothing stored, no partial set marked complete) or in its documented handling (a withheld and
counted quote)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from exports.builder import BuildFailed, ExportBuilder
from exports.config import ExportConfig
from exports.content import EvidenceRow, FactRow, SectionRow, SnapshotContent
from exports.errors import MissingContent, NotConfigured, RenderError
from exports.models import BuildResult
from exports.verify import docx_text
from exports.word.contract import DocumentSpec, TableBlock
from tests.exports.corpus import account, broker
from tests.fakes.exports import FakeReader, InProcessRenderer, MemoryStore

pytestmark = pytest.mark.asyncio
Make = Callable[..., ExportBuilder]


async def run(make: Make, content: SnapshotContent, **kw: Any) -> tuple[BuildResult, MemoryStore]:
    store = MemoryStore()
    result = await make(content, store, **kw).build_all(content.tenant_id, content.run_id)
    return result, store


def codes(result: BuildResult) -> set[str]:
    """The violation codes listed in the errors (``<class code>: <violation code> (...)``)."""
    out = set()
    for e in result.errors:
        head, _, rest = e.partition(": ")
        out.add(head)
        out.add(rest.split(" ", 1)[0])
    return out


def assert_failed(result: BuildResult, store: MemoryStore, *expected: str) -> None:
    assert result.status == "failed" and result.reason == "build_failed", result.errors
    assert not store.rows and not store.objects, "a failed build stores nothing"
    assert set(expected) <= codes(result), (expected, result.errors)


def with_fact(content: SnapshotContent, i: int, **update: Any) -> SnapshotContent:
    facts = list(content.facts)
    facts[i] = facts[i].model_copy(update=update)
    return content.model_copy(update={"facts": tuple(facts)})


def with_section(content: SnapshotContent, kind: str, **update: Any) -> SnapshotContent:
    sections = [s.model_copy(update=update) if s.kind == kind else s for s in content.sections]
    return content.model_copy(update={"sections": tuple(sections)})


async def test_the_base_account_builds(make_builder: Make) -> None:
    result, store = await run(make_builder, account())
    assert result.status == "complete", result.errors
    assert len(store.rows) == 6


# ---- B3 row conservation and caps ----------------------------------------------------------


async def test_b3_row_cap_fails_instead_of_truncating(
    make_builder: Make, cfg: ExportConfig
) -> None:
    caps = cfg.rules.limits.row_caps.model_copy(update={"postings": 2})
    limits = cfg.rules.limits.model_copy(update={"row_caps": caps})
    small = cfg.model_copy(update={"rules": cfg.rules.model_copy(update={"limits": limits})})
    result, store = await run(make_builder, account(n_postings=3), config=small)
    assert_failed(result, store, "row_cap_exceeded", "row_cap")


class DroppingRenderer(InProcessRenderer):
    """A lying renderer: silently drops the last row of every table."""

    def render(self, spec: DocumentSpec) -> bytes:
        blocks = []
        for b in spec.blocks:
            if isinstance(b, TableBlock) and b.rows:
                b = b.model_copy(update={"rows": b.rows[:-1], "keys": b.keys[:-1]})
            blocks.append(b)
        return super().render(spec.model_copy(update={"blocks": tuple(blocks)}))


async def test_b3_a_lost_row_in_the_produced_file_fails(cfg: ExportConfig, grading: Any) -> None:
    content = account()
    store = MemoryStore()
    builder = ExportBuilder(FakeReader(content), store, DroppingRenderer(), cfg, grading)
    result = await builder.build_all(content.tenant_id, content.run_id)
    assert_failed(result, store, "row_conservation")


async def test_b3_empty_sections_say_none_found(make_builder: Make) -> None:
    from tests.exports.corpus import sparse

    content = sparse()
    store = MemoryStore()
    result = await make_builder(content, store).build_all(content.tenant_id, content.run_id)
    assert result.status == "complete", result.errors
    summary = next(d for d in result.deliverables if d.kind == "summary")
    text = docx_text(store.objects[summary.object_key])
    # every section of the specification is present, so a sparse account prints "None found" in
    # each empty one: news, baseline, the four signal tables, lanes, the people method line, key
    # people, findings from profiles and status changed, and section 1's what stands out and
    # lane meanings (the corpus has none) and the People Interpretation section (reference shape)
    assert text.count("None found") == 14


# ---- B4 provenance ---------------------------------------------------------------------------


def assert_row_withheld(result: BuildResult, kind: str, row_id: str, rule: str, n: int) -> None:
    """The set was delivered; the row is listed as withheld for ``rule`` and ``kind`` keeps
    ``n`` rows (B4 by omission, never by invention; never a failed set, staging 2026-10-24)."""
    assert result.status == "complete", result.errors
    assert f"{kind}:{row_id}:{rule}" in result.manifest.withheld_blocks, (
        result.manifest.withheld_blocks
    )
    assert len(result.manifest.row_ids[kind]) == n
    assert row_id not in result.manifest.row_ids[kind]


async def test_b4_posting_without_url_is_withheld(make_builder: Make) -> None:
    c = account()
    postings = list(c.postings)
    postings[0] = postings[0].model_copy(update={"url": None})
    result, _store = await run(make_builder, c.model_copy(update={"postings": tuple(postings)}))
    assert_row_withheld(result, "jobs", "post-00000", "missing_url", len(postings) - 1)


async def test_b4_person_link_not_in_the_registry_is_withheld(make_builder: Make) -> None:
    c = account()
    people = list(c.people)
    people[1] = people[1].model_copy(
        update={"source_code": None, "profile_url": "https://alpha.example/unknown"}
    )
    result, _store = await run(make_builder, c.model_copy(update={"people": tuple(people)}))
    assert_row_withheld(result, "people", people[1].id, "missing_source_code", len(people) - 1)


async def test_b4_person_with_an_unknown_stored_code_fails(make_builder: Make) -> None:
    c = account()
    people = list(c.people)
    people[1] = people[1].model_copy(update={"source_code": "S-9999"})
    result, store = await run(make_builder, c.model_copy(update={"people": tuple(people)}))
    assert_failed(result, store, "unknown_source_code")


async def test_b4_detection_without_a_url_is_withheld(make_builder: Make) -> None:
    c = account()
    dets = list(c.detections)
    dets[0] = dets[0].model_copy(update={"source_url": None})
    result, _store = await run(make_builder, c.model_copy(update={"detections": tuple(dets)}))
    # the file's rows are the organisation's stack, so the withheld detection leaves them all
    assert_row_withheld(result, "tech", dets[0].id, "missing_url", len(c.stack))


# ---- B5 citations ----------------------------------------------------------------------------


async def test_b5_invented_code_is_an_orphan(make_builder: Make) -> None:
    c = with_section(account(), "narrative", codes=("S-9999",))
    result, store = await run(make_builder, c)
    assert_failed(result, store, "orphan_citation", "unknown_source_code")


async def test_b5_stored_code_not_in_the_registry(make_builder: Make) -> None:
    c = account()
    people = list(c.people)
    people[0] = people[0].model_copy(update={"source_code": "S-8888"})
    result, store = await run(make_builder, c.model_copy(update={"people": tuple(people)}))
    assert_failed(result, store, "unknown_source_code")


async def test_b5_graded_row_without_a_source(make_builder: Make) -> None:
    c = account()
    ev = list(c.evidence)
    ev[2] = ev[2].model_copy(update={"source_code": None, "url": "https://nowhere.example/x"})
    result, store = await run(make_builder, c.model_copy(update={"evidence": tuple(ev)}))
    assert_failed(result, store, "missing_source_code", "uncited_graded_row")


async def test_b5_narrative_sentence_without_a_code(make_builder: Make) -> None:
    result, store = await run(make_builder, with_section(account(), "narrative", codes=()))
    assert_failed(result, store, "narrative_without_code")


# ---- B6 numbers ------------------------------------------------------------------------------


async def test_b6_number_not_in_the_registry_or_the_cited_rows(make_builder: Make) -> None:
    # the block is withheld from the Summary, listed, and the set is still delivered
    c = with_section(account(), "caveat", text="The account has 17 synthetic teams.")
    result, store = await run(make_builder, c)
    assert_withheld(result, store, "unregistered_number", "17 synthetic teams")
    # the only narrative block withheld: no narrative, and the reason names the rule
    c = with_section(account(), "narrative", text="The account has 17 synthetic teams.")
    result, store = await run(make_builder, c)
    assert_failed(result, store, "missing_narrative")
    assert any("withheld (unregistered_number)" in e for e in result.errors), result.errors


def assert_withheld(result: BuildResult, store: MemoryStore, rule: str, text: str) -> None:
    """The set was delivered, one authored block is listed as withheld for ``rule`` and its
    text reached no file."""
    assert result.status == "complete", result.errors
    assert any(b.endswith(f":{rule}") for b in result.manifest.withheld_blocks), (
        result.manifest.withheld_blocks
    )
    for d in result.deliverables:
        if d.kind in ("summary", "evidence"):
            assert text not in docx_text(store.objects[d.object_key])


async def test_b6_number_from_a_registered_count_passes(make_builder: Make) -> None:
    c = account(n_people=4)
    c = with_section(c, "narrative", text="The list holds 4 synthetic people.")
    result, _ = await run(make_builder, c)
    assert result.status == "complete", result.errors


async def test_b6_number_present_in_a_cited_row_passes(make_builder: Make) -> None:
    c = with_fact(account(), 0, value_text="Synthetic revenue rose 12 percent in 2025")
    c = with_section(c, "narrative", text="Revenue rose 12 percent in 2025.")
    result, _ = await run(make_builder, c)
    assert result.status == "complete", result.errors


# ---- B7 statement policy ---------------------------------------------------------------------


async def test_b7_confirmed_without_the_primary_read_flag(make_builder: Make) -> None:
    result, store = await run(make_builder, with_fact(account(), 0, read_in_primary_this_run=False))
    assert_failed(result, store, "schema_violation", "confirmed_without_primary_read")


async def test_b7_probable_phrased_as_a_statement(make_builder: Make) -> None:
    result, store = await run(make_builder, with_fact(account(), 1, may_state_to_prospect=True))
    assert_failed(result, store, "statement_flag_disagrees")


async def test_b7_grade_outside_the_vocabulary(make_builder: Make) -> None:
    result, store = await run(make_builder, with_fact(account(), 1, product_grade="certain"))
    assert_failed(result, store, "grade_vocabulary")


async def test_b7_hypothesis_without_its_question(make_builder: Make) -> None:
    result, store = await run(make_builder, with_fact(account(), 2, confirming_question=None))
    assert_failed(result, store, "schema_violation", "question_missing")
    # a stored hypothesis block without its question is withheld, not a failed build
    c = account()
    held = next(s.text for s in c.sections if s.kind == "hypothesis")
    result, store = await run(make_builder, with_section(c, "hypothesis", confirming_question=" "))
    assert_withheld(result, store, "question_missing", held)


async def test_b7_aggregator_only_support_must_be_a_labelled_lead(make_builder: Make) -> None:
    c = account()
    ev = list(c.evidence)
    ev[1] = ev[1].model_copy(update={"is_aggregator": True})  # fact 1 is Probable
    result, store = await run(make_builder, c.model_copy(update={"evidence": tuple(ev)}))
    # a Probable finding backed by aggregators alone is a grading defect: that one row is
    # withheld and listed, the research keeps its files (it used to fail the whole build)
    assert_withheld(result, store, "aggregator_not_lead", c.facts[1].value_text)
    ev = list(c.evidence)
    ev[3] = ev[3].model_copy(update={"is_aggregator": True})  # fact 3 is a Lead: labelled
    ok = c.model_copy(update={"evidence": tuple(ev)})
    builder = make_builder(ok)
    out = builder.assemble(ok)
    labels = [r.grade_label for t in out.mapped["summary"].model.lanes for r in t.rows]
    assert "Lead (aggregator source)" in labels


async def test_b7_narrative_stating_an_unconfirmed_fact(make_builder: Make) -> None:
    probable = account().facts[1].id
    c = with_section(
        account(),
        "narrative",
        fact_ids=(probable,),
        text="The synthetic account is moving platforms.",
    )
    result, store = await run(make_builder, c)
    # the statement is withheld; it was the only narrative block, so the Summary has none
    assert_failed(result, store, "missing_narrative")
    assert any("withheld (unconfirmed_stated)" in e for e in result.errors), result.errors
    question = with_section(
        account(),
        "narrative",
        fact_ids=(probable,),
        text="Is the synthetic account moving platforms?",
    )
    result, _ = await run(make_builder, question)
    assert result.status == "complete", result.errors


async def test_b7_section_fact_must_be_a_rendered_row(make_builder: Make) -> None:
    c = account()
    held = next(s.text for s in c.sections if s.kind == "caveat")
    c = with_section(c, "caveat", fact_ids=("00000000-0000-4000-8000-999999999999",))
    result, store = await run(make_builder, c)
    assert_withheld(result, store, "section_fact_not_rendered", held)


# ---- B8 two outputs ----------------------------------------------------------------------------


async def test_b8_private_fact_never_reaches_a_file(make_builder: Make) -> None:
    result, store = await run(make_builder, with_fact(account(), 0, visibility="private"))
    assert_failed(result, store, "leak", "non_public_row")


async def test_b8_private_section_never_reaches_a_file(make_builder: Make) -> None:
    result, store = await run(make_builder, with_section(account(), "caveat", visibility="private"))
    assert_failed(result, store, "non_public_row")


async def test_b8_internal_grade_never_reaches_a_file(make_builder: Make) -> None:
    c = with_fact(account(), 1, product_grade="internal")
    result, store = await run(make_builder, c)
    assert_failed(result, store, "internal_in_public_file")


# ---- B9 identity and leak scrub --------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        f"Data pulled through {broker('p_scrape')}",  # a broker name
        "The run used 120 credits",  # a credit count
        "See call " + "ab" * 32,  # a call reference
        "Routed via p_search",  # a provider code
        "Synthetic Other Account Inc is a peer",  # another account
        "Synthetic Excluded Person leads IT",  # an excluded person
    ],
)
async def test_b9_forbidden_content_in_a_row(make_builder: Make, value: str) -> None:
    # the text is replaced by the withheld note and counted; the set is delivered (two such
    # sentences held back all five files of a finished research, staging 2026-10-25)
    result, store = await run(make_builder, with_fact(account(), 0, value_text=value))
    assert result.status == "complete", result.errors
    assert result.manifest.leaks_withheld >= 1
    for d in result.deliverables:
        if d.kind in ("summary", "evidence"):
            text = docx_text(store.objects[d.object_key])
            assert value not in text


async def test_b9_a_source_link_naming_a_data_tool_is_withheld_not_fatal(
    make_builder: Make,
) -> None:
    # a vendor's public company page cited as a source (staging 2026-10-25: two such links
    # failed the third-party scan of a finished research, build_failed:tool_name)
    c = account()
    in_sections = {fid for s in c.sections for fid in s.fact_ids}
    ev = next(
        e for e in c.evidence if e.fact_id not in in_sections and e.evidence_kind != "search_proof"
    )
    code = ev.source_code or next(s.code for s in c.sources if s.url == ev.url)
    host = broker("p_scrape").lower().replace(" ", "")
    url = f"https://www.{host}.dev/companies/synthetic"
    sources = tuple(s.model_copy(update={"url": url}) if s.code == code else s for s in c.sources)
    evidence = tuple(
        e.model_copy(update={"url": url}) if (e.source_code or "") == code or e.url == ev.url else e
        for e in c.evidence
    )
    c = c.model_copy(update={"sources": sources, "evidence": evidence})
    result, store = await run(make_builder, c)
    assert result.status == "complete", result.errors
    assert any(b.endswith(":tool_link") for b in result.manifest.withheld_blocks), (
        result.manifest.withheld_blocks
    )
    for d in result.deliverables:
        if d.kind in ("summary", "evidence"):
            assert host not in docx_text(store.objects[d.object_key]).lower()


async def test_b9_client_staff_name_in_authored_text(make_builder: Make) -> None:
    c = account().model_copy(update={"forbidden_terms": ("Jane Clientstaff",)})
    c = with_section(c, "caveat", text="Jane Clientstaff shared this view.")
    result, store = await run(make_builder, c)
    assert_withheld(result, store, "leak", "Jane Clientstaff")


async def test_b9_partner_framing_fails_for_a_partner_tenant(make_builder: Make) -> None:
    text = "The synthetic account is leaving Salesforce."
    result, store = await run(
        make_builder, with_section(account(partner=True), "caveat", text=text)
    )
    assert_withheld(result, store, "partner_framing", "leaving Salesforce")
    result, store = await run(
        make_builder, with_section(account(partner=False), "caveat", text=text)
    )
    assert result.status == "complete" and not result.manifest.withheld_blocks, result.errors
    summary = next(d for d in result.deliverables if d.kind == "summary")
    assert "leaving Salesforce" in docx_text(store.objects[summary.object_key])


async def test_b9_restricted_term_in_a_verbatim_quote_is_withheld_counted_listed(
    make_builder: Make,
) -> None:
    c = account()
    ev = list(c.evidence)
    name = broker("p_people")
    ev[0] = ev[0].model_copy(update={"supporting_sentence": f"Numbers from {name} say so."})
    c = c.model_copy(update={"evidence": tuple(ev)})
    store = MemoryStore()
    result = await make_builder(c, store).build_all(c.tenant_id, c.run_id)
    assert result.status == "complete", result.errors
    assert result.manifest.withheld_sentences == 1
    evidence = next(d for d in result.deliverables if d.kind == "evidence")
    text = docx_text(store.objects[evidence.object_key])
    assert name.lower() not in text.lower()
    assert "Sentence withheld from this file by the distribution rules" in text
    assert ev[0].url in text  # the link is kept
    registered = {n.key: n.value for n in result.manifest.numbers}
    assert registered["evidence.withheld"] == 1


async def test_b9_partner_framing_inside_a_quote_is_withheld(make_builder: Make) -> None:
    c = account(partner=True)
    ev = list(c.evidence)
    ev[0] = ev[0].model_copy(update={"supporting_sentence": "We are leaving Salesforce."})
    result, _ = await run(make_builder, c.model_copy(update={"evidence": tuple(ev)}))
    assert result.status == "complete" and result.manifest.withheld_sentences == 1


async def test_b9_sensitive_sentence_is_in_every_summary(
    make_builder: Make, cfg: ExportConfig
) -> None:
    c = account()
    store = MemoryStore()
    result = await make_builder(c, store).build_all(c.tenant_id, c.run_id)
    summary = next(d for d in result.deliverables if d.kind == "summary")
    assert cfg.rules.sensitive_sentence.text in docx_text(store.objects[summary.object_key])


# ---- D-24 authored prose ---------------------------------------------------------------------


async def test_banned_dash_in_authored_text(make_builder: Make) -> None:
    # withheld and listed; the other files are delivered (one dash held back five files on
    # staging, 2026-10-21)
    result, store = await run(
        make_builder, with_section(account(), "caveat", text="Public data — only.")
    )
    assert_withheld(result, store, "banned_dash", "Public data")
    result, store = await run(
        make_builder, with_section(account(), "caveat", text="Public data - only.")
    )
    assert_withheld(result, store, "banned_dash", "Public data")


async def test_a_dash_in_a_stored_title_is_not_punctuation(make_builder: Make) -> None:
    # the posting's own title is outside the dash rule's scope (a fifth of stored job titles
    # carry a dash; the archived script failed every such build), so it never fails a build or
    # withholds a block; the files print it without the dash (the reference holds none: a spaced
    # dash becomes a comma, fix wave C)
    c = account()
    ps = list(c.postings)
    ps[0] = ps[0].model_copy(update={"title": "Synthetic Developer – Remote (m/f/d)"})
    result, store = await run(make_builder, c.model_copy(update={"postings": tuple(ps)}))
    assert result.status == "complete", result.errors
    assert not result.manifest.withheld_blocks
    evidence = next(d for d in result.deliverables if d.kind == "evidence")
    assert "Synthetic Developer, Remote" in docx_text(store.objects[evidence.object_key])
    assert "\u2013" not in docx_text(store.objects[evidence.object_key])


async def test_banned_dash_inside_a_quote_stays_verbatim(make_builder: Make) -> None:
    c = with_section(account(), "executive_voice", text="We grew — a lot.")
    result, _ = await run(make_builder, c)
    assert result.status == "complete", result.errors


async def test_missing_narrative_refuses_the_summary(make_builder: Make) -> None:
    c = account()
    c = c.model_copy(update={"sections": tuple(s for s in c.sections if s.kind != "narrative")})
    result, store = await run(make_builder, c)
    assert_failed(result, store, "missing_content", "missing_narrative")
    # the reason is readable: how many facts were Confirmed and why none could be stated
    assert any("Confirmed facts of" in e and "reason:" in e for e in result.errors), result.errors


async def test_section_kind_outside_the_vocabulary(make_builder: Make) -> None:
    c = account()
    extra = SectionRow(id="x1", kind="opinion", position=0, text="An opinion.")
    result, store = await run(make_builder, c.model_copy(update={"sections": (*c.sections, extra)}))
    assert_failed(result, store, "section_kind_vocabulary")


# ---- B15 fail closed ---------------------------------------------------------------------------


async def test_b15_every_error_is_listed_at_once(make_builder: Make) -> None:
    c = with_fact(account(), 0, read_in_primary_this_run=False)
    c = with_section(c, "caveat", visibility="private")
    result, store = await run(make_builder, c)
    assert_failed(result, store, "confirmed_without_primary_read", "non_public_row")
    # the rules, not the classes, are what the run records as its stop reason
    assert result.codes == ("confirmed_without_primary_read", "non_public_row")
    assert BuildFailed(result).codes == "confirmed_without_primary_read,non_public_row"


class FailingRenderer(InProcessRenderer):
    """The renderer fails on every call."""

    def render(self, spec: DocumentSpec) -> bytes:
        raise RenderError("exit 3")


async def test_b15_a_render_failure_stores_nothing(cfg: ExportConfig, grading: Any) -> None:
    c = account()
    store = MemoryStore()
    result = await ExportBuilder(FakeReader(c), store, FailingRenderer(), cfg, grading).build_all(
        c.tenant_id, c.run_id
    )
    assert_failed(result, store, "render_failed")


async def test_b15_missing_adapters_are_not_configured(cfg: ExportConfig, grading: Any) -> None:
    c = account()
    builder = ExportBuilder(FakeReader(c), MemoryStore(), None, cfg, grading)
    result = await builder.build_all(c.tenant_id, c.run_id)
    assert (result.status, result.reason) == ("not_configured", "builders_not_configured")
    with pytest.raises(NotConfigured):
        await builder.build(c.tenant_id, c.run_id)


async def test_b15_reader_failure_is_a_failed_build(cfg: ExportConfig, grading: Any) -> None:
    builder = ExportBuilder(
        FakeReader(error=MissingContent("no such run")),
        MemoryStore(),
        InProcessRenderer(),
        cfg,
        grading,
    )
    result = await builder.build_all("t", "r")
    assert result.status == "failed" and "missing_content" in result.errors[0]
    with pytest.raises(BuildFailed) as raised:
        await builder.build("t", "r")
    assert raised.value.reason == "build_failed"


async def test_reader_not_configured_is_reported_as_such(cfg: ExportConfig, grading: Any) -> None:
    builder = ExportBuilder(
        FakeReader(error=NotConfigured("no store adapter")),
        MemoryStore(),
        InProcessRenderer(),
        cfg,
        grading,
    )
    result = await builder.build_all("t", "r")
    assert (result.status, result.reason) == ("not_configured", "builders_not_configured")


async def test_rows_never_change_the_input_models() -> None:
    """The fixtures are frozen: a lying test cannot mutate shared content by accident."""
    fact = account().facts[0]
    with pytest.raises(ValueError):
        fact.value_text = "changed"  # type: ignore[misc]
    assert isinstance(fact, FactRow) and isinstance(account().evidence[0], EvidenceRow)


# ---- B11 a required text with nothing printable is refused, never replaced by an id -------


@pytest.mark.parametrize(
    "target", ["fact", "section", "evidence", "posting", "detection", "speaker"]
)
async def test_b11_text_with_nothing_printable_is_refused(make_builder: Make, target: str) -> None:
    junk = "\x00\x1b\x7f"
    c = account()
    if target == "fact":
        c = with_fact(c, 0, value_text=junk)
    elif target == "section":
        c = with_section(c, "caveat", text=junk)
    elif target == "speaker":
        c = with_section(c, "executive_voice", speaker=junk)
    elif target == "evidence":
        ev = list(c.evidence)
        ev[0] = ev[0].model_copy(update={"supporting_sentence": junk})
        c = c.model_copy(update={"evidence": tuple(ev)})
    elif target == "posting":
        ps = list(c.postings)
        ps[0] = ps[0].model_copy(update={"title": junk})
        c = c.model_copy(update={"postings": tuple(ps)})
    else:
        ds = list(c.detections)
        ds[0] = ds[0].model_copy(update={"technology": junk})
        c = c.model_copy(update={"detections": tuple(ds)})
    result, store = await run(make_builder, c)
    if target == "speaker":
        # a quote without a printable speaker is withheld from the Summary, never rendered
        # with an id in the speaker's place
        held = next(s.text for s in account().sections if s.kind == "executive_voice")
        assert_withheld(result, store, "speaker_missing", held)
        return
    assert_failed(result, store, "schema_violation", "text_missing")
