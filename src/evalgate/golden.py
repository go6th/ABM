"""Golden labels (E2): every label has an attribute, an expected value and grade, a capture date,
a source, an opaque analyst id and a split (``dev`` or ``holdout``). The loader refuses a label
without any of them; labels older than ``label_max_age_days`` are excluded and counted (quarterly
re-verification); a file marked ``synthetic: true`` never counts toward a gate.

File format (``evals/golden/labels/<account>.yaml``)::

    account: acme.example
    synthetic: true
    labels:
      - attribute_key: jobs.salesforce_admin.open
        value: Senior Salesforce Administrator
        grade: confirmed
        captured_on: 2026-10-01
        source_url: https://careers.acme.example/jobs/42
        analyst: a-7f3c       # opaque id, never a name
        split: dev
    expected_attributes: [jobs.salesforce_admin.open]
    high_rows: {job_postings_12_months: done}

GUARANTEE(test_golden_loader_refuses_incomplete_labels_and_counts_stale_ones): E2.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

ANALYST = re.compile(r"^[a-z]-[0-9a-f]{4,16}$")


class GoldenInvalid(ValueError):
    """A golden file breaks the format."""


class Label(BaseModel):
    """One analyst label."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    attribute_key: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
    value: str = Field(min_length=1, max_length=500)
    grade: Literal["confirmed", "probable", "hypothesis", "lead"]
    captured_on: date
    source_url: HttpUrl
    analyst: str
    split: Literal["dev", "holdout"]


class GoldenAccount(BaseModel):
    """One account's labels and coverage expectations."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    account: str
    synthetic: bool
    labels: tuple[Label, ...]
    expected_attributes: tuple[str, ...] = ()
    high_rows: dict[str, Literal["done", "partial", "not_done", "blocked", "none"]] = {}


@dataclass(frozen=True)
class GoldenSet:
    """Loaded accounts, their fresh labels, and what was excluded."""

    accounts: tuple[GoldenAccount, ...]
    stale_excluded: int
    version: str

    def labels(self, split: str | None = None) -> list[tuple[str, Label]]:
        """``(account, label)`` pairs, optionally of one split."""
        return [
            (a.account, lb)
            for a in self.accounts
            for lb in a.labels
            if split is None or lb.split == split
        ]


def load(paths: Sequence[Path], today: date, max_age_days: int) -> GoldenSet:
    """Load and validate; stale labels are dropped and counted."""
    accounts: list[GoldenAccount] = []
    stale = 0
    digest = hashlib.sha256()
    for path in sorted(paths):
        raw = path.read_bytes()
        digest.update(path.name.encode() + b"\0" + raw)
        try:
            account = GoldenAccount.model_validate(yaml.safe_load(raw))
        except Exception as exc:
            raise GoldenInvalid(f"{path.name}: {exc}") from exc
        for label in account.labels:
            if not ANALYST.match(label.analyst):
                raise GoldenInvalid(f"{path.name}: analyst must be an opaque id")
        fresh = tuple(
            lb for lb in account.labels if today - lb.captured_on <= timedelta(days=max_age_days)
        )
        stale += len(account.labels) - len(fresh)
        accounts.append(account.model_copy(update={"labels": fresh}))
    return GoldenSet(tuple(accounts), stale, digest.hexdigest())
