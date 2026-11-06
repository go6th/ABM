"""An archived page a client-output rule refuses is kept in the workspace zip with the refused
words scrubbed to the record marker (as the run's own records are); one still refused after
scrubbing is left out. Staging 2026-11-04: twelve archived pages left out whole, one word each."""

from __future__ import annotations

from datetime import date

import pytest

from exports.content import ReleaseRow
from tests.exports.corpus import account
from tests.exports.degrade_kit import OTHER_DOMAIN, OTHER_NAME, Make, build, everything
from tests.exports.test_build_degrades_restricted import with_terms

pytestmark = pytest.mark.asyncio


def _release(text: str) -> ReleaseRow:
    return ReleaseRow(
        kind="page",
        url="https://alpha.example/releases/scrubbed",
        title="A release",
        day=date(2026, 9, 1),
        text=text,
    )


def _release_members(store, result) -> dict[str, str]:  # type: ignore[no-untyped-def]
    import io
    import zipfile

    d = next(x for x in result.deliverables if x.kind == "workspace")
    with zipfile.ZipFile(io.BytesIO(store.objects[d.object_key])) as z:
        return {n: z.read(n).decode() for n in z.namelist() if n.startswith("data/releases/")}


async def test_a_page_naming_a_restricted_term_is_kept_scrubbed(make_builder: Make) -> None:
    text = (
        f"Synthetic Alpha signed a supply deal with {OTHER_NAME} for the new plant.\nA second line."
    )
    c = with_terms(account(), OTHER_NAME, OTHER_DOMAIN).model_copy(
        update={"releases": (_release(text),)}
    )
    result, store = await build(make_builder, c)
    assert result.status == "complete", result.errors
    members = _release_members(store, result)
    assert len(members) == 1, members.keys()
    body = next(iter(members.values()))
    assert OTHER_NAME.casefold() not in body.casefold()
    assert "[withheld]" in body and "for the new plant." in body
    assert not [b for b in result.manifest.withheld_blocks if b.startswith("workspace.releases")]
    # nothing of the term reached any file of the set
    text_all = "\n".join(everything(store.objects[d.object_key]) for d in result.deliverables)
    assert OTHER_NAME.casefold() not in text_all.casefold()


async def test_a_release_title_naming_a_restricted_word_keeps_the_zip(make_builder: Make) -> None:
    """The package scan reads every member name; a restricted word in a release title reached
    the copy's file name and withheld the whole workspace zip (staging 2026-11-06, Olympus)."""
    word = "Zorblax"
    row = _release("Synthetic Alpha opened a new plant.\nA second line.").model_copy(
        update={"title": f"Synthetic Alpha and {word} sign a deal"}
    )
    c = with_terms(account(), word, OTHER_DOMAIN).model_copy(update={"releases": (row,)})
    result, store = await build(make_builder, c)
    assert result.status == "complete", result.errors
    assert any(d.kind == "workspace" for d in result.deliverables)
    members = _release_members(store, result)
    assert len(members) == 1 and word.casefold() not in next(iter(members)).casefold()
    text_all = "\n".join(everything(store.objects[d.object_key]) for d in result.deliverables)
    assert word.casefold() not in text_all.casefold()


def test_a_platform_name_is_never_a_restricted_term() -> None:
    """Another account named like a lexicon platform (workday.com) made "Workday" restricted,
    and the Job Postings file withheld its Workday row (staging 2026-11-06)."""
    from exports.policy import restricted_terms

    kept = restricted_terms(["Workday", "Salesforce", "Zorblax Industries"], "6th")
    assert kept == ("Zorblax Industries",)
