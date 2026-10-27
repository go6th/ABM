"""Render every deliverable file from a bundle.

    python -m parity.render <bundle.json> <out_dir> [--no-recalc]

The three workbooks are written with openpyxl, re-saved through LibreOffice (``parity.recalc``)
so their formulas carry cached values, and checked for error values. When
``render/docx/dist/cli.js`` exists the Word renderer is started on the same bundle and output
folder (``node render/docx/dist/cli.js <bundle.json> <out_dir>``) without waiting for it.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess  # nosec B404 - fixed argv, no shell
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from parity.bundle import AccountBundle, load_bundle
from parity.excel.jobs import render_jobs
from parity.excel.people import render_people
from parity.excel.stack import render_stack
from parity.recalc import check_workbook, recalculate

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCX_CLI = REPO_ROOT / "render" / "docx" / "dist" / "cli.js"


def workbook_names(bundle: AccountBundle) -> dict[str, str]:
    """The three workbook file names of an account."""
    prefix = bundle.meta.account_short
    return {
        "people": f"{prefix}_People_List.xlsx",
        "jobs": f"{prefix}_Job_Postings.xlsx",
        "stack": f"{prefix}_Tech_Stack_and_Teams.xlsx",
    }


def render_workbooks(bundle: AccountBundle, out_dir: Path, recalc: bool = True) -> list[Path]:
    """Write the three workbooks into ``out_dir`` (recalculated unless ``recalc`` is False)
    and return their paths; raises on an error value or a lost formula."""
    out_dir.mkdir(parents=True, exist_ok=True)
    names = workbook_names(bundle)
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "raw"
        raw.mkdir()
        sources = [
            render_people(bundle, raw / names["people"]),
            render_jobs(bundle, raw / names["jobs"]),
            render_stack(bundle, raw / names["stack"]),
        ]
        if not recalc:
            return [Path(shutil.copy2(src, out_dir / src.name)) for src in sources]
        written = recalculate(sources, out_dir)
        problems = [
            p for src, out in zip(sources, written, strict=True) for p in check_workbook(out, src)
        ]
    if problems:
        raise RuntimeError("workbook check failed: " + "; ".join(problems[:10]))
    return written


def start_docx(bundle_path: Path, out_dir: Path) -> subprocess.Popen[bytes] | None:
    """Start the Word renderer when it is built; None when it is not."""
    node = shutil.which("node")
    if node is None or not DOCX_CLI.exists():
        return None
    return subprocess.Popen(  # nosec B603 - fixed argv, no shell
        [node, str(DOCX_CLI), str(bundle_path), str(out_dir)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry."""
    ap = argparse.ArgumentParser(description="Render the deliverable files of a bundle.")
    ap.add_argument("bundle", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--no-recalc", action="store_true", help="skip the LibreOffice re-save")
    args = ap.parse_args(argv)
    bundle = load_bundle(args.bundle)
    written = render_workbooks(bundle, args.out_dir, recalc=not args.no_recalc)
    for path in written:
        print(path, file=sys.stderr)
    if start_docx(args.bundle, args.out_dir) is not None:
        print("Word renderer started", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
