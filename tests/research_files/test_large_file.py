"""A generated CSV above 1 GB: upload in 8 MiB parts and process it in a subprocess, and compare
its peak memory with a 20 MB file's. Memory must stay flat (no part of the pipeline holds the
file). Slow (minutes): runs only with ``RF_LARGE_FILE=1``; the measured numbers are printed.

GUARANTEE(test_a_large_file_keeps_memory_flat)
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

pytestmark = [
    pytest.mark.requires_postgres,
    pytest.mark.skipif(os.environ.get("RF_LARGE_FILE") != "1", reason="set RF_LARGE_FILE=1"),
]
ROOT = Path(__file__).resolve().parents[2]
WORK = Path(os.environ.get("RF_LARGE_DIR", Path.home() / ".cache" / "abm-large-file-test"))
NOTE = ("lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor " * 8)[:600]


def generate(path: Path, target_bytes: int) -> None:
    """id, amount, category, day, note: about 700 bytes per row, written in 8 MiB blocks."""
    with path.open("w", encoding="utf-8", newline="") as out:
        out.write("id,amount,category,day,note\n")
        written, i, block = 0, 0, []
        while written < target_bytes:
            i += 1
            day = f"2024-{1 + i % 12:02d}-{1 + i % 28:02d}"
            line = f"{i},{(i * 37) % 10007 / 10:.1f},cat{i % 23},{day},{NOTE}\n"
            block.append(line)
            written += len(line)
            if len(block) >= 12000:
                out.write("".join(block))
                block = []
        out.write("".join(block))


def drive(tenant: UUID, path: Path, root: Path) -> dict[str, object]:
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.research_files.large_driver",
            str(tenant),
            str(path),
            str(root),
        ],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=3600,
    )
    assert done.returncode == 0, done.stderr[-3000:]
    return dict(json.loads(done.stdout.strip().splitlines()[-1]))


def test_a_large_file_keeps_memory_flat(tenant_a: UUID, tenant_b: UUID) -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    small, large = WORK / "small.csv", WORK / "large.csv"
    blobs = WORK / "blobs"
    try:
        generate(small, 20 * 1024 * 1024)
        generate(large, 1100 * 1024 * 1024)
        assert large.stat().st_size > 1024**3
        base = drive(tenant_a, small, blobs)
        big = drive(tenant_b, large, blobs)
        print("\nsmall:", json.dumps(base), "\nlarge:", json.dumps(big))
        assert big["status"] == "ANALYZED" and big["dedupe_same_file"]
        assert int(str(big["rows"])) > 1_500_000
        assert big["model_spend"] == {}  # a table needs no model: $0
        growth = float(str(big["peak_rss_mb"])) - float(str(base["peak_rss_mb"]))
        assert growth < 150, f"peak memory grew {growth} MB for a 55x larger file"
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
