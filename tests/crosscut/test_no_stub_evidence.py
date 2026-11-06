"""The retired no-op stubs (ADR-0122): ``security``, ``metrics``, ``registers`` and ``unitcost``
returned constants (``beta_gate() -> True``, ``coverage() -> 1.0``, ``overdue() -> []``,
``scan() -> []``) and the section 11 report counted them as proof. These checks keep them gone and
keep the report honest: a requirement nothing checks reads Missing, never Done."""

from __future__ import annotations

import tomllib
from pathlib import Path

from crosscut import conformance

ROOT = Path(__file__).resolve().parents[2]
RETIRED = ("security", "metrics", "registers", "unitcost")
EMPTY = conformance.Catalog(frozenset(), frozenset(), frozenset())


def test_the_retired_stub_packages_are_gone() -> None:
    py = tomllib.loads((ROOT / "pyproject.toml").read_text())
    packages = py["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    source = py["tool"]["coverage"]["run"]["source_pkgs"]
    roots = py["tool"]["importlinter"]["root_packages"]
    for name in RETIRED:
        assert not (ROOT / "src" / name).exists(), name
        assert f"src/{name}" not in packages, name
        assert name not in source and name not in roots, name


def test_the_section11_spec_cites_no_retired_stub() -> None:
    spec = conformance.load_spec()
    cited = [
        (rid, ev["symbol"])
        for rid, req in spec["requirements"].items()
        for ev in req["evidence"]
        if "symbol" in ev and ev["symbol"].split(".", 1)[0] in RETIRED
    ]
    assert cited == []


def test_a_requirement_with_no_evidence_is_missing_not_done() -> None:
    spec = {"requirements": {"X": {"text": "x", "evidence": []}}}
    (r,) = conformance.evaluate(spec, EMPTY, tests=frozenset())
    assert r.status == "Missing"


def test_rotation_soc2_and_unit_costs_no_longer_report_done() -> None:
    got = {r.id: r.status for r in conformance.evaluate(conformance.load_spec(), EMPTY)}
    # config files exist, nothing executes them: Partial at best, never Done
    assert got["SC.rotation_schedule"] == "Partial"
    assert got["SC.soc2_pentest"] == "Partial"
    for rid in ("UC.broker_credits", "UC.infrastructure", "UC.dashboard", "UC.anomaly_alert"):
        assert got[rid] == "Missing", rid
    assert got["MX.report"] == "Missing"
    for rid in ("RR.risks", "RR.decisions", "CL.claims", "CL.messaging"):
        assert got[rid] != "Done", rid
