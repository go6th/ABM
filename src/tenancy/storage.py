"""Per-tenant object paths and short-lived download tokens (TN4).

Pure: no clock, no randomness, no I/O. Every new object key is built by ``tenant_path`` from the
CONTEXT tenant (``tenants/<tenant>/<kind>/...``); listing prefixes come from ``tenant_prefix``;
``tenant_of_key`` reads the owner back from any key the platform writes, including the
lifecycle prefixes ``quarantine/tenants/<tenant>/`` and ``uploads/tenants/<tenant>/`` and the two
layouts that predate this module (deliverables ``<tenant>/<account>/<run>/deliverables/..`` and
the operator upload quarantine ``quarantine/<tenant>/..``), so a download is refused unless the
key belongs to the session's tenant.

A download token binds tenant, object key, content hash and expiry under an HMAC key held in
Secret Manager (``SecretStore``); it is verified against the SESSION tenant, never a tenant in
the token alone. Lifetimes are configuration (``config/session.yaml``), never constants here.

GUARANTEE(test_storage_paths_and_tokens_are_tenant_bound)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,199}$")
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_LAYOUTS = (
    re.compile(rf"^tenants/(?P<t>{_UUID})/"),
    # short-lived objects under top-level prefixes, so the bucket's lifecycle rules (which match
    # a key's start only) delete them: uploads waiting for the scan, resumable upload leftovers
    re.compile(rf"^quarantine/tenants/(?P<t>{_UUID})/"),
    re.compile(rf"^uploads/tenants/(?P<t>{_UUID})/"),
    re.compile(rf"^(?P<t>{_UUID})/{_UUID}/{_UUID}/deliverables/"),
    re.compile(rf"^quarantine/(?P<t>{_UUID})/"),
)
KINDS = frozenset(
    {
        "raw",
        "branding",
        "exports",
        "audit-exports",
        "connector-quarantine",
        "usage-exports",
        "uploads",
    }
)


class PathRefused(ValueError):
    """A key or segment that would leave the tenant's prefix."""


def tenant_prefix(tenant_id: UUID) -> str:
    """The listing prefix of one tenant's new-layout objects (trailing slash included)."""
    return f"tenants/{tenant_id}/"


def tenant_prefixes(tenant_id: UUID) -> tuple[str, ...]:
    """Every prefix that can hold one tenant's objects (offboarding deletes all of them): the
    tenant's own tree, the two lifecycle prefixes and the older operator upload quarantine."""
    return (
        tenant_prefix(tenant_id),
        f"quarantine/tenants/{tenant_id}/",
        f"uploads/tenants/{tenant_id}/",
        f"quarantine/{tenant_id}/",
    )


_PREFIX = re.compile(rf"^(tenants/|quarantine/tenants/|uploads/tenants/|quarantine/)?{_UUID}/$")


def is_tenant_prefix(prefix: str) -> bool:
    """True when ``prefix`` is exactly one of the tenant trees ``object_prefixes`` returns (so a
    delete-by-prefix adapter can refuse anything wider: the bucket root, ``tenants/``)."""
    return bool(_PREFIX.match(prefix))


def object_prefixes(tenant_id: UUID) -> tuple[str, ...]:
    """Every prefix a tenant deletion removes: ``tenant_prefixes`` and the delivered files, which
    are stored as ``<tenant>/<account>/<run>/deliverables/<sha>`` (the layout that predates the
    ``tenants/`` tree; ``tenant_of_key`` reads it back the same way)."""
    return (*tenant_prefixes(tenant_id), f"{tenant_id}/")


def tenant_path(tenant_id: UUID, kind: str, *parts: str) -> str:
    """``tenants/<tenant>/<kind>/<parts..>``; refuses unknown kinds and any segment that is
    empty, a dot path, or carries a separator."""
    if kind not in KINDS:
        raise PathRefused(f"unknown storage kind {kind!r}")
    if not parts:
        raise PathRefused("an object key needs at least one segment")
    for part in parts:
        if not _SEGMENT.match(part) or part in (".", ".."):
            raise PathRefused("invalid path segment")
    return "/".join((f"tenants/{tenant_id}", kind, *parts))


def tenant_of_key(key: str) -> UUID | None:
    """The owning tenant of a platform object key, or None when the key has no known layout."""
    if ".." in key.split("/") or "//" in key:
        return None
    for layout in _LAYOUTS:
        m = layout.match(key)
        if m:
            return UUID(m.group("t"))
    return None


def require_owned(tenant_id: UUID, key: str) -> str:
    """The key when it belongs to ``tenant_id``; PathRefused otherwise."""
    if tenant_of_key(key) != tenant_id:
        raise PathRefused("object does not belong to this tenant")
    return key


@dataclass(frozen=True)
class DownloadGrant:
    """What a verified download token allows: one object of one tenant with one hash."""

    tenant_id: UUID
    key: str
    sha256: str
    expires_at: datetime


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_download(
    secret: bytes, tenant_id: UUID, key: str, sha256: str, now: datetime, ttl: timedelta
) -> str:
    """A token for one object of the tenant, valid until ``now + ttl``."""
    require_owned(tenant_id, key)
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise PathRefused("content hash must be a sha256 hex digest")
    if ttl <= timedelta(0):
        raise PathRefused("token lifetime must be positive")
    body = json.dumps(
        {"t": str(tenant_id), "k": key, "h": sha256, "e": int((now + ttl).timestamp())},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    mac = hmac.new(secret, body, hashlib.sha256).digest()
    return f"{_b64(body)}.{_b64(mac)}"


def verify_download(
    secret: bytes, token: str, session_tenant: UUID, now: datetime
) -> DownloadGrant:
    """The grant when the token is authentic, unexpired, and names the SESSION's tenant and a key
    under that tenant; PathRefused otherwise (one error for every failure)."""
    try:
        body_b64, mac_b64 = token.split(".", 1)
        body = _unb64(body_b64)
        expected = hmac.new(secret, body, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(mac_b64)):
            raise PathRefused("bad token")
        claims = json.loads(body)
        tenant = UUID(claims["t"])
        expires = datetime.fromtimestamp(int(claims["e"]), tz=now.tzinfo)
        key, sha = str(claims["k"]), str(claims["h"])
    except (ValueError, KeyError, TypeError) as e:
        raise PathRefused("bad token") from e
    if tenant != session_tenant or tenant_of_key(key) != session_tenant or now >= expires:
        raise PathRefused("bad token")
    return DownloadGrant(tenant, key, sha, expires)
