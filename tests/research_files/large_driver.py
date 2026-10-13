"""Subprocess driver for the large-file test: uploads a file from disk in parts (streamed, never
read whole), processes it, uploads it again (dedupe), and prints one JSON line with the peak
resident memory, timings, units, model spend and the dedupe result.

Usage: python -m tests.research_files.large_driver <tenant> <file> <blob_root>
"""

from __future__ import annotations

import asyncio
import json
import resource
import sys
import time
from pathlib import Path
from uuid import UUID


async def upload_from_disk(svc, tenant: UUID, path: Path):  # type: ignore[no-untyped-def]
    size = path.stat().st_size
    opened = await svc.uploads.init(tenant, "user-1", path.name, size, "text/csv")
    part = opened["part_size"]
    with path.open("rb") as handle:
        for n in range(opened["parts_total"]):
            handle.seek(n * part)
            left = min(part, size - n * part)

            async def body(left: int = left):  # type: ignore[no-untyped-def]
                remaining = left
                while remaining:
                    block = handle.read(min(1 << 20, remaining))
                    remaining -= len(block)
                    yield block

            await svc.uploads.put_part(tenant, UUID(opened["upload_id"]), n, body())
    return await svc.uploads.complete(tenant, UUID(opened["upload_id"]), "user-1")


async def main(tenant: str, file_path: str, root: str) -> None:
    from account_intelligence.db.session import tenant_txn
    from research_files import repo
    from tests.research_files.world import services

    svc = services(Path(root))
    t = UUID(tenant)
    path = Path(file_path)
    started = time.monotonic()
    done = await upload_from_disk(svc, t, path)
    uploaded = time.monotonic()
    await svc.processor().process(t, done.file_id)
    processed = time.monotonic()
    again = await upload_from_disk(svc, t, path)
    deduped = time.monotonic()
    async with tenant_txn(t) as s:
        row = await repo.get_file(s, done.file_id)
        spend = await repo.spend(s, done.file_id)
    peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(
        json.dumps(
            {
                "bytes": path.stat().st_size,
                "peak_rss_mb": round(peak_kb / 1024, 1),
                "upload_s": round(uploaded - started, 1),
                "process_s": round(processed - uploaded, 1),
                "reupload_s": round(deduped - processed, 1),
                "status": row.status,
                "units": row.units_done,
                "rows": (row.analytics or {}).get("tables", [{}])[0].get("rows"),
                "model_spend": spend,
                "dedupe_same_file": again.deduplicated and again.file_id == done.file_id,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:4]))
