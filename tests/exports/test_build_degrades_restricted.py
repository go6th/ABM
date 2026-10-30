"""A restricted term (another account, an excluded company, a person the files do not list) never
ends a finished research: the text, link or row that carries it is withheld and listed, and the
rest of the set is delivered. Staging 2026-10-31: a research that read, graded and wrote for 20
minutes ended PARTIAL with no files because the final scan of two produced files found a
restricted term (``exports.build_failed reason=leak=2,leak=5``).

Every account, term and URL here is synthetic. The leak rules themselves are in test_policy.py;
this file pins what the BUILD does when a rule fires: withhold, list, deliver."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from exports.content import FindingRow, SnapshotContent
from exports.policy import leak_hits, names_own, restricted_terms
from exports.readers.common import MapContext, own_names, restricted_of
from tests.exports.corpus import account
from tests.exports.degrade_kit import OTHER_DOMAIN, OTHER_NAME, Make, build, kinds_of, stored_text

pytestmark = pytest.mark.asyncio
FILES = {"summary", "evidence", "people", "jobs", "tech", "workspace"}


def with_terms(content: SnapshotContent, *terms: str) -> SnapshotContent:
    """``content`` forbidding exactly ``terms`` (the corpus default names are replaced)."""
    return content.model_copy(update={"forbidden_terms": terms})


def edit(rows: tuple[Any, ...], i: int, **update: Any) -> tuple[Any, ...]:
    """``rows`` with row ``i`` changed."""
    return tuple(r.model_copy(update=update) if k == i else r for k, r in enumerate(rows))


def assert_delivered_clean(result: Any, store: Any, *terms: str) -> None:
    """The whole set is stored and no term reached any member of any file, in any case."""
    assert result.status == "complete", result.errors
    assert kinds_of(result) == FILES, kinds_of(result)
    assert not result.withheld_files, result.withheld_files
    text = stored_text(store, result).casefold()
    for term in terms:
        assert term.casefold() not in text, term


# ---- the account's own identity is never "another account" ----------------------------------


@pytest.mark.parametrize(
    "term",
    [
        "alpha.example",  # the same domain listed twice (a second user's workspace, a duplicate)
        "Synthetic Alpha Ltd",  # the same name
        "synthetic alpha ltd",  # the same name in another case
        "Alpha",  # a smaller name that is a word of this account's name
        "Synthetic Alpha Holdings Ltd",  # the legal name of the entity lock
        "Synthetic Sub Ltd",  # a subsidiary of the entity lock
        "jobs.alpha.example",  # the verified careers host
    ],
)
async def test_the_accounts_own_identity_as_a_restricted_term_does_not_refuse_the_files(
    make_builder: Make, term: str
) -> None:
    c = with_terms(account(), term, OTHER_NAME).model_copy(
        update={
            "careers_host": "jobs.alpha.example",
            "entity_lock": {
                "legal_name": "Synthetic Alpha Holdings Ltd",
                "subsidiaries": [{"name": "Synthetic Sub Ltd", "domain": ""}],
            },
        }
    )
    result, store = await build(make_builder, c)
    # old code: the term was in the title of every file, so every file failed the final scan
    assert_delivered_clean(result, store, OTHER_NAME)


async def test_a_real_other_account_is_still_restricted_beside_the_accounts_own_name() -> None:
    c = with_terms(account(), "Alpha", OTHER_NAME, "synthetic alpha ltd")
    assert restricted_of(c.model_copy(update={"people_names": ()}), "6th") == (OTHER_NAME,)
    # not the account's name, not a word of it, not its domain: kept
    assert "other.example" in restricted_of(account(), "6th")


def test_own_names_cover_the_name_the_domain_the_lock_and_the_brand() -> None:
    c = account().model_copy(
        update={
            "brand_name": "Synthetic Brand",
            "careers_host": "jobs.alpha.example",
            "entity_lock": {
                "legal_name": "Synthetic Alpha Holdings Ltd",
                "brand": "SAH",
                "primary_domain": "alpha.example",
                "allowed_domains": ["alpha.example", "alpha-labs.example"],
                "careers_hosts": ["jobs.alpha.example"],
                "subsidiaries": [{"name": "Synthetic Sub Ltd", "domain": "sub.example"}, "junk"],
            },
        }
    )
    names = own_names(c)
    for expected in (
        "Synthetic Alpha Ltd",
        "alpha.example",
        "jobs.alpha.example",
        "Synthetic Brand",
        "Synthetic Alpha Holdings Ltd",
        "SAH",
        "alpha-labs.example",
        "Synthetic Sub Ltd",
        "sub.example",
    ):
        assert expected in names
    assert len(names) == len(set(names))


def test_names_own_is_whole_word_and_any_case() -> None:
    own = ("Synthetic Alpha Ltd", "alpha.example")
    assert names_own("alpha", own) and names_own("ALPHA.EXAMPLE", own)
    assert names_own("Alpha Ltd", own)
    assert not names_own("Alphabet", own) and not names_own("Alp", own)
    assert not names_own("Zorblax", own) and not names_own("Alpha", ())
    assert restricted_terms(["Alpha", "Zorblax"], "6th", own) == ("Zorblax",)


# ---- a link naming another account withholds the rows that rest on it ------------------------


def _source_link(c: SnapshotContent, i: int, url: str) -> SnapshotContent:
    """Source ``i`` (and every row that carries its address) on ``url``."""
    old = c.sources[i].url
    return c.model_copy(
        update={
            "sources": edit(c.sources, i, url=url),
            "evidence": tuple(
                e.model_copy(update={"url": url}) if e.url == old else e for e in c.evidence
            ),
            "people": tuple(
                p.model_copy(update={"profile_url": url}) if p.profile_url == old else p
                for p in c.people
            ),
            "postings": tuple(
                p.model_copy(update={"url": url}) if p.url == old else p for p in c.postings
            ),
            "detections": tuple(
                d.model_copy(update={"source_url": url}) if d.source_url == old else d
                for d in c.detections
            ),
            "findings": tuple(
                f.model_copy(update={"url": url}) if f.url == old else f for f in c.findings
            ),
        }
    )


def _row_of(c: SnapshotContent, kind: str) -> int:
    """The index of the source behind the first row of ``kind``."""
    url = {
        "fact": c.evidence[2].url,
        "person": c.people[1].profile_url,
        "posting": c.postings[1].url,
        "detection": c.detections[0].source_url,
        "finding": c.findings[0].url,
    }[kind]
    return next(i for i, s in enumerate(c.sources) if s.url == url)


@pytest.mark.parametrize("kind", ["fact", "person", "posting", "detection", "finding"])
async def test_a_link_on_another_accounts_site_withholds_that_row_not_the_research(
    make_builder: Make, kind: str
) -> None:
    c = with_terms(account(), OTHER_NAME, OTHER_DOMAIN)
    url = f"https://news.{OTHER_DOMAIN}/press/story"
    c = _source_link(c, _row_of(c, kind), url)
    result, store = await build(make_builder, c)
    # old code: the link reached the file unchecked and the final scan failed the whole set
    assert result.status == "complete", result.errors
    assert kinds_of(result) == FILES
    assert any(b.endswith(":restricted_link") for b in result.manifest.withheld_blocks), (
        result.manifest.withheld_blocks
    )
    text = stored_text(store, result).casefold()
    assert OTHER_DOMAIN not in text and OTHER_NAME.casefold() not in text


async def test_the_percent_encoded_form_of_the_domain_is_judged_too(make_builder: Make) -> None:
    c = with_terms(account(), OTHER_NAME, OTHER_DOMAIN)
    # "https://news.zorblax.example/x" as a redirect parameter of another site
    url = f"https://redirect.example/go?to=https%3A%2F%2Fnews.{OTHER_DOMAIN}%2Fx"
    c = _source_link(c, _row_of(c, "fact"), url)
    result, store = await build(make_builder, c)
    assert result.status == "complete", result.errors
    assert any(b.endswith(":restricted_link") for b in result.manifest.withheld_blocks)
    assert OTHER_DOMAIN not in stored_text(store, result).casefold()


async def test_a_second_profile_link_on_another_site_is_left_off_the_person(
    make_builder: Make,
) -> None:
    c = with_terms(account(), OTHER_NAME, OTHER_DOMAIN)
    c = c.model_copy(
        update={"people": edit(c.people, 1, second_profile_url=f"https://{OTHER_DOMAIN}/in/x")}
    )
    result, store = await build(make_builder, c)
    assert_delivered_clean(result, store, OTHER_NAME, OTHER_DOMAIN)
    assert len(result.manifest.row_ids["people"]) == len(c.people)  # the person stays listed


async def test_every_source_a_narrative_cites_being_blocked_holds_back_the_summary_only(
    make_builder: Make,
) -> None:
    c = with_terms(account(), OTHER_NAME, OTHER_DOMAIN)
    # the corpus narrative cites source 0 alone; its link is on another account's site
    c = _source_link(c, 0, f"https://{OTHER_DOMAIN}/about")
    result, store = await build(make_builder, c)
    assert result.status == "complete", result.errors  # old code: build_failed:missing_narrative
    assert result.withheld_files == ("summary:missing_narrative",)
    assert kinds_of(result) == FILES - {"summary"}
    assert OTHER_DOMAIN not in stored_text(store, result).casefold()


# ---- a restricted term in any text the files print ------------------------------------------


def _facts(c: SnapshotContent, **kw: Any) -> SnapshotContent:
    return c.model_copy(update={"facts": edit(c.facts, 1, **kw)})


def _evidence(c: SnapshotContent, **kw: Any) -> SnapshotContent:
    return c.model_copy(update={"evidence": edit(c.evidence, 1, **kw)})


def _people(c: SnapshotContent, **kw: Any) -> SnapshotContent:
    return c.model_copy(update={"people": edit(c.people, 1, **kw)})


def _postings(c: SnapshotContent, **kw: Any) -> SnapshotContent:
    return c.model_copy(update={"postings": edit(c.postings, 0, **kw)})


def _section(c: SnapshotContent, kind: str, **kw: Any) -> SnapshotContent:
    return c.model_copy(
        update={
            "sections": tuple(s.model_copy(update=kw) if s.kind == kind else s for s in c.sections)
        }
    )


def _t(base: str) -> str:
    return f"{base} {OTHER_NAME}"


INJECTIONS: dict[str, Callable[[SnapshotContent], SnapshotContent]] = {
    "fact": lambda c: _facts(c, value_text=_t("Synthetic finding")),
    "fact_question": lambda c: _facts(c, confirming_question=_t("Is it so?")),
    "evidence_sentence": lambda c: _evidence(c, supporting_sentence=_t("Synthetic sentence")),
    "caveat": lambda c: _section(c, "caveat", text=_t("Synthetic caveat")),
    "hypothesis": lambda c: _section(c, "hypothesis", text=_t("Synthetic hypothesis")),
    "hypothesis_question": lambda c: _section(c, "hypothesis", confirming_question=_t("Why?")),
    "first_call": lambda c: _section(c, "first_call_question", text=_t("Which team owns it?")),
    "voice_text": lambda c: _section(c, "executive_voice", text=_t("We are investing")),
    "voice_speaker": lambda c: _section(c, "executive_voice", speaker=_t("Synthetic Chief")),
    "person_name": lambda c: _people(c, name=_t("Synthetic Person")),
    "person_title": lambda c: _people(c, title=_t("Director")),
    "person_location": lambda c: _people(c, location=_t("Remote")),
    "person_profile_title": lambda c: _people(c, profile_title=_t("Head of IT")),
    "person_former_employer": lambda c: _people(c, former_employers=(_t("Synthetic Co"),)),
    "posting_title": lambda c: _postings(c, title=_t("Synthetic Admin")),
    "posting_description": lambda c: _postings(c, description=_t("Synthetic text.")),
    "posting_sentence": lambda c: _postings(c, platform_sentences=(_t("Works with Salesforce."),)),
    "posting_class_sentence": lambda c: _postings(c, class_sentence=_t("Owns Salesforce.")),
    "posting_location": lambda c: _postings(c, location=_t("Remote")),
    "posting_seniority": lambda c: _postings(c, seniority=_t("manager")),
    "detection": lambda c: c.model_copy(
        update={"detections": edit(c.detections, 1, technology=_t("Synthetic Tool"))}
    ),
    "detection_date": lambda c: c.model_copy(
        update={"detections": edit(c.detections, 1, first_seen=_t("2025-01"))}
    ),
    "stack": lambda c: c.model_copy(
        update={"stack": edit(c.stack, 1, technology=_t("Synthetic CRM"))}
    ),
    "stack_category": lambda c: c.model_copy(
        update={"stack": edit(c.stack, 1, category=_t("CRM"))}
    ),
    "finding_claim": lambda c: c.model_copy(
        update={"findings": edit(c.findings, 0, claim=_t("Synthetic claim"))}
    ),
    "finding_sentence": lambda c: c.model_copy(
        update={"findings": edit(c.findings, 0, sentence=_t("Own words."))}
    ),
    "source_title": lambda c: c.model_copy(
        update={"sources": edit(c.sources, 1, title=_t("A page"), publisher=_t("A publisher"))}
    ),
    "jobs_count_label": lambda c: c.model_copy(
        update={
            "jobs_counts": c.jobs_counts.model_copy(
                update={
                    "lines": (("pulled", _t("Records"), 5, _t("raw records")),),
                    "sentences": (_t("3 postings read"),),
                }
            )
        }
    ),
    "people_count_sentence": lambda c: c.model_copy(
        update={
            "people_counts": c.people_counts.model_copy(update={"sentences": (_t("4 people"),)})
        }
    ),
    "pull_query": lambda c: c.model_copy(update={"pull_calls": _calls(_t("alpha platform"))}),
    "lookalike": lambda c: c.model_copy(
        update={
            "entity_lock": {
                "legal_name": "Synthetic Alpha Holdings Ltd",
                "blocked_lookalikes": [{"host": "x.example", "title": _t("Look"), "why": "other"}],
            }
        }
    ),
    "release": lambda c: c.model_copy(update={"releases": _releases(_t("A page that names"))}),
}


def _calls(query: str) -> tuple[Any, ...]:
    from datetime import UTC, datetime

    from exports.content import PullCall

    return (
        PullCall(
            at=datetime(2026, 9, 1, tzinfo=UTC),
            operation="web_search",
            step="web",
            queries=(query,),
            result_count=2,
        ),
    )


def _releases(text: str) -> tuple[Any, ...]:
    from datetime import date

    from exports.content import ReleaseRow

    return (
        ReleaseRow(
            kind="page",
            url="https://alpha.example/releases/a",
            title="A release",
            day=date(2026, 9, 1),
            text=text,
        ),
    )


@pytest.mark.parametrize("path", sorted(INJECTIONS))
async def test_a_restricted_term_in_any_printed_text_never_ends_the_research(
    make_builder: Make, path: str
) -> None:
    base = with_terms(account(), OTHER_NAME, OTHER_DOMAIN)
    c = INJECTIONS[path](base)
    result, store = await build(make_builder, c)
    # old code: several of these paths reached the produced bytes, the final scan fired and the
    # whole build failed (leak / missing_narrative); now the text is withheld and the set stands
    assert result.status == "complete", (path, result.errors)
    assert result.withheld_files == (), (path, result.withheld_files)
    text = stored_text(store, result).casefold()
    assert OTHER_NAME.casefold() not in text and OTHER_DOMAIN not in text


async def test_the_withheld_texts_are_counted_by_part_of_the_file_and_rule(
    make_builder: Make,
) -> None:
    c = with_terms(account(), OTHER_NAME)
    c = INJECTIONS["posting_description"](INJECTIONS["fact"](c))
    result, _ = await build(make_builder, c)
    parts = set(result.manifest.texts_withheld)
    assert "summary.lanes:leak=1" in parts and any(p.startswith("jobs:leak=") for p in parts), parts
    # every printing of the text counts (the Summary and the Evidence file both print a finding)
    assert result.manifest.leaks_withheld >= 2
    # counts and neutral names only: never the term, the account or the text
    assert OTHER_NAME.casefold() not in " ".join(result.manifest.texts_withheld).casefold()


# ---- short and common words as account names ------------------------------------------------


def test_matching_is_whole_word_any_case_and_skips_three_characters_or_fewer() -> None:
    assert leak_hits("An EPIC day, an epic story", ("Epic",))
    assert leak_hits("a long-running epic-scale plan", ("Epic",))  # a hyphen ends a word
    assert not leak_hits("an Epicenter of epics", ("Epic",))
    assert not leak_hits("a delta in revenue", ("Del",))  # three characters never count
    assert restricted_terms(["Del", "Epic", " ", ""], "6th") == ("Epic",)


async def test_an_account_named_like_an_ordinary_word_withholds_the_cell_not_the_research(
    make_builder: Make,
) -> None:
    c = with_terms(account(), "Epic")
    postings = edit(
        c.postings,
        0,
        description="We run agile teams: write each epic and its stories. Epicenter of IT.",
    )
    postings = edit(postings, 2, description="Epicenter of IT and agile stories.")
    c = c.model_copy(update={"postings": postings})
    result, store = await build(make_builder, c)
    assert result.status == "complete", result.errors
    assert kinds_of(result) == FILES
    # the one description that uses the word is withheld; the rows stay listed; a word that merely
    # starts with it is untouched
    assert result.manifest.leaks_withheld >= 1
    assert len(result.manifest.row_ids["jobs"]) == len(c.postings)
    jobs = everything_of(store, result, "jobs")
    assert "Epicenter of IT and agile stories." in jobs and "write each epic" not in jobs


def everything_of(store: Any, result: Any, kind: str) -> str:
    """Every text of one stored file."""
    from tests.exports.degrade_kit import everything

    d = next(x for x in result.deliverables if x.kind == kind)
    return everything(store.objects[d.object_key])


# ---- the last line of defence ---------------------------------------------------------------


async def test_a_text_the_mapping_missed_is_replaced_after_the_final_scan_not_fatal(
    make_builder: Make, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new field the mapping does not funnel: the produced file fails its final scan, the model's
    refused strings are replaced by the withheld note, the file is rendered again and the set
    stands; the manifest and the log say which file and rule (``late_replaced``)."""
    from exports.policy import sanitise

    def unchecked(self: MapContext, text: str | None, where: str) -> str:
        return self.mask(sanitise(text or ""))  # the mapping forgets to check

    monkeypatch.setattr(MapContext, "rendered", unchecked)
    c = INJECTIONS["posting_description"](with_terms(account(), OTHER_NAME))
    result, store = await build(make_builder, c)
    assert result.status == "complete", result.errors
    assert kinds_of(result) == FILES and not result.withheld_files
    assert result.manifest.late_replaced == ("jobs:leak=1",), result.manifest.late_replaced
    assert OTHER_NAME.casefold() not in stored_text(store, result).casefold()


async def test_a_refused_address_the_mapping_missed_leaves_that_file_out_not_the_run(
    make_builder: Make, monkeypatch: pytest.MonkeyPatch, cfg: Any
) -> None:
    """An address cannot be replaced by a note: when the final scan refuses one, that file alone
    is left out (``files_withheld``) and the other four and the zip are delivered."""
    monkeypatch.setattr(MapContext, "link_rule", lambda self, url: None)
    c = with_terms(account(), OTHER_NAME, OTHER_DOMAIN)
    c = _source_link(c, _row_of(c, "posting"), f"https://news.{OTHER_DOMAIN}/job")
    result, store = await build(make_builder, c)
    assert result.status == "complete", result.errors
    # the posting's address is printed in the Job Postings file and in the Evidence file's reads
    assert result.withheld_files == ("evidence:leak", "jobs:leak"), result.withheld_files
    assert kinds_of(result) == FILES - {"jobs", "evidence"}
    assert result.manifest.files_withheld == ("evidence:leak", "jobs:leak")
    assert OTHER_DOMAIN not in stored_text(store, result).casefold()
    # the zip holds the other four and says nothing of the fifth's rows
    members = _members(store, result)
    jobs_name = cfg.filenames["jobs"].replace("{account}", "Synthetic_Alpha_Ltd") + ".xlsx"
    assert f"deliverables/{jobs_name}" not in members
    assert any(n.startswith("deliverables/") and "People" in n for n in members)
    assert "build/jobs.json" not in members and "build/people.json" in members


def _members(store: Any, result: Any) -> dict[str, bytes]:
    import io
    import zipfile

    d = next(x for x in result.deliverables if x.kind == "workspace")
    with zipfile.ZipFile(io.BytesIO(store.objects[d.object_key])) as z:
        return {n: z.read(n) for n in z.namelist()}


async def test_finding_rows_are_listed_without_their_address(make_builder: Make) -> None:
    """A withheld finding is listed by person and a short hash: the manifest never carries the
    link (it can name the very account it was withheld for)."""
    c = with_terms(account(), OTHER_NAME, OTHER_DOMAIN)
    c = _source_link(c, _row_of(c, "finding"), f"https://{OTHER_DOMAIN}/post/1")
    result, _ = await build(make_builder, c)
    blocks = [b for b in result.manifest.withheld_blocks if "linkedin_findings" in b]
    assert blocks and all(OTHER_DOMAIN not in b for b in blocks), blocks
    assert FindingRow  # the finding row type is the corpus's


async def test_a_quote_citing_a_blocked_and_a_clean_source_prints_only_the_clean_code(
    make_builder: Make,
) -> None:
    """Executive voice and hypotheses cited every code the registry knew, a blocked one too: the
    Evidence file does not list a blocked source, so the citation was an orphan and the whole
    build failed (``orphan_citation``). A blocked code is dropped from the block's citations."""
    c = with_terms(account(), OTHER_NAME, OTHER_DOMAIN)
    c = _source_link(c, 2, f"https://{OTHER_DOMAIN}/page")
    blocked = c.sources[2].code
    clean = c.sections[0].codes[0]
    sections = tuple(
        s.model_copy(update={"codes": (clean, blocked)})
        if s.kind in ("executive_voice", "hypothesis")
        else s
        for s in c.sections
    )
    result, store = await build(make_builder, c.model_copy(update={"sections": sections}))
    assert result.status == "complete", result.errors
    assert not result.withheld_files
    assert blocked not in stored_text(store, result)
