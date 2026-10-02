from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "migrations" / "0003_data_layer_v1.sql"
SOURCE_CHECKLIST = (
    ROOT / ".claude" / "skills" / "account-intelligence-platform" / "assets" / "checklist.yaml"
)


def test_data_layer_migration_adds_required_tenant_tables() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    required = {
        "source_code_counters",
        "sources",
        "fact_evidence",
        "coverage_rows",
        "people",
        "job_postings",
        "tech_detections",
        "raw_payloads",
        "tool_call_intents",
    }
    declared = set(re.findall(r"create\s+table\s+(?:if\s+not\s+exists\s+)?(\w+)", sql))
    secured = set(re.findall(r"--\s*lint:covers\s+([\w, ]+)", sql))
    secured_tables = {name.strip() for group in secured for name in group.split(",")}
    assert required <= declared | secured_tables


def test_migration_enforces_rls_and_composite_tenant_constraints() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    assert "foreach t in array array[" in sql
    assert "alter table %i enable row level security" in sql
    assert "alter table %i force row level security" in sql
    assert "create policy tenant_isolation on %i to app_rw" in sql
    assert re.search(r"using\s*\(tenant_id = \(select app_tenant\(\)\)\)", sql)
    assert re.search(r"with check\s*\(tenant_id = \(select app_tenant\(\)\)\)", sql)
    assert "foreign key (tenant_id, account_id)" in sql


def test_migration_protects_fact_evidence_and_terminal_coverage() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    assert "deferrable initially deferred" in sql
    assert "fact_requires_evidence" in sql
    assert "high priority coverage" in sql
    assert "blocked" in sql and "blocked_reason" in sql


def test_migration_uses_expand_only_ddl() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    assert not re.search(r"\bdrop\s+(table|column|constraint)\b", sql)
    assert not re.search(r"\balter\s+table\b[^;]*\bdrop\b", sql)


def test_application_checklist_matches_skill_appendix_asset() -> None:
    app = yaml.safe_load((ROOT / "config" / "checklist.yaml").read_text(encoding="utf-8"))
    source = yaml.safe_load(SOURCE_CHECKLIST.read_text(encoding="utf-8"))
    assert len(app["rows"]) == 33
    assert len(source["rows"]) == 33
    assert len(app["rows"]) == len(source["rows"])
    for index, (app_row, source_row) in enumerate(
        zip(app["rows"], source["rows"], strict=True), start=1
    ):
        expected_id = f"P{app_row['pass']}.{index:02d}" if app_row["pass"] == 1 else None
        if app_row["pass"] == 2:
            pass2_index = sum(row["pass"] == 2 for row in app["rows"][:index])
            expected_id = f"P2.{pass2_index:02d}"
        assert app_row["id"] == expected_id
        assert app_row["pass"] == source_row["pass"]
        assert app_row["row"] == source_row["label"]
        assert app_row["priority"].lower() == source_row["priority"]
