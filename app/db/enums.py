"""Enumerated values stored by the data layer.

Storage choice: **text columns with a named CHECK constraint**, not native Postgres ENUM types.
Reasons are in docs/data_layer/PLAN.md section 9. In short: lanes and levels come from config and
will change; a CHECK constraint is replaced inside one transactional migration, while an ENUM
value can never be removed or renamed without rebuilding the type and every column using it.

Stored values are lowercase snake case. Config files use display labels ("Not done"); each enum
maps between the two with ``label`` and ``from_label``, and tests fail if the two drift apart.

``PersonLevel`` and ``Lane`` are built from config (``people_scope.yaml`` and ``lanes.yaml``), so
their members are not known to the type checker; refer to them by value, for example
``Lane("salesforce")``.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Self

import sqlalchemy as sa

from app.rules import default_rules

# Room to add longer values later without altering the column type.
ENUM_COLUMN_LENGTH = 64


def slug(label: str) -> str:
    """'Not done' -> 'not_done', 'C-level' -> 'c_level'."""
    return re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")


class LabelledEnum(StrEnum):
    """A StrEnum whose stored value is a slug of a display label."""

    @property
    def label(self) -> str:
        labels: dict[str, str] = getattr(type(self), "_labels", {})
        return labels.get(self.value, self.value.replace("_", " ").capitalize())

    @classmethod
    def from_label(cls, label: str) -> Self:
        """Exact label first (labels need not be slugs of values), then the slug of the label."""
        labels: dict[str, str] = getattr(cls, "_labels", {})
        for value, known in labels.items():
            if known == label:
                return cls(value)
        return cls(slug(label))

    @classmethod
    def db_name(cls) -> str:
        """Name used for the CHECK constraint: ck_<table>_<db_name>."""
        return slug(re.sub(r"(?<!^)(?=[A-Z])", "_", cls.__name__))


def _with_labels[E: type[LabelledEnum]](cls: E, labels: dict[str, str]) -> E:
    missing = set(labels) ^ {m.value for m in cls}
    if missing:
        raise ValueError(f"{cls.__name__}: labels and members differ: {sorted(missing)}")
    cls._labels = labels  # type: ignore[attr-defined]
    return cls


# --------------------------------------------------------------------------- static enums


class Grade(LabelledEnum):
    CONFIRMED = "confirmed"
    PROBABLE = "probable"
    HYPOTHESIS = "hypothesis"
    LEAD = "lead"


class CoverageStatus(LabelledEnum):
    DONE = "done"
    PARTIAL = "partial"
    NOT_DONE = "not_done"
    BLOCKED = "blocked"
    NONE = "none"


class Priority(LabelledEnum):
    HIGH = "high"
    MEDIUM = "medium"
    OPTIONAL = "optional"


class RunType(LabelledEnum):
    FULL = "full"
    REFRESH = "refresh"


class RunStatus(LabelledEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    FAILED = "failed"
    COMPLETED = "completed"


class JobClass(LabelledEnum):
    """PRD s3.4: each read posting is classified as one of these."""

    BUILD_ROLE = "build_role"
    ADMIN_OWNER_ROLE = "admin_owner_role"
    WORKING_TOOL = "working_tool"
    USER_ROLE = "user_role"


class JobState(LabelledEnum):
    OPEN = "open"
    CLOSED = "closed"


class SourceKind(LabelledEnum):
    """Primary sources can support Confirmed; aggregators only Lead (config/grading.yaml)."""

    PRIMARY = "primary"
    SECONDARY = "secondary"
    AGGREGATOR = "aggregator"


_with_labels(
    CoverageStatus,
    {
        "done": "Done",
        "partial": "Partial",
        "not_done": "Not done",
        "blocked": "Blocked",
        "none": "None",
    },
)
_with_labels(
    JobClass,
    {
        "build_role": "Build role",
        "admin_owner_role": "Admin and owner role",
        "working_tool": "Working tool",
        "user_role": "User role",
    },
)

# --------------------------------------------------------------------------- from config


def _config_enum(name: str, labels_by_value: dict[str, str]) -> type[LabelledEnum]:
    members = {value.upper(): value for value in labels_by_value}
    cls: type[LabelledEnum] = LabelledEnum(name, members)  # type: ignore[assignment,call-arg]
    cls.__module__ = __name__
    return _with_labels(cls, labels_by_value)


def build_person_level(levels_in_scope: list[str]) -> type[LabelledEnum]:
    return _config_enum("PersonLevel", {slug(label): label for label in levels_in_scope})


def build_lane(lanes: list[tuple[str, str]]) -> type[LabelledEnum]:
    return _config_enum("Lane", dict(lanes))


_rules = default_rules()
PersonLevel = build_person_level(_rules.people_scope.levels_in_scope)
Lane = build_lane([(lane.id, lane.label) for lane in _rules.lanes.lanes])

ALL_ENUMS: tuple[type[LabelledEnum], ...] = (
    Grade,
    CoverageStatus,
    Priority,
    RunType,
    RunStatus,
    JobClass,
    JobState,
    SourceKind,
    PersonLevel,
    Lane,
)

# --------------------------------------------------------------------------- column type


def enum_type(enum_cls: type[LabelledEnum], **kwargs: Any) -> sa.Enum:
    """Text column + named CHECK constraint (ck_<table>_<enum>) storing the enum's values."""
    return sa.Enum(
        enum_cls,
        name=enum_cls.db_name(),
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        length=ENUM_COLUMN_LENGTH,
        values_callable=lambda cls: [m.value for m in cls],
        **kwargs,
    )


# --------------------------------------------------------------------------- drift check

_QUOTED = re.compile(r"'((?:[^']|'')*)'")


def enum_check_drift(connection: sa.Connection, metadata: sa.MetaData) -> list[str]:
    """Compare each enum column's CHECK constraint in Postgres with the Python enum values.

    Catches a config change (a new lane, a renamed level) that was not followed by a migration.
    """
    problems: list[str] = []
    query = sa.text(
        "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
        "JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace "
        "WHERE c.contype = 'c' AND n.nspname = :schema AND t.relname = :table "
        "AND c.conname = :name"
    )
    for table in metadata.sorted_tables:
        for column in table.columns:
            if not isinstance(column.type, sa.Enum) or column.type.enum_class is None:
                continue
            enum_cls = column.type.enum_class
            name = f"ck_{table.name}_{column.type.name}"
            row = connection.execute(
                query, {"schema": table.schema or "public", "table": table.name, "name": name}
            ).first()
            if row is None:
                problems.append(f"{table.fullname}.{column.name}: CHECK {name} missing")
                continue
            in_db = {v.replace("''", "'") for v in _QUOTED.findall(row[0])}
            expected = {m.value for m in enum_cls}
            if in_db != expected:
                problems.append(
                    f"{table.fullname}.{column.name}: database allows {sorted(in_db)}, "
                    f"code expects {sorted(expected)}"
                )
    return problems
