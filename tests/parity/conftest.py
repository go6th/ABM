"""Shared fixtures of the parity tests: the synthetic bundle (invented account, names and
links only) and the reference workspace, which exists only on a machine that holds it (the
folder is ignored by git); tests that need it are skipped elsewhere."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from parity.bundle import AccountBundle, validate_bundle

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SYNTHETIC = FIXTURES / "synthetic_bundle.json"
REFERENCE_WS = ROOT / "fixtures" / "workspaces" / "reference-ws"
reference = pytest.mark.skipif(
    not (REFERENCE_WS / "build" / "prose_extra.json").exists(),
    reason="the reference workspace is not present on this machine",
)


@pytest.fixture(scope="session")
def synthetic_doc() -> dict[str, Any]:
    """The synthetic bundle as a JSON document."""
    with open(SYNTHETIC, encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


@pytest.fixture(scope="session")
def synthetic_bundle(synthetic_doc: dict[str, Any]) -> AccountBundle:
    """The synthetic bundle, validated."""
    return validate_bundle(synthetic_doc)
