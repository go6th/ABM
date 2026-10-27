"""A bundle from a run's stored content (``parity.from_run``) on the synthetic export corpus:
it validates, every number in ``computed`` comes from its rows, the pursuing client's name is
in ``meta.pursuing_client_internal`` and nowhere else, corrections recompute the numbers, and
with the tools present the rendered files pass the gate."""

from __future__ import annotations

import json
import shutil
from typing import Any
from uuid import uuid4

import pytest

from parity import counts
from parity.bundle import validate_bundle
from parity.from_run import (
    RunFacts,
    apply_overrides,
    build_bundle,
    code_id,
    short_name,
)
from parity.recalc import soffice_binary
from parity.render import DOCX_CLI
from tests.exports.corpus import rich, sparse

tools = pytest.mark.skipif(
    soffice_binary() is None or shutil.which("node") is None or not DOCX_CLI.exists(),
    reason="LibreOffice, Node or the Word renderer is missing",
)


@pytest.fixture(scope="module")
def run_bundle() -> dict[str, Any]:
    return build_bundle(
        rich(),
        RunFacts(
            pursuing_client="Pursuing Client Inc",
            internal_items=(("Contract (Confirmed)", "A signthetic note [Deck, p. 2]"),),
        ),
    )


def test_codes_and_names() -> None:
    assert code_id("S-0007") == "S7" and code_id("J-0012") == "J12"
    assert short_name("Acme Widgets Ltd") == "Acme Widgets Ltd"
    assert short_name("***", "acme.example") == "acme"
    assert short_name("A/B Co (plc)") == "A B Co plc"


def test_the_bundle_validates_and_counts_its_rows(run_bundle: dict[str, Any]) -> None:
    validate_bundle(run_bundle)
    c = run_bundle["computed"]
    assert c["people_total"] == len(run_bundle["people_rows"]) == 30
    assert c["senior"] == sum(1 for p in run_bundle["people_rows"] if p["senior"])
    assert sum(c["ent_counts"].values()) == len(run_bundle["jobs_rows"]) == 40
    jobs = counts.jobs_summary(run_bundle)
    for lane, jc in jobs.items():
        assert c["jobs_counts"][lane]["total"] == jc.total
        assert c["jobs_counts"][lane]["open"] == jc.open
    assert set(run_bundle["sources"]) and all(k.startswith("S") for k in run_bundle["sources"])
    assert all(k.startswith("J") for k in run_bundle["jobs_read"])
    assert run_bundle["meta"]["lanes"][0] == "Salesforce"
    assert all(
        r["cls"] in ("Build role", "Admin and owner role", "Working tool", "User role")
        for r in run_bundle["jobs_rows"]
    )
    assert run_bundle["prose"]["workbook_notes"]["jobs"]


def test_the_pursuing_client_is_internal_only(run_bundle: dict[str, Any]) -> None:
    assert run_bundle["meta"]["pursuing_client_internal"] == "Pursuing Client Inc"
    rest = dict(run_bundle)
    rest["meta"] = {k: v for k, v in run_bundle["meta"].items() if k != "pursuing_client_internal"}
    assert "Pursuing Client" not in json.dumps(rest)


def test_a_sparse_run_still_builds() -> None:
    bundle = build_bundle(sparse())
    assert bundle["computed"]["people_total"] == 0
    assert bundle["jobs_rows"] == [] and bundle["ranking"]["rows"] == []
    assert "pursuing_client_internal" not in bundle["meta"]


def test_overrides_change_the_rows_and_the_numbers(run_bundle: dict[str, Any]) -> None:
    senior = next(p for p in run_bundle["people_rows"] if p["senior"])
    before = run_bundle["computed"]["prio"].get("C", 0)
    edited = apply_overrides(
        run_bundle, [{"name": senior["name"], "priority": "C", "note": "checked"}]
    )
    row = next(p for p in edited["people_rows"] if p["name"] == senior["name"])
    assert row["priority"] == "C" and row["note"] == "checked"
    assert edited["overrides"][-1] == {"name": senior["name"], "priority": "C", "note": "checked"}
    assert edited["computed"]["prio"].get("C", 0) >= before
    with pytest.raises(KeyError):
        apply_overrides(run_bundle, [{"name": "Nobody Here", "priority": "A"}])


@tools
def test_the_rendered_run_bundle_passes_the_gate(run_bundle: dict[str, Any]) -> None:
    from parity.service import render_files

    result = render_files(run_bundle, uuid4())
    assert result.passed, json.dumps(result.gate, indent=1)[:2000]
    names = set(result.files)
    prefix = run_bundle["meta"]["account_short"]
    assert {
        f"{prefix}_People_List.xlsx",
        f"{prefix}_Account_Summary.docx",
        "manifest.json",
        "bundle.json",
    } <= names
    assert f"{prefix}_Internal_Only.docx" in names  # the facts carried internal notes
    assert any(n.endswith("_workspace.zip") for n in names)
    listed = {f["name"]: f for f in result.manifest["files"]}
    assert listed[f"{prefix}_Internal_Only.docx"]["client"] is False
    assert result.manifest["summary"]
