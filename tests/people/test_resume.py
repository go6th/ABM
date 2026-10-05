"""Resume matrix (spec 9.6): a crash at each named point, then a resumed stage, must reach the
same final rows as an uninterrupted run, with no duplicate paid call beyond the documented bound
(one call per crash between a provider response and the ledger write; ARCH 5.5).

The fake ledger replays identical calls for free, like the connector ledger; search pages,
profiles and posts are stored as they arrive (``FakeRepo``), so a resume continues from the
stored cursor and never re-pulls a stored profile. The real-database version of the persist
points is in ``tests/people/test_repo_db.py``.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from people.pipeline import PipelineResult
from tests.fakes.people import Corpus, Crash, FakeRepo
from tests.people.kit import Kit


def rows_hash(result: PipelineResult) -> str:
    rows = [
        (p.person_key, p.status, p.classification.reason, p.merged_into, p.possible_duplicate)
        for p in result.people
    ]
    counts = result.counts.model_dump(mode="json") if result.counts else None
    body = json.dumps([rows, counts, [c.local_id for c in result.claims]], sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


async def _baseline() -> tuple[str, int]:
    corpus = Corpus()
    result = await Kit(corpus).run()
    return rows_hash(result), corpus.ledger.paid


async def _crash_then_resume(
    kill_at: str | None, kill_after: int = 0, ledger_kill: str | None = None
) -> tuple[str, int]:
    corpus = Corpus()
    repo = FakeRepo()
    repo.kill_at, repo.kill_after = kill_at, kill_after
    kit = Kit(corpus, repo=repo)
    if ledger_kill is not None:
        kit.ledger.crash_after_response.add(kit.ledger.ref(*json.loads(ledger_kill)))
    with pytest.raises(Crash):
        await kit.run()
    resumed = Kit(corpus, repo=repo)  # a new process: same store, same ledger
    result = await resumed.run()
    return rows_hash(result), corpus.ledger.paid


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kill_at", "kill_after"),
    [
        ("save_page", 0),  # before the first search page is stored
        ("save_page", 3),  # mid-pagination
        ("save_profiles", 0),  # mid-profile batch
        ("save_posts", 1),  # mid-posts
        ("save_run", 0),  # mid-persist of the record
    ],
)
async def test_resume_after_a_crash_reaches_the_same_rows(kill_at: str, kill_after: int) -> None:
    expected_hash, expected_paid = await _baseline()
    got_hash, paid = await _crash_then_resume(kill_at, kill_after)
    assert got_hash == expected_hash
    assert paid == expected_paid  # every call before the crash was ledgered: replayed for free


@pytest.mark.asyncio
async def test_crash_between_provider_response_and_ledger_write_costs_at_most_one_call() -> None:
    expected_hash, expected_paid = await _baseline()
    key = json.dumps(["profile", [["https://www.linkedin.com/in/raj-patel"]][0]])
    got_hash, paid = await _crash_then_resume(None, ledger_kill=key)
    assert got_hash == expected_hash
    assert paid == expected_paid + 1  # the documented bound: one repeated call per crash


@pytest.mark.asyncio
async def test_after_persist_before_checkpoint_the_stage_replays_the_record() -> None:
    corpus = Corpus()
    kit = Kit(corpus)
    first = await kit.run()
    paid = corpus.ledger.paid
    again = await Kit(corpus, repo=kit.repo).run()  # the checkpoint was lost; the record exists
    assert again.replayed and rows_hash(again) == rows_hash(first)
    assert corpus.ledger.paid == paid
    assert [e["row"] for e in kit.rec.evidence] == [e.row_key for e in again.evidence]
