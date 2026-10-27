"""Replay: build the bundle of the reference workspace, render the three workbooks and run the
owner's checker (``scripts/parity/parity_check.py``) in process; skipped unless the workspace
and LibreOffice are present."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from parity.bundle import validate_bundle
from parity.recalc import soffice_binary
from parity.render import render_workbooks, workbook_names
from parity.workspace import build_bundle
from tests.parity.conftest import REFERENCE_WS, ROOT, reference

CHECKER = ROOT / "scripts" / "parity" / "parity_check.py"


def load_checker() -> ModuleType:
    """The owner's checker as a module."""
    spec = importlib.util.spec_from_file_location("parity_check", CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@reference
def test_reference_workspace_replays_to_pass(tmp_path: Path) -> None:
    if soffice_binary() is None:
        pytest.skip("LibreOffice is not installed")
    doc = build_bundle(REFERENCE_WS)
    bundle = validate_bundle(doc)
    assert bundle.ranking.base == 1904
    assert bundle.computed.lane_rows_all == 1897 and bundle.computed.lane_rows_account == 1659
    assert (bundle.computed.senior, bundle.computed.prio.A) == (424, 80)
    assert bundle.meta.pursuing_client_internal
    assert bundle.meta.pursuing_client_internal not in json.dumps(
        {k: v for k, v in doc["meta"].items() if k != "pursuing_client_internal"}
    )
    assert bundle.meta.pursuing_client_internal not in json.dumps(doc["prose"])
    render_workbooks(bundle, tmp_path, recalc=True)
    checker = load_checker()
    golden = REFERENCE_WS / "deliverables"
    for name in workbook_names(bundle).values():
        report = checker.Report(name, 15)
        checker.cmp_xlsx(str(golden / name), str(tmp_path / name), report, False)
        assert report.items == [], f"{name}: {report.items[:5]}"
