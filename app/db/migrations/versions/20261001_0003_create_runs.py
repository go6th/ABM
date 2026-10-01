"""create runs

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-01

intel.runs (roadmap s11.1 Run, data layer step 8). Enumerated columns are varchar plus one
explicit CHECK, a fixed snapshot of the values (CONVENTIONS.md, "Writing migrations").
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUN_TYPES = ("full", "refresh")
RUN_STATUSES = ("queued", "running", "paused", "failed", "completed")

TIMESTAMPS_MATCH_STATUS = (
    "CASE status"
    " WHEN 'queued' THEN started_at IS NULL AND finished_at IS NULL"
    " WHEN 'running' THEN started_at IS NOT NULL AND finished_at IS NULL"
    " WHEN 'paused' THEN started_at IS NOT NULL AND finished_at IS NULL"
    " WHEN 'completed' THEN started_at IS NOT NULL AND finished_at IS NOT NULL"
    " WHEN 'failed' THEN finished_at IS NOT NULL"
    " END"
)


def _in_list(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("run_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), server_default="queued", nullable=False),
        sa.Column(
            "config_version",
            sa.Text(),
            nullable=False,
            comment="Rule bundle version pinned for this run (app.rules)",
        ),
        sa.Column(
            "credit_cap",
            sa.Integer(),
            nullable=True,
            comment="Agreed before the run leaves queued. Internal: never shown to clients",
        ),
        sa.Column(
            "parent_run_id",
            sa.Uuid(),
            nullable=True,
            comment="Run this refresh starts from (same account)",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "error", sa.Text(), nullable=True, comment="Why the run failed; set only when failed"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runs")),
        sa.UniqueConstraint("id", "account_id", name=op.f("uq_runs_id_account_id")),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["intel.accounts.id"],
            name=op.f("fk_runs_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_run_id", "account_id"],
            ["intel.runs.id", "intel.runs.account_id"],
            name=op.f("fk_runs_parent_run_id_account_id_runs"),
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(_in_list("run_type", RUN_TYPES), name=op.f("ck_runs_run_type")),
        sa.CheckConstraint(_in_list("status", RUN_STATUSES), name=op.f("ck_runs_run_status")),
        sa.CheckConstraint(
            "status = 'queued' OR credit_cap IS NOT NULL",
            name=op.f("ck_runs_cap_agreed_before_start"),
        ),
        sa.CheckConstraint("credit_cap >= 0", name=op.f("ck_runs_credit_cap_not_negative")),
        sa.CheckConstraint(
            "finished_at >= started_at", name=op.f("ck_runs_finished_after_started")
        ),
        sa.CheckConstraint(
            "length(btrim(config_version)) > 0", name=op.f("ck_runs_config_version_not_blank")
        ),
        sa.CheckConstraint(
            "(run_type = 'refresh') = (parent_run_id IS NOT NULL)",
            name=op.f("ck_runs_refresh_has_parent"),
        ),
        sa.CheckConstraint("parent_run_id <> id", name=op.f("ck_runs_not_own_parent")),
        sa.CheckConstraint(TIMESTAMPS_MATCH_STATUS, name=op.f("ck_runs_timestamps_match_status")),
        sa.CheckConstraint(
            "(status = 'failed') = (error IS NOT NULL)", name=op.f("ck_runs_error_iff_failed")
        ),
        sa.CheckConstraint(
            "error IS NULL OR length(btrim(error)) > 0", name=op.f("ck_runs_error_not_blank")
        ),
        schema="intel",
        comment="Full runs and refreshes of an account (roadmap s11.1 Run)",
    )
    op.create_index(op.f("ix_runs_status"), "runs", ["status"], schema="intel")
    op.create_index(
        op.f("ix_runs_account_id_created_at"),
        "runs",
        ["account_id", sa.text("created_at DESC")],
        schema="intel",
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_runs_account_id_created_at"), table_name="runs", schema="intel")
    op.drop_index(op.f("ix_runs_status"), table_name="runs", schema="intel")
    op.drop_table("runs", schema="intel")
