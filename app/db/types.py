"""Column types shared by every data layer model (docs/data_layer/CONVENTIONS.md)."""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from sqlalchemy import DateTime, Dialect, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import mapped_column
from sqlalchemy.types import TypeDecorator

# ----------------------------------------------------------------------------- ids


def _pick_generator() -> tuple[Callable[[], uuid.UUID], int]:
    """uuid7 (time-ordered, Python 3.14+) where the runtime has it, otherwise uuid4."""
    gen = getattr(uuid, "uuid7", None)
    if callable(gen):
        return gen, 7
    return uuid.uuid4, 4


_GENERATOR, UUID_VERSION = _pick_generator()


def new_id() -> uuid.UUID:
    """A new primary key value."""
    return _GENERATOR()


# Raw SQL inserts that omit the id still get one: gen_random_uuid() is built into Postgres 13+
# (a v4 UUID; Postgres 18 adds uuidv7()).
SERVER_UUID_DEFAULT = text("gen_random_uuid()")

UUIDPk = Annotated[
    uuid.UUID,
    mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=new_id,
        server_default=SERVER_UUID_DEFAULT,
        sort_order=-100,
    ),
]

# ----------------------------------------------------------------------------- time

TZDateTime = DateTime(timezone=True)

CreatedAt = Annotated[
    datetime,
    mapped_column(TZDateTime, nullable=False, server_default=func.now(), sort_order=100),
]
UpdatedAt = Annotated[
    datetime,
    mapped_column(
        TZDateTime, nullable=False, server_default=func.now(), onupdate=func.now(), sort_order=101
    ),
]

# ----------------------------------------------------------------------------- JSON


class JSONObjectError(TypeError):
    pass


def _check_json(value: Any, path: str = "$") -> None:
    """Reject what JSON (and so JSONB) cannot round-trip faithfully."""
    if value is None or isinstance(value, str | bool | int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise JSONObjectError(f"{path}: NaN and infinity are not valid JSON")
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            _check_json(item, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise JSONObjectError(f"{path}: object keys must be strings, got {key!r}")
            _check_json(item, f"{path}.{key}")
        return
    raise JSONObjectError(f"{path}: {type(value).__name__} is not a JSON type")


class JSONObject(TypeDecorator[dict[str, Any]]):
    """JSONB column that only accepts a JSON object (a dict), checked before it reaches Postgres.

    Stricter than plain JSONB: a top-level list or scalar, non-string keys, NaN or infinity,
    and Python objects such as datetime or Decimal raise ``JSONObjectError`` at bind time instead
    of being silently coerced or stored as something else.
    """

    impl = JSONB
    cache_ok = True

    def process_bind_param(self, value: dict[str, Any] | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise JSONObjectError(f"expected a JSON object (dict), got {type(value).__name__}")
        _check_json(value)
        return value

    def process_result_value(self, value: Any, dialect: Dialect) -> dict[str, Any] | None:
        return value if value is None else dict(value)
