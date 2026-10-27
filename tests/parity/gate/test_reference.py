"""The gate on the reference workspace: runs only when its bundle and rendered files exist.

The workspace holds real data and is outside git; nothing here fixes it. The test asserts the
two findings the gate is known to make on it: the Summary's removed-postings count disagrees
with the computed entity count, and the pull log names the pursuing client before the scrub.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from parity.gate.__main__ import run_gate
from parity.gate.files import load_bundle, prefix_of, standing_names
from parity.gate.numbers import computed_numbers, run_numbers
from parity.gate.scrub import residue
from tests.parity.gate.conftest import HAVE_OFFICE

ROOT = Path(__file__).resolve().parents[3]
WORKSPACE = ROOT / "fixtures" / "workspaces" / "reference-ws"
BUNDLE = WORKSPACE / "bundle.json"


def _rendered_dir() -> Path | None:
    """The folder holding the rendered reference files: the first candidate with the whole
    standing set, else the first with the Account Summary."""
    if not BUNDLE.is_file():
        return None
    prefix = prefix_of(load_bundle(BUNDLE))
    candidates = (WORKSPACE / "out", ROOT / "out" / "parity_ref", ROOT / "out")
    for candidate in candidates:
        if all((candidate / name).is_file() for name in standing_names(prefix)):
            return candidate
    for candidate in candidates:
        if (candidate / f"{prefix}_Account_Summary.docx").is_file():
            return candidate
    return None


reference = pytest.mark.skipif(
    not BUNDLE.is_file() or _rendered_dir() is None,
    reason="reference bundle or rendered files missing",
)


@reference
def test_number_check_reports_the_known_inconsistency(tmp_path: Path) -> None:
    bundle = load_bundle(BUNDLE)
    known = computed_numbers(bundle)
    assert 569 in known and 553 not in known
    out = _rendered_dir()
    assert out is not None
    result = run_numbers(sorted(out.glob("*.docx")) + sorted(out.glob("*.xlsx")), bundle)
    assert any(f.message.startswith("553 near") and f.severity == "fail" for f in result.findings)


@reference
def test_pull_log_names_the_client_before_the_scrub() -> None:
    bundle = load_bundle(BUNDLE)
    assert bundle["meta"].get("pursuing_client_internal")
    log = (WORKSPACE / "notes" / "pull_log_phase1.md").read_text(encoding="utf-8")
    assert "client_name" in residue(log, bundle)


@reference
@pytest.mark.skipif(not HAVE_OFFICE, reason="LibreOffice or pdftotext missing")
def test_full_gate_runs_and_scrubs_the_zip(tmp_path: Path) -> None:
    import shutil

    bundle = load_bundle(BUNDLE)
    src = _rendered_dir()
    assert src is not None
    out = tmp_path / "out"
    shutil.copytree(src, out)
    report = run_gate(out, bundle, WORKSPACE)
    confidentiality = report.check("confidentiality")
    assert confidentiality is not None
    assert not any("remains in" in f.message for f in confidentiality.findings)
    numbers = report.check("number_consistency")
    assert numbers is not None and numbers.status == "FAIL"
