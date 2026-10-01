"""Load golden accounts and check them against the pinned checklist."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from app.golden.schema import GoldenAccount
from app.rules.loader import RuleBundle, default_rules

DEFAULT_GOLDEN_DIR = Path(__file__).resolve().parents[2] / "tests" / "evals" / "golden"
SCHEMA_FILENAME = "golden_account.schema.json"


class GoldenError(ValueError):
    """A golden account file is invalid or inconsistent with the checklist."""


def golden_json_schema() -> dict[str, object]:
    return GoldenAccount.model_json_schema()


def _check_against_rules(golden: GoldenAccount, rules: RuleBundle) -> None:
    known_rows = {r.id for r in rules.checklist.rows}
    used = {c.row_id for c in golden.expected_coverage}
    used |= {row for s in golden.sources for row in s.row_ids}
    unknown = sorted(used - known_rows)
    if unknown:
        raise GoldenError(f"{golden.golden_id}: unknown coverage rows {unknown}")
    missing_high = sorted(
        rules.checklist.high_priority_ids - {c.row_id for c in golden.expected_coverage}
    )
    if missing_high:
        raise GoldenError(
            f"{golden.golden_id}: High rows without an expected status {missing_high}"
        )


def load_golden_file(path: Path, rules: RuleBundle | None = None) -> GoldenAccount:
    try:
        golden = GoldenAccount.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise GoldenError(f"{path.name}: {exc}") from exc
    _check_against_rules(golden, rules or default_rules())
    return golden


def load_golden_accounts(
    golden_dir: Path | str = DEFAULT_GOLDEN_DIR,
    *,
    allow_synthetic: bool = True,
    rules: RuleBundle | None = None,
) -> list[GoldenAccount]:
    """Load every golden account in the directory, sorted by id.

    Pass ``allow_synthetic=False`` wherever results count as evidence (parity reports,
    decision log); synthetic placeholders then raise instead of silently passing.
    """
    root = Path(golden_dir)
    files = sorted(p for p in root.glob("*.json") if p.name != SCHEMA_FILENAME)
    accounts = [load_golden_file(p, rules) for p in files]
    ids = [g.golden_id for g in accounts]
    if len(ids) != len(set(ids)):
        raise GoldenError("golden ids must be unique")
    if not allow_synthetic:
        synthetic = [g.golden_id for g in accounts if g.synthetic]
        if synthetic:
            raise GoldenError(f"synthetic golden accounts are not evidence: {synthetic}")
    return sorted(accounts, key=lambda g: g.golden_id)
