"""``tenants``: the isolation boundary. Every tenant-owned row references one.

Created by migration 0001 (starter schema). Mapped here so foreign keys resolve
in model metadata; tenancy is administered by the migrator role, not the app.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from account_intelligence.db.base import Base, UuidPkMixin
from account_intelligence.db.types import JSONB


class Tenant(UuidPkMixin, Base):
    """A tenant (customer organization) and its policy."""

    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(Text)
    plan: Mapped[str] = mapped_column(Text, server_default=text("'concierge'"))
    region: Mapped[str] = mapped_column(Text, server_default=text("'ap-south-1'"))
    isolation_tier: Mapped[str] = mapped_column(Text, server_default=text("'pooled'"))
    kms_key_arn: Mapped[str | None] = mapped_column(Text)
    policy_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
