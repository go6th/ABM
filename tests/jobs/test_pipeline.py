from datetime import date

import pytest

from jobs.models import RawPosting
from jobs.pipeline import JobsPipeline
from tests.fakes.jobs import FakePostingRepo, FakePostingSource


@pytest.mark.asyncio
async def test_stage_reentry_uses_stored_rows_without_repull() -> None:
    source = FakePostingSource(
        [
            RawPosting(
                source_id="job-1",
                title="Role",
                description="Text",
                url="https://example.org/job-1",
                provider_code="p_test",
                raw_ref="raw:1",
                retrieved_at="2026-10-01T00:00:00Z",
                posted_at="2026-05-01",
                text_complete=True,
            )
        ]
    )
    repo = FakePostingRepo()
    pipeline = JobsPipeline(source, repo)
    first = await pipeline.run("run-1", ["salesforce"], date(2026, 10, 1))
    second = await pipeline.run("run-1", ["salesforce"], date(2026, 10, 1))
    assert first == second
    assert source.calls == 1
    assert repo.writes == 1
