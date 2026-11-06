"""``account_source_counters``: next free source code number per account.

Written only by ``account_intelligence.db.source_ids``. A trigger keeps the
counter moving forward, so a code is never handed out twice.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from account_intelligence.db.base import Base, TimestampMixin


class AccountSourceCounter(TimestampMixin, Base):
    """Next source number per account (stable codes across runs)."""

    __tablename__ = "account_source_counters"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "account_id"], ["accounts.tenant_id", "accounts.id"]),
        CheckConstraint("next_value >= 1", name="next_value_positive"),
        Index("ix_account_source_counters_tenant_owner", "tenant_id", "owner_user_id"),
    )

    account_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id"), server_default=text("app_tenant()")
    )
    # private workspaces (migration 0063): the acting user's row unless the code says otherwise
    owner_user_id: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("app_user()")
    )
    next_value: Mapped[int] = mapped_column(Integer, server_default=text("1"))
