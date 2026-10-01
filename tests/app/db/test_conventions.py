"""Shared conventions: UUID keys, timestamps, the JSONB object type, and the convention checker."""

from __future__ import annotations

import math
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, MetaData, String, create_engine, select, text, update
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.db import types as dbtypes
from app.db.base import (
    NAMING_CONVENTION,
    TYPE_ANNOTATION_MAP,
    Base,
    IdMixin,
    TimestampMixin,
    convention_violations,
)
from app.db.types import JSONObject, JSONObjectError, new_id
from app.db.url import sqlalchemy_url


class SampleBase(DeclarativeBase):
    """Same conventions as ``Base`` but its own metadata, so samples never reach migrations."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = TYPE_ANNOTATION_MAP


class Widget(IdMixin, TimestampMixin, SampleBase):
    __tablename__ = "widget"
    __table_args__ = {"schema": "intel"}  # noqa: RUF012

    status: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict[str, Any]]


# --------------------------------------------------------------------- ids


def test_new_id_uses_uuid7_when_available() -> None:
    expected = 7 if hasattr(uuid, "uuid7") else 4
    assert expected == dbtypes.UUID_VERSION
    ids = [new_id() for _ in range(1000)]
    assert len(set(ids)) == 1000
    assert {i.version for i in ids} == {expected}
    if expected == 7:
        assert ids == sorted(ids), "uuid7 values must be time ordered"


def test_falls_back_to_uuid4(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(uuid, "uuid7", raising=False)
    gen, version = dbtypes._pick_generator()
    assert version == 4
    assert gen().version == 4


# --------------------------------------------------------------------- JSON


@pytest.mark.parametrize(
    "value",
    [
        {"a": 1, "b": [1, 2.5, None, True], "c": {"d": "x"}},
        {},
    ],
)
def test_json_object_accepts_json(value: dict[str, Any]) -> None:
    assert JSONObject().process_bind_param(value, dialect=None) == value  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ([1, 2], "expected a JSON object"),
        ("text", "expected a JSON object"),
        ({"x": math.nan}, "NaN"),
        ({"x": [1, math.inf]}, r"\$\.x\[1\]"),
        ({"x": datetime.now(UTC)}, "datetime is not a JSON type"),
        ({"x": Decimal("1.5")}, "Decimal is not a JSON type"),
        ({1: "a"}, "keys must be strings"),
        ({"x": {"y": {2: 3}}}, r"\$\.x\.y"),
        ({"x": {1, 2}}, "set is not a JSON type"),
    ],
)
def test_json_object_rejects_what_json_cannot_hold(value: Any, message: str) -> None:
    with pytest.raises(JSONObjectError, match=message):
        JSONObject().process_bind_param(value, dialect=None)  # type: ignore[arg-type]


def test_json_object_passes_none_through() -> None:
    assert JSONObject().process_bind_param(None, dialect=None) is None  # type: ignore[arg-type]
    assert JSONObject().process_result_value(None, dialect=None) is None  # type: ignore[arg-type]


# --------------------------------------------------------------------- conventions


def test_type_map_gives_timestamptz_and_jsonb() -> None:
    table = Widget.__table__
    assert table.c.created_at.type.timezone is True  # type: ignore[attr-defined]
    assert isinstance(table.c.payload.type, JSONObject)
    assert table.c.id.primary_key


def test_sample_model_meets_conventions() -> None:
    assert convention_violations(SampleBase.metadata) == []


def test_every_real_table_meets_conventions() -> None:
    import app.db.models  # noqa: F401

    assert convention_violations(Base.metadata) == []


def test_checker_reports_each_broken_rule() -> None:
    class BadBase(DeclarativeBase):
        metadata = MetaData(naming_convention=NAMING_CONVENTION)
        type_annotation_map = TYPE_ANNOTATION_MAP

    class IntKey(BadBase):
        __tablename__ = "int_key"
        __table_args__ = {"schema": "intel"}  # noqa: RUF012
        pk: Mapped[int] = mapped_column(primary_key=True)

    class NaiveTime(IdMixin, BadBase):
        __tablename__ = "naive_time"
        __table_args__ = {"schema": "public"}  # noqa: RUF012
        created_at: Mapped[datetime] = mapped_column(
            String(30).with_variant(String(30), "postgresql"), nullable=True
        )
        updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    problems = "\n".join(convention_violations(BadBase.metadata))
    assert "intel.int_key: primary key must be the single column 'id'" in problems
    assert "intel.int_key: missing created_at" in problems
    assert "public.naive_time: schema must be one of" in problems
    assert "public.naive_time: created_at must be timezone-aware" in problems
    assert "public.naive_time: created_at must be NOT NULL with a server default" in problems
    assert "public.naive_time: updated_at must refresh on update (onupdate)" in problems


# --------------------------------------------------------------------- live database


@pytest.fixture()
def engine(empty_database_url: str) -> Iterator[Engine]:
    eng = create_engine(sqlalchemy_url(empty_database_url))
    with eng.begin() as conn:
        conn.execute(text("CREATE SCHEMA intel"))
    SampleBase.metadata.create_all(eng)
    yield eng
    eng.dispose()


def test_columns_are_uuid_timestamptz_and_jsonb_in_postgres(engine: Engine) -> None:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name, data_type, column_default FROM information_schema.columns "
                "WHERE table_schema = 'intel' AND table_name = 'widget'"
            )
        ).all()
    cols = {r[0]: (r[1], r[2]) for r in rows}
    assert cols["id"] == ("uuid", "gen_random_uuid()")
    assert cols["created_at"] == ("timestamp with time zone", "now()")
    assert cols["updated_at"] == ("timestamp with time zone", "now()")
    assert cols["payload"][0] == "jsonb"


def test_insert_sets_id_and_timestamps(engine: Engine) -> None:
    with Session(engine) as s, s.begin():
        w = Widget(status="new", payload={"k": [1, 2]})
        s.add(w)
        s.flush()
        assert w.id.version == dbtypes.UUID_VERSION
    with Session(engine) as s:
        got = s.scalars(select(Widget)).one()
        assert got.created_at.tzinfo is not None
        assert got.created_at == got.updated_at
        assert got.payload == {"k": [1, 2]}


def test_orm_and_core_updates_refresh_updated_at(engine: Engine) -> None:
    with Session(engine) as s, s.begin():
        w = Widget(status="new", payload={})
        s.add(w)
        s.flush()
        s.refresh(w)
        wid, created, first = w.id, w.created_at, w.updated_at

    with Session(engine) as s, s.begin():
        obj = s.get(Widget, wid)
        assert obj is not None
        obj.status = "read"
    with Session(engine) as s:
        after_orm = s.get(Widget, wid)
        assert after_orm is not None
        assert after_orm.updated_at > first
        assert after_orm.created_at == created
        orm_time = after_orm.updated_at

    with Session(engine) as s, s.begin():
        s.execute(update(Widget).where(Widget.id == wid).values(status="done"))
    with Session(engine) as s:
        after_core = s.get(Widget, wid)
        assert after_core is not None
        assert after_core.updated_at > orm_time


def test_raw_sql_update_does_not_refresh_updated_at(engine: Engine) -> None:
    """The documented limitation of the trigger-free approach."""
    with Session(engine) as s, s.begin():
        w = Widget(status="new", payload={})
        s.add(w)
        s.flush()
        s.refresh(w)
        wid, before = w.id, w.updated_at
    with engine.begin() as conn:
        conn.execute(text("UPDATE intel.widget SET status = 'raw' WHERE id = :id"), {"id": wid})
    with Session(engine) as s:
        got = s.get(Widget, wid)
        assert got is not None
        assert got.status == "raw"
        assert got.updated_at == before


def test_raw_sql_insert_gets_server_defaults(engine: Engine) -> None:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO intel.widget (status, payload) VALUES ('x', '{}'::jsonb) "
                "RETURNING id, created_at, updated_at"
            )
        ).one()
    assert isinstance(row.id, uuid.UUID)
    assert row.created_at.tzinfo is not None
    assert row.created_at == row.updated_at


def test_invalid_json_never_reaches_the_database(engine: Engine) -> None:
    with Session(engine) as s, pytest.raises(StatementError, match="NaN"), s.begin():
        s.add(Widget(status="x", payload={"score": math.nan}))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM intel.widget")).scalar_one() == 0
