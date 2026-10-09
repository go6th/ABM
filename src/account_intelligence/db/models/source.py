"""``sources``: every external item seen for an account, with a stable code.

``source_code`` (``S-0001``) is allocated once per account by
``account_intelligence.db.source_ids`` and never changes; ``url_hash`` is the
sha256 of ``url_normalized`` and the database checks that it is. A trigger
blocks deletes and any change to identity columns. First/last-seen references
may point to either the legacy ``runs`` lifecycle or the newer ``account_runs`` lifecycle.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from account_intelligence.db.base import Base, TimestampMixin, UuidPkMixin
from account_intelligence.db.enums import SourceKind, sql_in_values
from account_intelligence.db.types import SHA256_HEX

SOURCE_CODE_PATTERN = r"^S-[0-9]{4,}$"

# account_runs is owned by the run service and intentionally has no ORM class. Register only the
# composite key columns needed to resolve Source's database-enforced references; migrations/env.py
# excludes this external table from ORM schema drift checks.
if "account_runs" not in Base.metadata.tables:
    Table(
        "account_runs",
        Base.metadata,
        Column("tenant_id", Uuid, nullable=False),
        Column("account_id", Uuid, nullable=False),
        Column("id", Uuid, nullable=False),
        UniqueConstraint("tenant_id", "account_id", "id"),
    )


class Source(UuidPkMixin, TimestampMixin, Base):
    """A source URL with its stable per-account code (S-0001...)."""

    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("account_id", "source_code"),
        UniqueConstraint("account_id", "url_hash"),
        UniqueConstraint("tenant_id", "account_id", "id"),
        ForeignKeyConstraint(["tenant_id", "account_id"], ["accounts.tenant_id", "accounts.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "account_id", "first_seen_run_id"],
            ["runs.tenant_id", "runs.account_id", "runs.id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "account_id", "last_seen_run_id"],
            ["runs.tenant_id", "runs.account_id", "runs.id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "account_id", "first_seen_account_run_id"],
            ["account_runs.tenant_id", "account_runs.account_id", "account_runs.id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "account_id", "last_seen_account_run_id"],
            ["account_runs.tenant_id", "account_runs.account_id", "account_runs.id"],
        ),
        CheckConstraint(
            "num_nonnulls(first_seen_run_id, first_seen_account_run_id) = 1",
            name="one_first_seen_run",
        ),
        CheckConstraint(
            "num_nonnulls(last_seen_run_id, last_seen_account_run_id) = 1",
            name="one_last_seen_run",
        ),
        CheckConstraint(f"source_code ~ '{SOURCE_CODE_PATTERN}'", name="source_code_format"),
        CheckConstraint(f"kind IN {sql_in_values(SourceKind)}", name="kind"),
        CheckConstraint(
            "url_hash = encode(sha256(convert_to(url_normalized, 'UTF8')), 'hex')",
            name="url_hash_matches",
        ),
        Index("ix_sources_first_seen_run_id", "first_seen_run_id"),
        Index("ix_sources_last_seen_run_id", "last_seen_run_id"),
        Index("ix_sources_first_seen_account_run_id", "first_seen_account_run_id"),
        Index("ix_sources_last_seen_account_run_id", "last_seen_account_run_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id"), server_default=text("app_tenant()")
    )
    account_id: Mapped[UUID]
    source_code: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    url_normalized: Mapped[str] = mapped_column(Text)
    url_hash: Mapped[str] = mapped_column(SHA256_HEX)
    title: Mapped[str | None] = mapped_column(Text)
    publisher: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[date | None]
    kind: Mapped[str] = mapped_column(Text)
    first_seen_run_id: Mapped[UUID | None]
    last_seen_run_id: Mapped[UUID | None]
    first_seen_account_run_id: Mapped[UUID | None]
    last_seen_account_run_id: Mapped[UUID | None]
