"""Golden accounts (P0-4): analyst-reviewed expected outcomes for whole runs.

A golden account is one YAML file under ``evals/golden/accounts/``: the account, who reviewed it
and when, the expected coverage status of rows the analyst checked, the attributes they confirmed
with the grade they would accept, and the senior people they expect (by profile URL). Synthetic
files are marked ``synthetic: true`` and never count toward a gate. ``score_run`` compares a run's
output (statuses, graded facts, people) with one golden account; the eval gates read the result.

    python -m evals.golden_accounts evals/golden/accounts
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

Status = Literal["done", "partial", "not_done", "blocked", "none"]
Grade = Literal["confirmed", "probable", "hypothesis", "lead", "internal"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExpectedAttribute(_Model):
    """An attribute the analyst verified, the value, and the weakest grade they accept."""

    attribute_key: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
    value: str
    min_grade: Grade
    source_url: HttpUrl


class ExpectedPerson(_Model):
    """A senior person the list must contain."""

    profile_url: HttpUrl
    title: str


class GoldenAccount(_Model):
    """One reviewed account."""

    account_domain: str
    reviewed_by: str
    reviewed_on: date
    synthetic: bool
    coverage: dict[str, Status] = Field(default_factory=dict)
    attributes: tuple[ExpectedAttribute, ...] = ()
    people: tuple[ExpectedPerson, ...] = ()

    @model_validator(mode="after")
    def _known_rows(self) -> GoldenAccount:
        from account_intelligence.tracker.config import load_checklist

        keys = {r.key for r in load_checklist().rows}
        unknown = set(self.coverage) - keys
        if unknown:
            raise ValueError(f"unknown coverage rows: {sorted(unknown)}")
        return self


GRADE_RANK = {"lead": 0, "hypothesis": 1, "internal": 1, "probable": 2, "confirmed": 3}


@dataclass(frozen=True)
class AccountScore:
    """How one run compares with its golden account (every ratio names its base)."""

    account: str
    synthetic: bool
    coverage_match: tuple[int, int]
    attributes_found: tuple[int, int]
    attributes_grade_ok: tuple[int, int]
    people_found: tuple[int, int]


def load_accounts(directory: Path) -> list[GoldenAccount]:
    """Every golden account file, validated."""
    return [
        GoldenAccount.model_validate(yaml.safe_load(p.read_text()))
        for p in sorted(directory.glob("*.yaml"))
    ]


def score_run(
    golden: GoldenAccount,
    statuses: Mapping[str, str],
    facts: Sequence[Mapping[str, str]],
    people_urls: Sequence[str],
) -> AccountScore:
    """Compare a run's statuses, graded facts (attribute_key, value, product_grade) and people."""
    cov = sum(statuses.get(k) == v for k, v in golden.coverage.items())
    found = grade_ok = 0
    for a in golden.attributes:
        match = [
            f
            for f in facts
            if f["attribute_key"] == a.attribute_key and f["value"].casefold() == a.value.casefold()
        ]
        if match:
            found += 1
            best = max(GRADE_RANK[f["product_grade"]] for f in match)
            grade_ok += best >= GRADE_RANK[a.min_grade]
    urls = {u.rstrip("/").lower() for u in people_urls}
    people = sum(str(p.profile_url).rstrip("/").lower() in urls for p in golden.people)
    return AccountScore(
        golden.account_domain,
        golden.synthetic,
        (cov, len(golden.coverage)),
        (found, len(golden.attributes)),
        (grade_ok, len(golden.attributes)),
        (people, len(golden.people)),
    )


def main(argv: list[str]) -> int:
    """Validate the golden set; report Blocked while only synthetic accounts exist."""
    accounts = load_accounts(Path(argv[0]) if argv else Path("evals/golden/accounts"))
    real = [a for a in accounts if not a.synthetic]
    print(f"{len(accounts)} golden accounts, {len(real)} analyst-reviewed")
    if len(real) < 3:
        print("Blocked: needs 3 to 5 analyst-reviewed golden accounts (P0-4)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
