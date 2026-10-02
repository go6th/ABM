"""Vocabulary for the v1 data layer.

Postgres stores these as CHECK-constrained text (decision recorded in
PLAN.md: the starter schema uses CHECK text everywhere, and CHECK text avoids
ALTER TYPE churn as vocabularies grow). Python reads and writes plain str
values; the value lists below are the single source of truth and are tested
against the YAML config for drift (tests/unit/test_enums.py).
"""

from __future__ import annotations

import re
from enum import StrEnum


class Grade(StrEnum):
    """Product-language grades (PRD Appendix B). `internal` arrives in Phase B."""

    CONFIRMED = "confirmed"
    PROBABLE = "probable"
    HYPOTHESIS = "hypothesis"
    LEAD = "lead"


class CoverageStatus(StrEnum):
    """Coverage tracker endings (PRD 3.4). Stored lowercase with underscores."""

    DONE = "done"
    PARTIAL = "partial"
    NOT_DONE = "not_done"
    BLOCKED = "blocked"
    NONE = "none"


class Priority(StrEnum):
    """Checklist row priority (PRD Appendix A)."""

    HIGH = "high"
    MEDIUM = "medium"
    OPTIONAL = "optional"


class RunType(StrEnum):
    """Full account run or refresh of a previous run."""

    FULL = "full"
    REFRESH = "refresh"


class RunStatus(StrEnum):
    """v1 research-run lifecycle. Terminal states have no exits."""

    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    FAILED = "failed"
    COMPLETED = "completed"


class JobClass(StrEnum):
    """Posting classification (PRD 3.3/3.4)."""

    BUILD_ROLE = "build_role"
    ADMIN_OWNER_ROLE = "admin_owner_role"
    WORKING_TOOL = "working_tool"
    USER_ROLE = "user_role"


class JobState(StrEnum):
    """Posting lifecycle state."""

    OPEN = "open"
    CLOSED = "closed"


class PersonLevel(StrEnum):
    """Seniority levels in scope (config/people_scope.yaml)."""

    C_LEVEL = "c_level"
    EVP = "evp"
    SVP = "svp"
    VP = "vp"
    HEAD = "head"
    DIRECTOR = "director"
    SENIOR_MANAGER = "senior_manager"


class Lane(StrEnum):
    """Platform lanes (config/lanes.yaml)."""

    SALESFORCE = "salesforce"
    SERVICENOW = "servicenow"
    SAP = "sap"
    TABLEAU_ANALYTICS = "tableau_analytics"
    VEEVA = "veeva"
    DATA_AI = "data_ai"
    OTHER = "other"


class SourceKind(StrEnum):
    """Where a source sits in the evidence hierarchy (PRD grading scheme)."""

    PRIMARY = "primary"
    SECONDARY = "secondary"
    AGGREGATOR = "aggregator"


_ENUMS: tuple[type[StrEnum], ...] = (
    Grade,
    CoverageStatus,
    Priority,
    RunType,
    RunStatus,
    JobClass,
    JobState,
    PersonLevel,
    Lane,
    SourceKind,
)


def values[E: StrEnum](enum_cls: type[E]) -> tuple[str, ...]:
    """Stored (lowercase) values of an enum, in declaration order."""
    return tuple(member.value for member in enum_cls)


def sql_in_values[E: StrEnum](enum_cls: type[E]) -> str:
    """SQL fragment for CHECK constraints, e.g. ('confirmed', 'probable', ...)."""
    return "(" + ", ".join(f"'{value}'" for value in values(enum_cls)) + ")"


def normalize_key(raw: str) -> str:
    """Normalize a config label to the stored key form: lowercase, snake_case."""
    return re.sub(r"[^a-z0-9]+", "_", raw.strip().lower()).strip("_")


def all_enums() -> tuple[type[StrEnum], ...]:
    """Every vocabulary in one place, for drift tests and the data dictionary."""
    return _ENUMS
