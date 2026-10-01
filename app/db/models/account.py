"""``intel.accounts``: one target company (roadmap s11.1 Account).

The careers URL is stored only once it has been verified by visiting it, never constructed
(roadmap s11.1, PRD s3.3). ``record_careers_url`` is the only writer and stamps the time.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from urllib.parse import urlsplit

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.db.base import DEFAULT_TENANT_ID, SCHEMA, Model
from app.db.enums import Lane, enum_type

# A lowercase hostname with at least one dot: labels of letters, digits and inner hyphens.
DOMAIN_PATTERN = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"


class InvalidDomain(ValueError):
    pass


class InvalidCareersUrl(ValueError):
    pass


def normalize_domain(raw: str) -> str:
    """'https://WWW.Example.com:443/careers?x=1' -> 'example.com'.

    Strips scheme, credentials, port, path, query, fragment, a leading 'www.' and a trailing
    dot; lowercases; converts international names to their ASCII (punycode) form.
    """
    value = raw.strip()
    if not value:
        raise InvalidDomain("domain is empty")
    parts = urlsplit(value if "//" in value else f"//{value}")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise InvalidDomain(f"no host in {raw!r}")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise InvalidDomain(f"{raw!r} is not a valid domain") from exc
    host = host.lower().removeprefix("www.")
    if not re.fullmatch(DOMAIN_PATTERN, host):
        raise InvalidDomain(f"{raw!r} does not normalise to a valid domain (got {host!r})")
    return host


class Account(Model):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "domain"),
        Index(None, "tenant_id", "name"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint("domain = lower(domain)", name="domain_lowercase"),
        CheckConstraint("position('://' in domain) = 0", name="domain_no_scheme"),
        CheckConstraint("position('/' in domain) = 0", name="domain_no_slash"),
        CheckConstraint("domain NOT LIKE 'www.%'", name="domain_no_www"),
        CheckConstraint(f"domain ~ '{DOMAIN_PATTERN}'", name="domain_hostname"),
        CheckConstraint("careers_url ~ '^https?://[^/\\s]+'", name="careers_url_http"),
        CheckConstraint(
            "(careers_url IS NULL) = (careers_url_verified_at IS NULL)",
            name="careers_url_verified_together",
        ),
        CheckConstraint(
            "pursuing_client IS NULL OR length(btrim(pursuing_client)) > 0",
            name="pursuing_client_not_blank",
        ),
        {"schema": SCHEMA, "comment": "Target companies (roadmap s11.1 Account)"},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        default=DEFAULT_TENANT_ID,
        server_default=text(f"'{DEFAULT_TENANT_ID}'::uuid"),
        comment="Single default tenant in v1; Phase 3 adds the tenants table and foreign key",
    )
    name: Mapped[str] = mapped_column(nullable=False)
    domain: Mapped[str] = mapped_column(
        nullable=False, comment="Normalised: lowercase, no scheme, no www, no path"
    )
    careers_url: Mapped[str | None] = mapped_column(
        comment="Only set once verified by visiting; never constructed"
    )
    careers_url_verified_at: Mapped[datetime | None] = mapped_column(
        comment="When careers_url was verified by visiting it"
    )
    industry: Mapped[str | None]
    primary_lane: Mapped[str | None] = mapped_column(enum_type(Lane))
    pursuing_client: Mapped[str | None] = mapped_column(
        comment="Client the research is for; decides whose history is checked (PRD s3.3)"
    )

    @validates("domain")
    def _normalise_domain(self, _key: str, value: str) -> str:
        return normalize_domain(value)

    @validates("careers_url_verified_at")
    def _verified_at_is_aware(self, _key: str, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("careers_url_verified_at must be timezone-aware")
        return value

    def record_careers_url(self, url: str, verified_at: datetime) -> None:
        """Store a careers URL that was just verified by visiting it."""
        url = url.strip()
        if urlsplit(url).scheme not in {"http", "https"} or not urlsplit(url).netloc:
            raise InvalidCareersUrl(f"{url!r} is not an http(s) URL")
        self.careers_url = url
        self.careers_url_verified_at = verified_at
