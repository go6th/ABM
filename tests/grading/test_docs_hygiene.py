"""Generated docs are current; no vendor name in the grading layer; fixtures are synthetic."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

from account_intelligence.connectors.registry_internal import vendor_names
from account_intelligence.connectors.settings import load_providers_config
from account_intelligence.grading import docs
from account_intelligence.grading.schema import GradedClaim

REPO = Path(__file__).resolve().parents[2]


def test_generated_docs_are_current() -> None:
    assert docs.main(["--check"]) == 0, "run: python -m account_intelligence.grading.docs"


def test_fixtures_validate_against_the_exported_schema() -> None:
    schema = json.loads((REPO / "docs" / "grading" / "schema.json").read_text())
    assert schema["GradedClaim"] == GradedClaim.model_json_schema()
    from tests.grading.db_world import graded_confirmed, graded_lead

    for claim in (graded_confirmed(), graded_lead()):
        assert GradedClaim.model_validate_json(claim.model_dump_json()) == claim


def test_no_vendor_name_in_the_grading_layer_or_its_config() -> None:
    cfg = load_providers_config()
    names = [n.lower() for n in vendor_names(cfg).values() if n != "Parallel"]
    names += [
        ".".join((urlsplit(p.base_url).hostname or "").split(".")[-2:])
        for p in cfg.providers.values()
    ]
    files = list((REPO / "src" / "account_intelligence" / "grading").rglob("*.py")) + [
        REPO / "config" / "grading_policy.yaml",
        REPO / "config" / "source_registry.yaml",
        REPO / "docs" / "grading" / "rules.md",
    ]
    offenders = [
        str(f.relative_to(REPO)) for f in files if any(n in f.read_text().lower() for n in names)
    ]
    assert offenders == []


def test_grading_fixtures_are_labelled_synthetic() -> None:
    for name in ("world.py", "db_world.py"):
        assert (
            "synthetic" in (REPO / "tests" / "grading" / name).read_text().split('"""')[1].lower()
        )
    assert "synthetic" in (REPO / "tests" / "fakes" / "grading_fakes.py").read_text().lower()
