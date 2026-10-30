"""The third-party rules (never name our data tools, never print the cost word) never end a
finished research either. Staging 2026-10-31: the same build refused one file on
``exports.build_failed ... tool_name=1`` (the rule name was a fixed label: the word the providers
use for their own unit, and a link to a provider's host, were both reported as ``tool_name``).

A job description that says "R&D tax credits", a news headline, a verbatim quote or a source link
that carries the word, a tool's host or a tool's name is withheld where it is printed (a cell, a
sentence, a row) and listed by rule; the set is delivered. The agent layer's own guard
(``agents.naming.assert_client_clean``, G9, applied to every built file) is applied to each text
by the builder, so a file the builder makes is never refused there."""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, date, datetime
from typing import Any

import pytest

from agents.naming import assert_client_clean
from agents.production.delivery import member_texts
from exports.builder import _third_party_rules
from exports.content import PullCall, ReleaseRow, SnapshotContent
from exports.policy import client_rule
from exports.readers.common import MapContext
from tests.exports.corpus import account, broker
from tests.exports.degrade_kit import Make, build, kinds_of, stored_text

pytestmark = pytest.mark.asyncio
FILES = {"summary", "evidence", "people", "jobs", "tech", "workspace"}
WORD = "credits"  # the word the providers use for their own unit (spec 12)


def edit(rows: tuple[Any, ...], i: int, **update: Any) -> tuple[Any, ...]:
    """``rows`` with row ``i`` changed."""
    return tuple(r.model_copy(update=update) if k == i else r for k, r in enumerate(rows))


def tool_host() -> str:
    """A data tool's own host, from the provider configuration (never spelled here)."""
    from exports.config import provider_hosts

    return next(h for h in provider_hosts() if "parallel" in h)


def assert_clean_for_the_agent_guard(result: Any, store: Any) -> None:
    """G9 over every text member of every stored file, as the research run applies it."""
    for d in result.deliverables:
        assert_client_clean(member_texts(store.objects[d.object_key]))


# ---- the word -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "where",
    ["description", "platform_sentence", "title", "fact", "evidence_sentence", "caveat", "person"],
)
async def test_the_cost_word_in_a_printed_text_is_withheld_not_fatal(
    make_builder: Make, where: str
) -> None:
    c = account()
    text = "Eligible for R&D tax credits and more"
    if where == "description":
        c = c.model_copy(update={"postings": edit(c.postings, 0, description=text)})
    elif where == "platform_sentence":
        c = c.model_copy(
            update={"postings": edit(c.postings, 0, platform_sentences=(f"Salesforce {text}",))}
        )
    elif where == "title":
        c = c.model_copy(update={"postings": edit(c.postings, 0, title=f"Admin, {text}")})
    elif where == "fact":
        c = c.model_copy(update={"facts": edit(c.facts, 1, value_text=text)})
    elif where == "evidence_sentence":
        c = c.model_copy(update={"evidence": edit(c.evidence, 1, supporting_sentence=text)})
    elif where == "caveat":
        sections = tuple(
            s.model_copy(update={"text": text}) if s.kind == "caveat" else s for s in c.sections
        )
        c = c.model_copy(update={"sections": sections})
    else:
        c = c.model_copy(update={"people": edit(c.people, 1, title=text)})
    result, store = await build(make_builder, c)
    # old code: the word reached the produced file, the third-party scan failed the whole build
    # (the log said tool_name) after the research had been paid for
    assert result.status == "complete", result.errors
    assert kinds_of(result) == FILES and not result.withheld_files
    assert WORD not in stored_text(store, result).casefold()
    # listed by rule: a text in a cell by part of the file, a whole block by its rule
    listed = (*result.manifest.texts_withheld, *result.manifest.withheld_blocks)
    assert any(":cost_word" in p for p in listed), listed


async def test_a_quote_that_says_the_word_is_withheld_and_its_link_kept(
    make_builder: Make,
) -> None:
    c = account()
    quote = f"Film {WORD} roll after the talk."
    c = c.model_copy(update={"evidence": edit(c.evidence, 0, supporting_sentence=quote)})
    result, store = await build(make_builder, c)
    assert result.status == "complete", result.errors
    assert result.manifest.withheld_sentences >= 1
    text = stored_text(store, result)
    assert WORD not in text.casefold() and c.evidence[0].url in text


async def test_the_rule_is_named_by_a_neutral_label_the_manifest_may_carry(
    make_builder: Make,
) -> None:
    """The manifest is embedded in the zip and the zip is scanned for the word: the rule that held
    a text is ``cost_word``, never the word, so the listing cannot itself fail the scan."""
    c = account().model_copy(update={"postings": edit(account().postings, 0, description=WORD)})
    result, _ = await build(make_builder, c)
    assert result.status == "complete" and not result.withheld_files
    listed = " ".join((*result.manifest.texts_withheld, *result.manifest.withheld_blocks))
    assert "cost_word" in listed and WORD not in listed.casefold()


def test_the_third_party_scans_rules_are_reported_by_name_and_never_all_as_tool_name() -> None:
    # the details a scan returns: ``member: pattern-kind`` and ``data_tool_host: host``
    assert _third_party_rules(("word/document.xml: credits",)) == "cost_word"
    assert _third_party_rules(("xl/sharedStrings.xml: tool_name",)) == "tool_name"
    assert _third_party_rules(("data_tool_host: example.test",)) == "data_tool_host"
    assert (
        _third_party_rules(("a.xml: credits", "b.xml: tool_name", "data_tool_host: h"))
        == "cost_word,data_tool_host,tool_name"
    )
    assert _third_party_rules(()) == "tool_name"


# ---- links ----------------------------------------------------------------------------------


async def test_a_news_link_that_carries_the_word_withholds_its_row_not_the_research(
    make_builder: Make,
) -> None:
    c = account()
    url = "https://news.example/rd-tax-credits-explained"
    old = c.sources[2].url
    c = c.model_copy(
        update={
            "sources": edit(c.sources, 2, url=url),
            "evidence": tuple(
                e.model_copy(update={"url": url}) if e.url == old else e for e in c.evidence
            ),
        }
    )
    result, store = await build(make_builder, c)
    assert result.status == "complete", result.errors
    assert any(b.endswith(":cost_word_link") for b in result.manifest.withheld_blocks), (
        result.manifest.withheld_blocks
    )
    assert "tax-credits" not in stored_text(store, result)


async def test_a_posting_on_a_data_tools_host_is_withheld_and_listed(make_builder: Make) -> None:
    c = account()
    url = f"https://{tool_host()}/jobs/1"
    old = c.postings[1].url
    c = c.model_copy(
        update={
            "postings": edit(c.postings, 1, url=url),
            "sources": tuple(
                s.model_copy(update={"url": url}) if s.url == old else s for s in c.sources
            ),
        }
    )
    result, store = await build(make_builder, c)
    # old code: the link reached the file and the third-party scan failed the build
    assert result.status == "complete", result.errors
    assert any(b.endswith(":tool_link") for b in result.manifest.withheld_blocks)
    assert tool_host() not in stored_text(store, result)


async def test_a_data_tools_host_in_a_description_is_withheld_not_fatal(
    make_builder: Make,
) -> None:
    c = account()
    c = c.model_copy(
        update={
            "postings": edit(c.postings, 0, description=f"Apply at https://{tool_host()}/apply")
        }
    )
    result, store = await build(make_builder, c)
    assert result.status == "complete", result.errors
    assert tool_host() not in stored_text(store, result)
    assert any(":data_tool_host=" in p for p in result.manifest.texts_withheld)


# ---- the agent layer's guard (G9) -----------------------------------------------------------


async def test_a_title_the_agent_guard_refuses_is_withheld_so_the_run_is_not_failed(
    make_builder: Make,
) -> None:
    """ "Parallel Computing" is an ordinary job title and a data provider's name: the guard the
    research run applies to every built file (G9) refuses it in any position but the lowercase
    phrases "in parallel" and "parallel processing". The builder passed it and the run was FAILED
    after the build (``client_output_leak``); now the title cell is withheld in the build."""
    assert client_rule("Parallel Computing Engineer") == "provider_name"
    c = account()
    c = c.model_copy(update={"postings": edit(c.postings, 0, title="Parallel Computing Engineer")})
    result, store = await build(make_builder, c)
    assert result.status == "complete", result.errors
    assert any(":provider_name=" in p for p in result.manifest.texts_withheld)
    assert_clean_for_the_agent_guard(result, store)


async def test_the_lowercase_english_phrases_stay_printed_in_full(make_builder: Make) -> None:
    text = "Works in parallel with data engineers; parallel processing of jobs."
    assert client_rule(text) is None
    c = account()
    c = c.model_copy(update={"postings": edit(c.postings, 0, description=text)})
    result, store = await build(make_builder, c)
    assert result.status == "complete", result.errors
    assert "Works in parallel with data engineers" in stored_text(store, result)
    assert_clean_for_the_agent_guard(result, store)


async def test_every_built_file_passes_the_agent_guard(make_builder: Make) -> None:
    result, store = await build(make_builder, account())
    assert result.status == "complete", result.errors
    assert_clean_for_the_agent_guard(result, store)


# ---- the account's own name -----------------------------------------------------------------


@pytest.mark.parametrize(
    "name", [f"Synthetic {WORD} Holdings", "Parallel Computing Group", "Parallel Search Labs"]
)
async def test_an_account_named_like_a_tool_or_the_cost_word_still_builds(
    make_builder: Make, name: str
) -> None:
    """The name is in every file's title, header, properties and file name: an account called
    after one used to refuse all five. The refused words are masked in the name the files print."""
    c = account(name)
    result, store = await build(make_builder, c)
    assert result.status == "complete", result.errors
    assert kinds_of(result) == FILES and not result.withheld_files
    text = stored_text(store, result)
    assert WORD not in text.casefold() and "Parallel" not in text.replace("[withheld]", "")
    assert_clean_for_the_agent_guard(result, store)


# ---- the run's own records ------------------------------------------------------------------


def _members(store: Any, result: Any) -> dict[str, str]:
    d = next(x for x in result.deliverables if x.kind == "workspace")
    with zipfile.ZipFile(io.BytesIO(store.objects[d.object_key])) as z:
        return {n: z.read(n).decode("utf-8", "ignore") for n in z.namelist()}


async def test_a_search_query_that_names_the_word_or_a_term_is_scrubbed_in_the_pull_log(
    make_builder: Make,
) -> None:
    other = "Zorblax Industries"
    queries = (f"alpha R&D tax {WORD}", f"alpha vs {other}", f"https://{tool_host()}/x alpha")
    calls = tuple(
        PullCall(
            at=datetime(2026, 9, 1, tzinfo=UTC),
            operation="web_search",
            step="web",
            queries=(q,),
            result_count=2,
        )
        for q in queries
    )
    c = account().model_copy(update={"pull_calls": calls, "forbidden_terms": (other,)})
    result, store = await build(make_builder, c)
    # old code: the zip scan failed the whole build on the record
    assert result.status == "complete", result.errors
    assert kinds_of(result) == FILES
    members = _members(store, result)
    log = members["notes/pull_log.md"] + members["data/web_searches.json"]
    assert "[withheld]" in log
    text = "\n".join(members.values()).casefold()
    assert WORD not in text and other.casefold() not in text and tool_host() not in text
    assert result.manifest.members_scrubbed, "the scrubbed members are listed with their counts"
    assert all("=" in m for m in result.manifest.members_scrubbed)


async def test_a_page_copy_that_names_a_tool_keeps_a_cleaned_name_and_title(
    make_builder: Make,
) -> None:
    name = broker("p_scrape")
    row = ReleaseRow(
        kind="page",
        url="https://alpha.example/releases/a",
        title=f"Press: {name} data",
        day=date(2026, 9, 1),
        text="A page.",
    )
    result, store = await build(make_builder, account().model_copy(update={"releases": (row,)}))
    assert result.status == "complete", result.errors
    names = " ".join(_members(store, result))
    assert name.casefold() not in names.casefold()
    assert not result.withheld_files


# ---- the last line of defence ---------------------------------------------------------------


async def test_a_word_the_mapping_missed_is_replaced_after_the_scan_and_reported_by_rule(
    make_builder: Make, monkeypatch: pytest.MonkeyPatch
) -> None:
    from exports.policy import sanitise

    monkeypatch.setattr(
        MapContext, "rendered", lambda self, text, where: self.mask(sanitise(text or ""))
    )
    c = account().model_copy(
        update={"postings": edit(account().postings, 0, description=f"Earn {WORD} here.")}
    )
    result, store = await build(make_builder, c)
    assert result.status == "complete", result.errors
    assert not result.withheld_files
    assert result.manifest.late_replaced == ("jobs:cost_word=1",)
    assert WORD not in stored_text(store, result).casefold()


def test_the_cost_word_and_the_tool_host_are_not_a_leak_of_the_run_itself(
    make_builder: Make,
) -> None:
    c: SnapshotContent = account()
    ctx = MapContext(c, make_builder(c).cfg, make_builder(c).grading)
    assert ctx.block_rule("plain text") is None
    assert ctx.block_rule(f"{WORD} are not spent") == "cost_word"
    assert ctx.block_rule(f"see https://{tool_host()}/x") == "data_tool_host"
    assert ctx.link_rule("https://news.example/film-credits") == "cost_word_link"
    assert ctx.link_rule(f"https://{tool_host()}/x") == "tool_link"
    assert ctx.link_rule("https://news.example/story") is None


async def test_an_excluded_persons_name_the_scanner_denies_is_not_listed_not_fatal() -> None:
    """The people export scans everything it lists; a title-excluded person is listed by name, so a
    name the client-output scanner denies ended the whole read (``LeakError: people export``). The
    person stays counted in the summary lines and is not named."""
    from people.export import build_export
    from tests.fakes.people import Corpus
    from tests.people.kit import Kit

    result = await Kit(Corpus()).run()
    assert result.counts is not None
    ann = next(p for p in result.people if p.name == "Ann Lee")
    named = ann.model_copy(update={"name": broker("p_scrape")})
    people = [named if p is ann else p for p in result.people]
    export = build_export(people, result.counts, result.count_sentences, result.source_codes)
    assert broker("p_scrape") not in {n for n, _r, _t in export.excluded}
    assert ("excluded", "security", 1) in export.summary


def test_the_application_container_registers_the_agent_guard_before_any_build() -> None:
    """The builder applies G9's rule through a registry the agent layer fills at import; a process
    that built files without importing it would lose the rule. The application container (the
    API and the worker) imports it, and the research graph imports it with its guard."""
    import inspect

    from account_intelligence.app import container

    assert "agents import naming" in inspect.getsource(container)
    from agents import graph

    assert "from agents.naming import" in inspect.getsource(graph)
    assert client_rule("Parallel Computing") == "provider_name"
