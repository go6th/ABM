"""``python -m parity.gate <out_dir> <bundle.json> [--workspace <dir>] [--stale ...] [--json]``

Runs every check on the rendered files in ``out_dir``, builds the workspace zip when a
workspace folder is given, writes ``manifest.json`` and prints the report. Exit 1 on any
failure.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from parity.gate.confidentiality import run_confidentiality
from parity.gate.doc_checks import run_doc_checks
from parity.gate.files import Bundle, Office, client_files, load_bundle, office_files
from parity.gate.manifest import write_manifest
from parity.gate.numbers import run_numbers
from parity.gate.report import Finding, GateReport
from parity.gate.rowcheck import run_rowcheck
from parity.gate.scan import run_scan
from parity.gate.warnings import run_warnings
from parity.gate.workbook import run_workbook_checks
from parity.gate.zipper import build_workspace_zip


def _office() -> Office | None:
    try:
        from exports.verify import LibreOffice
    except ImportError:
        return None
    office = LibreOffice(timeout_s=600.0)
    return office if office.binary else None


def run_gate(
    out_dir: Path,
    bundle: Bundle,
    workspace: Path | None = None,
    stale: Sequence[str] = (),
    office: Office | None = None,
) -> GateReport:
    """Every check in order; the zip and the manifest are written into ``out_dir``."""
    office = _office() if office is None else office
    report = GateReport()
    files = client_files(out_dir)
    report.checks.append(run_scan(office_files(out_dir)))
    report.checks.append(run_doc_checks(files, bundle, office, stale))
    report.checks.append(run_rowcheck(files, office))
    report.checks.append(run_numbers(files, bundle))
    workbook_result, summary = run_workbook_checks(files, bundle, office)
    report.checks.append(workbook_result)
    zip_path = None
    if workspace is not None:
        zip_result = build_workspace_zip(bundle, workspace, out_dir)
        zip_path = zip_result.path
        if zip_result.refused:
            report.warnings.append(
                Finding(
                    "zip",
                    f"left out of the zip (refused names): {len(zip_result.refused)}",
                    zip_path.name,
                    "warn",
                )
            )
    report.checks.append(run_confidentiality(out_dir, bundle, zip_path))
    report.warnings += run_warnings(files, bundle)
    report.manifest = write_manifest(out_dir, str(bundle.get("schema_version") or ""), summary)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """The command line entry point."""
    parser = argparse.ArgumentParser(prog="python -m parity.gate", description=__doc__)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--stale", nargs="*", default=[])
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    bundle = load_bundle(args.bundle)
    report = run_gate(args.out_dir, bundle, args.workspace, tuple(args.stale))
    print(report.to_json() if args.json else report.text())
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
