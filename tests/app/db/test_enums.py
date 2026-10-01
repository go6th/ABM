"""Enums: exact values, no drift from config, and CHECK constraints that hold in Postgres."""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from psycopg.errors import CheckViolation
from sqlalchemy import Column, Engine, MetaData, Table, create_engine, insert, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.schema import CreateTable

from app.db.base import NAMING_CONVENTION
from app.db.enums import (
    ALL_ENUMS,
    CoverageStatus,
    Grade,
    JobClass,
    JobState,
    Lane,
    PersonLevel,
    Priority,
    RunStatus,
    RunType,
    SourceKind,
    build_lane,
    build_person_level,
    enum_check_drift,
    enum_type,
    slug,
)
from app.db.url import sqlalchemy_url
from app.people import _LEVEL_PATTERNS
from app.rules import RuleBundle, load_rules
from app.rules.loader import DEFAULT_CONFIG_DIR
from app.rules.schemas import CoverageStatus as ConfigStatus
from app.rules.schemas import GradeName
from app.rules.schemas import Priority as ConfigPriority


def values(enum_cls: type) -> list[str]:
    return [m.value for m in enum_cls]  # type: ignore[attr-defined]


# --------------------------------------------------------------------- exact values


@pytest.mark.parametrize(
    ("enum_cls", "expected"),
    [
        (Grade, ["confirmed", "probable", "hypothesis", "lead"]),
        (CoverageStatus, ["done", "partial", "not_done", "blocked", "none"]),
        (Priority, ["high", "medium", "optional"]),
        (RunType, ["full", "refresh"]),
        (RunStatus, ["queued", "running", "paused", "failed", "completed"]),
        (JobClass, ["build_role", "admin_owner_role", "working_tool", "user_role"]),
        (JobState, ["open", "closed"]),
        (SourceKind, ["primary", "secondary", "aggregator"]),
        (
            Lane,
            ["salesforce", "servicenow", "sap", "tableau_analytics", "veeva", "data_ai", "other"],
        ),
    ],
    ids=lambda x: getattr(x, "__name__", ""),
)
def test_values(enum_cls: type, expected: list[str]) -> None:
    assert values(enum_cls) == expected


def test_stored_values_are_snake_case_and_fit_the_column() -> None:
    for enum_cls in ALL_ENUMS:
        for v in values(enum_cls):
            assert re.fullmatch(r"[a-z][a-z0-9_]*", v), (enum_cls.__name__, v)
            assert len(v) <= 64


def test_constraint_names_are_unique() -> None:
    names = [e.db_name() for e in ALL_ENUMS]
    assert len(names) == len(set(names))
    assert CoverageStatus.db_name() == "coverage_status"


# --------------------------------------------------------------------- no drift from config


def test_grade_matches_grading_yaml(rules: RuleBundle) -> None:
    config_names = [g.name.value for g in rules.grading.grades]
    assert values(Grade) == [slug(n) for n in config_names]
    assert [g.label for g in Grade] == config_names
    assert [g.label for g in Grade] == [g.value for g in GradeName]


def test_aggregators_only_support_lead(rules: RuleBundle) -> None:
    allowed = {Grade.from_label(g.value) for g in rules.grading.rules.aggregator_allowed_grades}
    assert allowed == {Grade.LEAD}
    assert SourceKind.AGGREGATOR in SourceKind


def test_absence_and_keyword_grades_exist(rules: RuleBundle) -> None:
    assert Grade.from_label(rules.grading.rules.absence_of_evidence_grade.value) is Grade.HYPOTHESIS
    assert Grade.from_label(rules.grading.rules.keyword_match_grade.value) is Grade.LEAD


def test_coverage_status_matches_checklist_yaml(rules: RuleBundle) -> None:
    config_labels = [s.value for s in rules.checklist.statuses]
    assert [s.label for s in CoverageStatus] == config_labels
    assert values(CoverageStatus) == [slug(s) for s in config_labels]
    assert {s.label for s in CoverageStatus} == {s.value for s in ConfigStatus}


def test_priority_matches_checklist_yaml(rules: RuleBundle) -> None:
    used = {r.priority.value for r in rules.checklist.rows}
    assert {p.label for p in Priority} == used
    assert {p.label for p in Priority} == {p.value for p in ConfigPriority}
    for row in rules.checklist.rows:
        assert Priority.from_label(row.priority.value).label == row.priority.value


def test_person_level_is_read_from_people_scope(rules: RuleBundle) -> None:
    labels = rules.people_scope.levels_in_scope
    assert [m.label for m in PersonLevel] == labels
    assert values(PersonLevel) == [slug(label) for label in labels]


def test_every_level_the_people_code_assigns_is_storable() -> None:
    for level, _ in _LEVEL_PATTERNS:
        assert PersonLevel.from_label(level).label == level


def test_lane_is_read_from_lanes_yaml(rules: RuleBundle) -> None:
    assert values(Lane) == rules.lanes.ids
    assert Lane("tableau_analytics").label == "Tableau and analytics"


def test_config_change_changes_the_enum(tmp_path: Path) -> None:
    cfg = tmp_path / "config"
    shutil.copytree(DEFAULT_CONFIG_DIR, cfg)
    data = yaml.safe_load((cfg / "lanes.yaml").read_text())
    data["lanes"].insert(0, {"id": "workday", "label": "Workday"})
    (cfg / "lanes.yaml").write_text(yaml.safe_dump(data))
    lanes = load_rules(cfg).lanes
    lane = build_lane([(x.id, x.label) for x in lanes.lanes])
    assert values(lane)[0] == "workday"
    level = build_person_level(["Director", "Principal Engineer"])
    assert values(level) == ["director", "principal_engineer"]


def test_lanes_config_is_validated(tmp_path: Path) -> None:
    from app.rules import ConfigError

    for bad, message in [
        ({"id": "Data AI", "label": "x"}, "lowercase"),
        ({"id": "sap", "label": "dup"}, "unique"),
    ]:
        cfg = tmp_path / message
        shutil.copytree(DEFAULT_CONFIG_DIR, cfg)
        data = yaml.safe_load((cfg / "lanes.yaml").read_text())
        data["lanes"].append(bad)
        (cfg / "lanes.yaml").write_text(yaml.safe_dump(data))
        with pytest.raises(ConfigError, match=message):
            load_rules(cfg)


def test_labels_round_trip_and_reject_unknown() -> None:
    for enum_cls in ALL_ENUMS:
        for member in enum_cls:
            assert enum_cls.from_label(member.label) is member
    with pytest.raises(ValueError):
        Grade.from_label("Certain")
    assert JobClass.ADMIN_OWNER_ROLE.label == "Admin and owner role"
    assert RunStatus.PAUSED.label == "Paused"


# --------------------------------------------------------------------- column type

sample_md = MetaData(naming_convention=NAMING_CONVENTION)
sample = Table(
    "enum_sample",
    sample_md,
    Column("grade", enum_type(Grade), nullable=False),
    Column("lane", enum_type(Lane), nullable=True),
    schema="intel",
)


def test_column_is_text_with_named_check() -> None:
    ddl = str(CreateTable(sample).compile(dialect=postgresql.dialect()))
    assert "grade VARCHAR(64) NOT NULL" in ddl
    assert "CONSTRAINT ck_enum_sample_grade CHECK" in ddl
    for v in values(Grade):
        assert f"'{v}'" in ddl
    assert "CREATE TYPE" not in ddl


@pytest.fixture()
def engine(empty_database_url: str) -> Iterator[Engine]:
    eng = create_engine(sqlalchemy_url(empty_database_url))
    with eng.begin() as conn:
        conn.execute(text("CREATE SCHEMA intel"))
    sample_md.create_all(eng)
    yield eng
    eng.dispose()


def test_database_accepts_valid_and_rejects_invalid(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(insert(sample).values(grade=Grade.LEAD, lane=Lane("veeva")))
        conn.execute(text("INSERT INTO intel.enum_sample (grade) VALUES ('confirmed')"))
    with pytest.raises(IntegrityError) as info, engine.begin() as conn:
        conn.execute(text("INSERT INTO intel.enum_sample (grade) VALUES ('Confirmed')"))
    assert isinstance(info.value.orig, CheckViolation)
    with pytest.raises(StatementError), engine.begin() as conn:
        conn.execute(insert(sample).values(grade="certain"))
    with engine.connect() as conn:
        stored = conn.execute(text("SELECT grade FROM intel.enum_sample ORDER BY 1")).scalars()
        assert list(stored) == ["confirmed", "lead"]


def test_drift_check_is_clean_then_catches_a_change(engine: Engine) -> None:
    with engine.connect() as conn:
        assert enum_check_drift(conn, sample_md) == []
    # Simulate config gaining a lane without a migration: code expects a value the DB rejects.
    grown = build_lane([*[(m.value, m.label) for m in Lane], ("workday", "Workday")])
    md = MetaData(naming_convention=NAMING_CONVENTION)
    Table("enum_sample", md, Column("lane", enum_type(grown)), schema="intel")
    with engine.connect() as conn:
        problems = enum_check_drift(conn, md)
    assert len(problems) == 1
    assert "intel.enum_sample.lane" in problems[0] and "workday" in problems[0]
    md2 = MetaData(naming_convention=NAMING_CONVENTION)
    Table("enum_sample", md2, Column("lane", enum_type(Lane), key="x"), schema="intel")
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE intel.enum_sample DROP CONSTRAINT ck_enum_sample_lane"))
        assert "CHECK ck_enum_sample_lane missing" in enum_check_drift(conn, md2)[0]
