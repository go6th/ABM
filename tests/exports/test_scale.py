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
    # the register, the job reads (part B), the same reads by lane (part C, all Salesforce), the
    # data pull counts (part H, five lines, plus one platform postings row and one entity split
    # row) and one written-out URL per source (part I)
    assert result.manifest.rows_per_file["evidence"] == (
        (10_000 + 1) + 3334 + 3334 + 5 + 1 + 1 + (10_000 + 1)
    )
    assert peak < MEMORY_CAP
    sizes = {d.kind: d.size for d in result.deliverables}
    print(
        f"\nSCALE 10000 postings, {len(content.sources)} sources: {seconds:.1f} s,"  # type: ignore[attr-defined]
        f" peak traced memory {peak / 1024**2:.0f} MiB, sizes {sizes}"
    )


# ---- fix wave C (2026-10-29): 7,000 postings with real-sized descriptions ---------------------
# The review's account: 7,000 postings (descriptions of 2,000 to 12,000 characters, all read in
# full) and 800 people took 219 s and 1.7 GB before the fix (quadratic hyperlink relationships in
# python-docx, a leak scan run per pattern per cell, a Word table of a million cells held as an
# element tree, a dash scan replacing thousands of passages over a 100 MB text). The measurement
# runs in a child process so the peak memory is this build's alone.

BIG_SECONDS = 240.0  # measured 65 s to 110 s (the same machine on a slow day); CI gets slack
BIG_RSS_MIB = 1000.0  # measured 692 MiB


def peak_rss_mib() -> float:
    """This process's own peak resident memory. ``VmHWM`` is per process image: the
    ``ru_maxrss`` of a child started from a large parent (the test run) still carries the
    parent's peak, which made a 700 MiB build read as 6 GiB."""
    from pathlib import Path

    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmHWM:"):
            return int(line.split()[1]) / 1024
    import resource

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def build_big(n_postings: int) -> dict[str, float]:
    """Build the account in this process; seconds and peak resident memory (MiB)."""
    import asyncio
    import random

    from exports.content import PersonRow

    random.seed(7)
    base = account(
        "Synthetic Scale Corp", "scale.example", n_facts=6, n_people=0, n_postings=0, n_detections=2
    )
    sources = list(base.sources)
    postings = []
    sentence = "Synthetic Salesforce description of the role and its daily work with the platform. "
    for i in range(n_postings):
        url = f"https://scale.example/jobs/{i}"
        sources.append(
            SourceRow(code=f"S-{len(sources) + 1:04d}", url=url, published=None, kind="secondary")
        )
        size = random.randint(2000, 12000)
        postings.append(
            PostingRow(
                posting_key=f"post-{i:06d}",
                title=f"Synthetic Salesforce Role {i}",
                url=url,
                status=("open", "closed", "unknown")[i % 3],
                posting_class=("admin_owner", "working_tool", "user", "build")[i % 4],
                platforms=("Salesforce",),
                platform_sentences=("Synthetic Salesforce sentence.",),
                platform_sentence_pairs=(("Salesforce", "Synthetic Salesforce sentence."),),
                description=(sentence * (size // len(sentence) + 1))[:size],
                posted_at=date(2026, 1 + i % 9, 1),
                first_seen=date(2026, 1 + i % 9, 2),
                full_text_read=True,
            )
        )
    people = []
    for i in range(800):
        s = SourceRow(
            code=f"S-{len(sources) + 1:04d}",
            url=f"https://www.linkedin.com/in/scale-{i}",
            published=None,
            kind="secondary",
        )
        sources.append(s)
        people.append(
            PersonRow(
                id=f"pk-{i:05d}",
                name=f"Synthetic Person {i:04d}",
                title="Synthetic Director",
                level="director",
                lanes="salesforce",
                priority="AB"[i % 2],
                profile_url=s.url,
                source_code=s.code,
            )
        )
    content = base.model_copy(
        update={
            "postings": tuple(postings),
            "sources": tuple(sources),
            "people": tuple(people),
            "jobs_counts": None,
        }
    )
    builder = ExportBuilder(
        FakeReader(content),
        MemoryStore(),
        InProcessRenderer(),
        load_export_config(),
        load_grading_config(),
    )
    started = time.perf_counter()
    result = asyncio.run(builder.build_all(content.tenant_id, content.run_id))
    seconds = time.perf_counter() - started
    assert result.status == "complete", result.errors
    assert result.manifest.rows_per_file["jobs"] == n_postings
    return {"seconds": seconds, "rss_mib": peak_rss_mib()}


def test_seven_thousand_postings_build_in_bounded_time_and_memory() -> None:
    import json
    import subprocess
    import sys

    done = subprocess.run(  # noqa: S603 - fixed argv, this interpreter, no shell
        [sys.executable, "-m", "tests.exports.test_scale", "7000"],
        capture_output=True,
        text=True,
        timeout=900,
        check=True,
    )
    stats = json.loads(done.stdout.strip().splitlines()[-1])
    print(f"\nSCALE 7000 postings, 800 people: {stats}")
    assert stats["seconds"] < BIG_SECONDS, stats
    assert stats["rss_mib"] < BIG_RSS_MIB, stats


if __name__ == "__main__":
    import json
    import sys

    print(json.dumps(build_big(int(sys.argv[1]))))
