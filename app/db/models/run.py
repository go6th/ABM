"""``intel.runs``: one full run or refresh of an account (roadmap s11.1 Run).

The run pins the config bundle it used (``config_version``), so results can always be traced to
the checklist and grading rules in force. The credit cap is agreed before the run leaves
``queued`` (roadmap s3 principle 7: one cap agreed before the run, enforced by software).

Status changes go through the methods below, which keep the timestamps consistent with the
CHECK constraints. The database enforces the consistency; the methods enforce the order.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.db.base import SCHEMA, Model
from app.db.enums import RunStatus, RunType, enum_type

# Allowed status changes. Terminal states have no exits; a retry is a new run.
TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.FAILED}),
    RunStatus.RUNNING: frozenset({RunStatus.PAUSED, RunStatus.FAILED, RunStatus.COMPLETED}),
    RunStatus.PAUSED: frozenset({RunStatus.RUNNING, RunStatus.FAILED}),
    RunStatus.FAILED: frozenset(),
    RunStatus.COMPLETED: frozenset(),
}


class InvalidTransition(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


class Run(Model):
    __tablename__ = "runs"
    __table_args__ = (
        # Target of the composite self reference, so a parent must belong to the same account.
        UniqueConstraint("id", "account_id"),
        ForeignKeyConstraint(
            ["parent_run_id", "account_id"],
            [f"{SCHEMA}.runs.id", f"{SCHEMA}.runs.account_id"],
            ondelete="RESTRICT",
        ),
        Index(None, "status"),
        CheckConstraint(
            "status = 'queued' OR credit_cap IS NOT NULL", name="cap_agreed_before_start"
        ),
        CheckConstraint("credit_cap >= 0", name="credit_cap_not_negative"),
        CheckConstraint("finished_at >= started_at", name="finished_after_started"),
        CheckConstraint("length(btrim(config_version)) > 0", name="config_version_not_blank"),
        CheckConstraint(
            "(run_type = 'refresh') = (parent_run_id IS NOT NULL)", name="refresh_has_parent"
        ),
        CheckConstraint("parent_run_id <> id", name="not_own_parent"),
        CheckConstraint(
            "CASE status"
            " WHEN 'queued' THEN started_at IS NULL AND finished_at IS NULL"
            " WHEN 'running' THEN started_at IS NOT NULL AND finished_at IS NULL"
            " WHEN 'paused' THEN started_at IS NOT NULL AND finished_at IS NULL"
            " WHEN 'completed' THEN started_at IS NOT NULL AND finished_at IS NOT NULL"
            " WHEN 'failed' THEN finished_at IS NOT NULL"
            " END",
            name="timestamps_match_status",
        ),
        CheckConstraint("(status = 'failed') = (error IS NOT NULL)", name="error_iff_failed"),
        CheckConstraint("error IS NULL OR length(btrim(error)) > 0", name="error_not_blank"),
        {"schema": SCHEMA, "comment": "Full runs and refreshes of an account (roadmap s11.1 Run)"},
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.accounts.id", ondelete="RESTRICT"), nullable=False
    )
    run_type: Mapped[str] = mapped_column(enum_type(RunType), nullable=False)
    status: Mapped[str] = mapped_column(
        enum_type(RunStatus),
        nullable=False,
        default=RunStatus.QUEUED.value,
        server_default=RunStatus.QUEUED.value,
    )
    config_version: Mapped[str] = mapped_column(
        nullable=False, comment="Rule bundle version pinned for this run (app.rules)"
    )
    credit_cap: Mapped[int | None] = mapped_column(
        comment="Agreed before the run leaves queued. Internal: never shown to clients"
    )
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        comment="Run this refresh starts from (same account)"
    )
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    error: Mapped[str | None] = mapped_column(comment="Why the run failed; set only when failed")

    def __init__(self, **kwargs: object) -> None:
        # Queued from construction, not only after the INSERT, so lifecycle methods work on a
        # new object before it is flushed.
        kwargs.setdefault("status", RunStatus.QUEUED.value)
        super().__init__(**kwargs)

    @validates("run_type")
    def _check_type(self, _key: str, value: str) -> str:
        return RunType(value).value

    # ------------------------------------------------------------------ lifecycle

    def agree_cap(self, credits: int) -> None:
        if self.status != RunStatus.QUEUED:
            raise InvalidTransition("the credit cap is agreed before the run starts")
        if credits < 0:
            raise ValueError("credit cap cannot be negative")
        self.credit_cap = credits

    def _move(self, to: RunStatus) -> None:
        current = RunStatus(self.status)
        if to not in TRANSITIONS[current]:
            raise InvalidTransition(f"run cannot go from {current.value} to {to.value}")
        self.status = to.value

    def start(self, at: datetime | None = None) -> None:
        if self.credit_cap is None:
            raise InvalidTransition("agree the credit cap before starting the run")
        self._move(RunStatus.RUNNING)
        if self.started_at is None:
            self.started_at = at or _now()

    def pause(self) -> None:
        self._move(RunStatus.PAUSED)

    def resume(self) -> None:
        self._move(RunStatus.RUNNING)

    def complete(self, at: datetime | None = None) -> None:
        self._move(RunStatus.COMPLETED)
        self.finished_at = at or _now()

    def fail(self, error: str, at: datetime | None = None) -> None:
        if not error.strip():
            raise ValueError("a failed run must say why")
        self._move(RunStatus.FAILED)
        self.error = error.strip()
        self.finished_at = at or _now()


# Latest runs of an account first (defined here so it can use the mapped columns' sort order).
Index("ix_runs_account_id_created_at", Run.account_id, Run.created_at.desc())
