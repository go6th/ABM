"""Two reading repairs from the Olympus research (staging 2026-11-04: 144 postings, 23 read, none
matched): with the caps off a feed text whose completeness the contract does not state is read,
and a page that names none of the platforms the feed text named is not taken for the posting."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from jobs.models import RawPosting
from jobs.pipeline import JobsPipeline
from jobs.stage import run_jobs
from tests.fakes.jobs import FakeLlm, FakePages, FakePostingSource, FakeRunInput, Recorder
from tests.jobs.conftest import CANARY, frozen_config, is_hostile, markers

pytestmark = pytest.mark.asyncio

AS_OF = date(2026, 10, 1)
URL = "https://careers.acme.example/jobs/k1"
PAD = (
    " This synthetic posting text exists only to test the pipeline and describes no real role." * 3
)
NAMES_SAP = "As a financial analyst you will report month end results in SAP." + PAD
NO_PLATFORM = "As a financial analyst you will report month end results to the controller." + PAD
SHELL = "Careers at Acme. Search jobs. Sign in. Create an account. Cookie settings. Privacy." + PAD
FULL_SALESFORCE = (
    "The analyst logs every forecast in Salesforce and reports to the controller." + PAD
)


def _feed(description: str, complete: bool | None) -> RawPosting:
    return RawPosting(
        source_id="k1",
        title="Financial Analyst",
        description=description,
        url=URL,
        provider_code="p_signals",
        raw_ref="synthetic/raw/k1",
        retrieved_at="2026-10-01T00:00:00+00:00",
        posted_at="2026-06-01",
        first_seen_at="2026-06-01",
        status="open",
        text_complete=complete,
    )


async def _run(description: str, complete: bool | None, page: str | None, *, read_excerpts: bool):
    cfg = replace(frozen_config(), read_excerpts=read_excerpts)
    source = FakePostingSource({}, [_feed(description, complete)])
    pages = FakePages({URL: {"ok": True, "text": page}} if page is not None else {})
    pipeline = JobsPipeline(
        cfg,
        source,
        pages,
        FakeLlm({}),
        FakeLlm({}),
        canary=CANARY,
        hostile_markers=markers(),
        is_hostile=is_hostile,
    )
    rec = Recorder()
    result = await run_jobs(
        "run-k", pipeline, rec, rec, rec, rec, FakeRunInput(frozenset({"veeva"}), AS_OF)
    )
    posting = next(p for p in result.postings if p.posting_key == "id:k1")
    return posting, pages


async def test_a_page_that_names_none_of_the_feeds_platforms_is_not_the_posting() -> None:
    """The page is a shell, so the feed text stays and is read: it is the only copy of the
    posting (staging 2026-11-06: 22 matched postings, all left unread, an empty Jobs file)."""
    posting, pages = await _run(NAMES_SAP, None, SHELL, read_excerpts=True)
    assert pages.calls == [URL]  # the one read was spent
    assert posting.platforms == ("SAP",)  # the feed's match stands
    assert posting.escalation == "failed" and posting.read and not posting.full_text_missing
    assert posting.sentences  # the platform sentence comes from the feed text


async def test_a_feed_excerpt_whose_page_is_a_shell_stays_unread() -> None:
    posting, pages = await _run(NAMES_SAP, False, SHELL, read_excerpts=True)
    assert pages.calls == [URL]
    assert not posting.read and posting.escalation == "failed" and posting.full_text_missing


async def test_a_failed_page_read_reads_the_feed_description() -> None:
    posting, pages = await _run(NAMES_SAP, None, None, read_excerpts=True)
    assert pages.calls == [URL] and posting.read and posting.platforms == ("SAP",)


async def test_a_short_page_does_not_replace_a_longer_unmatched_feed_text() -> None:
    posting, _ = await _run(NO_PLATFORM + PAD * 4, None, SHELL, read_excerpts=True)
    assert posting.escalation == "failed" and posting.read
    assert posting.text_sha256 is not None and posting.platforms == ()


async def test_a_page_that_carries_the_feeds_platform_is_the_posting() -> None:
    page = "The analyst reports month end results in SAP and Salesforce." + PAD
    posting, pages = await _run(NAMES_SAP, None, page, read_excerpts=True)
    assert pages.calls == [URL]
    assert posting.read and posting.escalation == "read"
    assert posting.platforms == ("SAP", "Salesforce")


async def test_with_the_caps_off_a_feed_text_of_unknown_completeness_is_read() -> None:
    posting, pages = await _run(NO_PLATFORM, None, FULL_SALESFORCE, read_excerpts=True)
    assert pages.calls == [URL]
    assert posting.read and posting.platforms == ("Salesforce",)


async def test_with_the_caps_on_an_unmatched_feed_text_is_still_not_read() -> None:
    posting, pages = await _run(NO_PLATFORM, None, FULL_SALESFORCE, read_excerpts=False)
    assert pages.calls == [] and posting.platforms == () and not posting.read


async def test_a_complete_unmatched_feed_text_is_not_read_even_with_the_caps_off() -> None:
    posting, pages = await _run(NO_PLATFORM, True, FULL_SALESFORCE, read_excerpts=True)
    assert pages.calls == [] and not posting.full_text_missing


async def test_a_carried_unread_lead_is_read_from_its_stored_feed_text() -> None:
    """A second research carries the previous run's postings unread as they were; one a run
    before 2026-11-06 left unread after a shell page is read from its stored feed text now, with
    no page read (staging, Olympus run 739d6e34: the Jobs file was empty again)."""
    from jobs.models import PreviousPostings

    old, _ = await _run(NAMES_SAP, False, SHELL, read_excerpts=True)
    broken = old.model_copy(update={"text_complete": None})  # the state the old rule stored
    assert not broken.read and broken.escalation == "failed" and broken.platforms == ("SAP",)
    cfg = replace(frozen_config(), read_excerpts=True)
    pages = FakePages({URL: {"ok": True, "text": SHELL}})
    pipeline = JobsPipeline(
        cfg,
        FakePostingSource({}, []),
        pages,
        FakeLlm({}),
        FakeLlm({}),
        canary=CANARY,
        hostile_markers=markers(),
        is_hostile=is_hostile,
    )
    previous = PreviousPostings(run_id="run-k", as_of=AS_OF, postings=(broken,))
    result = await pipeline.run_pass(
        "run-k2", frozenset({"veeva"}), AS_OF, previous, {broken.posting_key: NAMES_SAP}
    )
    posting = next(p for p in result.postings if p.posting_key == "id:k1")
    assert posting.read and posting.sentences and posting.platforms == ("SAP",)
    assert pages.calls == []  # no page read: the stored feed text is read


async def test_a_carried_excerpt_lead_stays_unread() -> None:
    from jobs.models import PreviousPostings

    old, _ = await _run(NAMES_SAP, False, SHELL, read_excerpts=True)
    cfg = replace(frozen_config(), read_excerpts=True)
    pipeline = JobsPipeline(
        cfg,
        FakePostingSource({}, []),
        FakePages({}),
        FakeLlm({}),
        FakeLlm({}),
        canary=CANARY,
        hostile_markers=markers(),
        is_hostile=is_hostile,
    )
    previous = PreviousPostings(run_id="run-k", as_of=AS_OF, postings=(old,))
    result = await pipeline.run_pass(
        "run-k2", frozenset({"veeva"}), AS_OF, previous, {old.posting_key: NAMES_SAP}
    )
    assert not next(p for p in result.postings if p.posting_key == "id:k1").read
