"""Data warnings, the manifest, the whole gate and its command line."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from parity.gate.__main__ import main, run_gate
from parity.gate.manifest import write_manifest
from parity.gate.report import FAIL, PASS, SKIP, GateReport
from parity.gate.warnings import run_warnings
from tests.parity.gate.conftest import (
    HAVE_OFFICE,
    PREFIX,
    make_docx,
    make_workspace,
    write_outputs,
    write_workbooks,
)


def test_data_warnings(bundle: dict[str, Any], tmp_path: Path) -> None:
    bundle["people_rows"].append(
        dict(bundle["people_rows"][0], profile_url="https://profiles.example/in/other")
    )
    bundle["people_excluded"]["security"].append(bundle["people_excluded"]["security"][0])
    files = write_workbooks(tmp_path, bundle)
    messages = sorted(w.message for w in run_warnings(files, bundle))
    assert messages == [
        "duplicate names in the security exclusion list: 1",
        "duplicate names with different profile URLs: 1",
        "empty columns on sheet People: Second profile",
        "postings without a requisition ID: 1",
    ]
    assert all(w.severity == "warn" for w in run_warnings(files, bundle))


def test_manifest_lists_every_file_with_hash_and_mime(
    bundle: dict[str, Any], tmp_path: Path
) -> None:
    write_outputs(tmp_path, bundle)
    make_docx(tmp_path / f"{PREFIX}_Internal_Only.docx", ["internal"])
    data = write_manifest(tmp_path, "1.0.0", {"x.xlsx": {"people.senior": 4}})
    stored = json.loads((tmp_path / "manifest.json").read_text())
    assert stored == data
    assert data["bundle_version"] == "1.0.0"
    assert data["summary"] == {"x.xlsx": {"people.senior": 4}}
    names = [f["name"] for f in data["files"]]
    assert "manifest.json" not in names and len(names) == 6
    summary = next(f for f in data["files"] if f["name"].endswith("_Account_Summary.docx"))
    assert len(summary["sha256"]) == 64 and summary["size"] > 0 and summary["client"] is True
    assert summary["mime"].endswith("wordprocessingml.document")
    internal = next(f for f in data["files"] if "Internal_Only" in f["name"])
    assert internal["client"] is False


def _report_statuses(report: GateReport) -> dict[str, str]:
    return {c.name: c.status for c in report.checks}


def test_run_gate_on_a_clean_set(bundle: dict[str, Any], tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    write_outputs(out, bundle)
    ws = make_workspace(tmp_path / "ws")
    report = run_gate(out, bundle, ws)
    statuses = _report_statuses(report)
    expected = PASS if HAVE_OFFICE else SKIP
    assert statuses == {
        "third_party_scan": PASS,
        "doc_checks": PASS,
        "rowcheck": expected,
        "number_consistency": PASS,
        "workbook": expected,
        "confidentiality": PASS,
    }
    assert report.passed
    assert (out / f"{PREFIX}_workspace.zip").is_file() and (out / "manifest.json").is_file()
    assert report.manifest is not None and len(report.manifest["files"]) == 6
    assert any(w.message.startswith("left out of the zip") for w in report.warnings)
    as_json = json.loads(report.to_json())
    assert as_json["passed"] is True and "GATE PASS" in report.text()


def test_cli_exit_codes(
    bundle: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    write_outputs(out, bundle)
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(bundle))
    assert main([str(out), str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True
    bundle["prose"]["narrative"]["wrong"] = ["We excluded 553 postings by wording."]
    path.write_text(json.dumps(bundle))
    assert main([str(out), str(path)]) == 1
    text = capsys.readouterr().out
    assert "FAIL number_consistency" in text and text.rstrip().endswith("GATE FAIL")


def test_the_synthetic_bundle_validates() -> None:
    pytest.importorskip("parity.bundle")
    from parity.bundle import validate_bundle
    from tests.parity.gate.conftest import make_bundle

    validate_bundle(make_bundle())


def test_report_fail_when_any_check_fails() -> None:
    from parity.gate.report import CheckResult

    report = GateReport([CheckResult("a", PASS), CheckResult("b", FAIL)])
    assert not report.passed
    assert report.check("b") is not None and report.check("zz") is None
