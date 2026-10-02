from __future__ import annotations

import re
from pathlib import Path

import yaml

from src.db.errors import DataLayerError
from src.db.repositories.contracts import ChecklistRow

DEFAULT_CHECKLIST = Path(__file__).resolve().parents[3] / "config" / "checklist.yaml"

def load_checklist(path: Path = DEFAULT_CHECKLIST) -> tuple[str, list[ChecklistRow]]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DataLayerError(f"cannot load checklist config {path}: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("version"), str):
        raise DataLayerError("checklist config requires a version string")
    raw_rows = raw.get("rows")
    if not isinstance(raw_rows, list) or len(raw_rows) != 33:
        raise DataLayerError("checklist config must contain exactly 33 rows")
    rows: list[ChecklistRow] = []
    seen: set[str] = set()
    for item in raw_rows:
        if not isinstance(item, dict):
            raise DataLayerError("checklist rows must be mappings")
        row_id = item.get("id")
        pass_number = item.get("pass")
        label = item.get("row")
        priority = item.get("priority")
        if (
            not isinstance(row_id, str)
            or not re.fullmatch(r"P[12]\.\d{2}", row_id)
            or row_id in seen
        ):
            raise DataLayerError(f"invalid or duplicate checklist row id: {row_id!r}")
        if pass_number not in {1, 2} or not isinstance(label, str) or not label.strip():
            raise DataLayerError(f"invalid checklist row definition: {row_id}")
        if priority not in {"High", "Medium", "Optional"}:
            raise DataLayerError(f"invalid checklist priority for {row_id}")
        seen.add(row_id)
        rows.append(ChecklistRow(row_id, pass_number, label, priority))
    if sum(row.pass_number == 1 for row in rows) != 5:
        raise DataLayerError("checklist must contain five pass-1 rows")
    if sum(row.pass_number == 2 for row in rows) != 28:
        raise DataLayerError("checklist must contain twenty-eight pass-2 rows")
    return raw["version"], rows

def validate_coverage_proof(
    status: str,
    queries: list[str],
    tool_categories: list[str],
    result_count: int | None,
    blocked_reason: str | None = None,
) -> None:
    if status not in {"Done", "Partial", "Not done", "Blocked", "None"}:
        raise DataLayerError(f"invalid coverage status: {status}")
    if status == "Blocked":
        if not blocked_reason or not blocked_reason.strip():
            raise DataLayerError("Blocked coverage requires a reason")
        return
    if not queries or any(not query.strip() for query in queries):
        raise DataLayerError("coverage proof requires non-empty queries")
    if not tool_categories or any(not tool.strip() for tool in tool_categories):
        raise DataLayerError("coverage proof requires at least one tool category")
    if result_count is None or result_count < 0:
        raise DataLayerError("coverage proof requires a non-negative result count")
