"""``accounts``: one pursued company per tenant.

The row is the anchor for every run, source, fact, person, posting and
detection. ``domain`` is the identity key (unique per tenant) and is stored as
a bare hostname: lowercase, no scheme, no port, no path, enforced by the
database. ``careers_url_verified_at`` is set only when the careers site has
been visited and confirmed, never when a URL was merely constructed.

``primary_lane`` stays CHECK-free in v1: lanes become a per-tenant setting in
Phase 3 and a database CHECK would block custom lane lists.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UuidPkMixin
from src.db.types import JSONB

DOMAIN_SHAPE_CHECK = (
    "domain = lower(domain) "
    "AND position('/' in domain) = 0 "
    "AND position(':' in domain) = 0"
)


class Account(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "domain"),
        CheckConstraint(DOMAIN_SHAPE_CHECK, name="ck_accounts_domain_shape"),
        Index("ix_accounts_tenant_id_name", "tenant_id", "name"),
    )

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    careers_url: Mapped[str | None] = mapped_column(Text)
    careers_url_verified_at: Mapped[datetime | None]
    industry: Mapped[str | None] = mapped_column(Text)
    primary_lane: Mapped[str | None] = mapped_column(Text)
    pursuing_client: Mapped[str | None] = mapped_column(Text)
    # Starter columns kept unchanged (platform watch/cadence settings).
    watch_config_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    refresh_cadence: Mapped[str | None] = mapped_column(Text)
