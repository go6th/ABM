"""``runs``: one full run or refresh of an account.

The database owns the lifecycle rules (migration 0004): the credit cap must be
set before a run leaves ``queued``; timestamps follow the status; ``error`` is
present exactly when the run failed; a refresh points at a parent run of the
same account. A trigger enforces ``RUN_TRANSITIONS`` and keeps identity columns
immutable. Runs are never deleted.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from account_intelligence.db.base import Base, TimestampMixin, UuidPkMixin
from account_intelligence.db.enums import RunStatus, RunType, sql_in_values


class Run(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "account_id", "id"),
        ForeignKeyConstraint(["tenant_id", "account_id"], ["accounts.tenant_id", "accounts.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "account_id", "parent_run_id"],
            ["runs.tenant_id", "runs.account_id", "runs.id"],
        ),
        CheckConstraint(f"run_type IN {sql_in_values(RunType)}", name="run_type"),
        CheckConstraint(f"status IN {sql_in_values(RunStatus)}", name="status"),
        CheckConstraint(
            "status = 'queued' OR credit_cap IS NOT NULL", name="credit_cap_before_start"
        ),
        CheckConstraint(
            "(run_type = 'refresh') = (parent_run_id IS NOT NULL)", name="refresh_has_parent"
        ),
        CheckConstraint("finished_at >= started_at", name="finished_after_started"),
        Index("ix_runs_account_id_created_at", "account_id", text("created_at DESC")),
        Index("ix_runs_status", "status"),
        Index(
            "ix_runs_parent_run_id",
            "parent_run_id",
            postgresql_where=text("parent_run_id IS NOT NULL"),
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id"), server_default=text("app_tenant()")
    )
    account_id: Mapped[UUID]
    run_type: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'queued'"))
    config_version: Mapped[str] = mapped_column(Text)
    credit_cap: Mapped[int | None] = mapped_column(Integer)
    parent_run_id: Mapped[UUID | None]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    error: Mapped[str | None] = mapped_column(Text)
