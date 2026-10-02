"""``account_source_counters``: next free source code number per account.

Written only by ``account_intelligence.db.source_ids``. A trigger keeps the
counter moving forward, so a code is never handed out twice.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Integer, text
from sqlalchemy.orm import Mapped, mapped_column

from account_intelligence.db.base import Base, TimestampMixin


class AccountSourceCounter(TimestampMixin, Base):
    __tablename__ = "account_source_counters"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "account_id"], ["accounts.tenant_id", "accounts.id"]),
        CheckConstraint("next_value >= 1", name="next_value_positive"),
    )

    account_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id"), server_default=text("app_tenant()")
    )
    next_value: Mapped[int] = mapped_column(Integer, server_default=text("1"))
