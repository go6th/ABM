"""A multi-GB document (default 2 GiB of Markdown notes; ``LARGE_FILE_GB``, ``LARGE_FILE_KIND``
csv or md) goes to storage in resumable chunks and is read in parts, and no process holds the
file: the driver's peak memory stays under a fixed ceiling whatever the size, each sandbox child
under its own limit, every part is read and no passage is stored twice. Slow (tens of minutes):
runs only with ``RF_LARGE_FILE=1``; the measured numbers are printed.

GUARANTEE(test_a_multi_gb_document_is_read_in_parts_under_a_memory_ceiling)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import pytest

from tests.documents.world import admin

pytestmark = [
    pytest.mark.requires_postgres,
    pytest.mark.large_file,
    pytest.mark.skipif(os.environ.get("RF_LARGE_FILE") != "1", reason="set RF_LARGE_FILE=1"),
]
ROOT = Path(__file__).resolve().parents[2]
WORK = Path(os.environ.get("RF_LARGE_DIR", Path.home() / ".cache" / "abm-large-document-test"))
CEILING_MB = 700  # the driver: API-side work of a whole multi-GB file
LINE = "Budget review with Northwind Traders: renewal, pilot scope and next steps. " * 2


def generate(path: Path, target: int, kind: str) -> int:
    """Notes in sections (md) or contact rows (csv), written in 8 MiB blocks; the row count."""
    rows = 0
    with path.open("w", encoding="utf-8", newline="") as out:
        if kind == "csv":
            out.write("Name,Company,Email,Note\n")
        written, block = 0, []
        while written < target:
            rows += 1
            if kind == "csv":
                line = f'Person {rows},Northwind,p{rows}@northwind.example,"{LINE}"\n'
            else:
                line = (f"# Meeting {rows // 40}\n" if rows % 40 == 1 else "") + LINE + "\n"
            block.append(line)
            written += len(line)
            if len(block) >= 50_000:
                out.write("".join(block))
                block = []
        out.write("".join(block))
    return rows


def test_a_multi_gb_document_is_read_in_parts_under_a_memory_ceiling(tenant_a: UUID) -> None:
    kind = os.environ.get("LARGE_FILE_KIND", "md")
    target = int(float(os.environ.get("LARGE_FILE_GB", "2")) * 1024**3)
    WORK.mkdir(parents=True, exist_ok=True)
    path, blobs = WORK / f"large.{kind}", WORK / "blobs"
    try:
        rows = generate(path, target, kind)
        done = subprocess.run(
            [sys.executable, "-m", "tests.documents.large_driver", str(tenant_a), str(path),
             str(blobs)],
            cwd=ROOT, env=os.environ.copy(), capture_output=True, text=True, timeout=4 * 3600,
        )  # fmt: skip
        assert done.returncode == 0, done.stderr[-3000:]
        out = json.loads(done.stdout.strip().splitlines()[-1])
        print("\nlarge document:", json.dumps(out))
        assert out["status"] == "parsed" and out["parts_done"] == out["parts"] > 1
        assert out["peak_rss_mb"] < CEILING_MB, out
        assert out["child_peak_rss_mb"] < 1536, out
        if kind == "csv":
            assert out["passages"] == rows
        assert admin(
            "SELECT count(*) - count(DISTINCT seq) FROM document_passages WHERE document_id = %s",
            out["document_id"],
        ) == [(0,)]
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
