"""Scale smoke (spec 9.10): a large synthetic account (10,000 postings, 5,000 sources) builds,
conserves every row and stays within a memory cap. Time and peak memory are MEASURED and printed
(``-s``); no number is asserted except the generous cap, because the spec gives none."""

from __future__ import annotations

import time
import tracemalloc
from datetime import date

import pytest

from account_intelligence.grading.config import load_grading_config
from exports.builder import ExportBuilder
from exports.config import load_export_config
from exports.content import PostingRow, SourceRow
from tests.exports.corpus import account
from tests.fakes.exports import FakeReader, InProcessRenderer, MemoryStore

MEMORY_CAP = 2 * 1024**3  # 2 GiB of traced Python allocations: a smoke ceiling, not a target


def large() -> object:
    base = account(
        "Synthetic Scale Corp", "scale.example", n_facts=0, n_people=0, n_postings=0, n_detections=0
    )
    sources = list(base.sources)
    postings = []
    for i in range(10_000):
        url = f"https://scale.example/jobs/{i}"
        sources.append(
            SourceRow(code=f"S-{len(sources) + 1:04d}", url=url, published=None, kind="primary")
        )
        postings.append(
            PostingRow(
                posting_key=f"post-{i:06d}",
                title=f"Synthetic Salesforce Role {i}",
                url=url,
                status=("open", "closed")[i % 2],
                posting_class="admin_owner",
                platforms=("Salesforce",),
                platform_sentences=("Synthetic Salesforce sentence.",),
                description="Synthetic description of the role. " * 40,
                posted_at=date(2026, 1 + i % 9, 1),
                full_text_read=i % 3 == 0,
            )
        )
    while len(sources) < 5_000 + 1:
        sources.append(
            SourceRow(
                code=f"S-{len(sources) + 1:04d}",
                url=f"https://scale.example/s/{len(sources)}",
                published=None,
                kind="secondary",
            )
        )
    return base.model_copy(
        update={"postings": tuple(postings), "sources": tuple(sources), "jobs_counts": None}
    )


@pytest.mark.asyncio
async def test_large_account_builds_within_the_memory_cap() -> None:
    content = large()
    store = MemoryStore()
    builder = ExportBuilder(
        FakeReader(content), store, InProcessRenderer(), load_export_config(), load_grading_config()
    )
    tracemalloc.start()
    started = time.perf_counter()
    result = await builder.build_all(content.tenant_id, content.run_id)  # type: ignore[attr-defined]
    seconds = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert result.status == "complete", result.errors
    assert result.manifest.rows_per_file["jobs"] == 10_000
    assert result.manifest.rows_per_file["evidence"] == 10_000 + 1 + 3334  # sources + job reads
    assert peak < MEMORY_CAP
    sizes = {d.kind: d.size for d in result.deliverables}
    print(
        f"\nSCALE 10000 postings, {len(content.sources)} sources: {seconds:.1f} s,"  # type: ignore[attr-defined]
        f" peak traced memory {peak / 1024**2:.0f} MiB, sizes {sizes}"
    )
