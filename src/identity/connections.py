"""Tenant IdP connections: the one writer of ``idp_connections`` (W1).

Create, update (conditional on ``version``) and list the context tenant's connections. A client
secret is never stored here, only the ``SecretStore`` reference to it, and never read back out:
listings say only whether one is set.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

COLUMNS = (
    "id, slug, protocol, issuer, client_id, authorization_endpoint, token_endpoint, jwks_uri,"
    " saml_sso_url, sp_entity_id, verified_domains, group_role_map, jit_enabled,"
    " require_admin_mfa, status, version, client_secret_ref IS NOT NULL AS has_client_secret"
)


class ConnectionConflict(RuntimeError):
    """A duplicate slug, or a version that changed since it was read."""


def as_json(row: Any) -> dict[str, Any]:
    """A connection row as client-safe JSON (no secret reference, no certificate body)."""
    out = dict(row._mapping)
    out["id"] = str(out["id"])
    return out


async def listing(s: AsyncSession) -> list[dict[str, Any]]:
    """The context tenant's connections."""
    sql = text(f"SELECT {COLUMNS} FROM idp_connections ORDER BY slug")  # noqa: S608
    return [as_json(r) for r in (await s.execute(sql)).all()]


async def create(s: AsyncSession, values: dict[str, Any], secret_ref: str | None) -> dict[str, Any]:
    """Insert a connection; ConnectionConflict on a duplicate slug."""
    params = {**values, "ref": secret_ref, "group_role_map": json.dumps(values["group_role_map"])}
    try:
        async with s.begin_nested():
            row = (
                await s.execute(
                    text(
                        "INSERT INTO idp_connections (slug, protocol, issuer, client_id,"
                        " client_secret_ref, authorization_endpoint, token_endpoint, jwks_uri,"
                        " saml_sso_url, saml_idp_cert_pem, sp_entity_id, verified_domains,"
                        " group_role_map, jit_enabled, require_admin_mfa) VALUES (:slug,"
                        " :protocol, :issuer, :client_id, :ref, :authorization_endpoint,"
                        " :token_endpoint, :jwks_uri, :saml_sso_url, :saml_idp_cert_pem,"
                        " :sp_entity_id, :verified_domains, CAST(:group_role_map AS jsonb),"
                        f" :jit_enabled, :require_admin_mfa) RETURNING {COLUMNS}"
                    ),
                    params,
                )
            ).first()
    except IntegrityError:
        raise ConnectionConflict("connection could not be created") from None
    return as_json(row)


async def update(
    s: AsyncSession,
    connection_id: UUID,
    values: dict[str, Any],
    secret_ref: str | None,
    expected_version: int,
) -> dict[str, Any]:
    """Update a connection at the version it was read; a new secret reference replaces the old
    one (the old secret is not read). ConnectionConflict when the version moved."""
    params = {
        **values,
        "ref": secret_ref,
        "id": connection_id,
        "v": expected_version,
        "group_role_map": json.dumps(values["group_role_map"]),
    }
    try:
        async with s.begin_nested():
            row = (
                await s.execute(
                    text(
                        "UPDATE idp_connections SET slug = :slug, protocol = :protocol,"
                        " issuer = :issuer, client_id = :client_id,"
                        " client_secret_ref = COALESCE(:ref, client_secret_ref),"
                        " authorization_endpoint = :authorization_endpoint,"
                        " token_endpoint = :token_endpoint, jwks_uri = :jwks_uri,"
                        " saml_sso_url = :saml_sso_url, saml_idp_cert_pem = :saml_idp_cert_pem,"
                        " sp_entity_id = :sp_entity_id, verified_domains = :verified_domains,"
                        " group_role_map = CAST(:group_role_map AS jsonb),"
                        " jit_enabled = :jit_enabled, require_admin_mfa = :require_admin_mfa,"
                        " status = :status, version = version + 1, updated_at = now()"
                        f" WHERE id = :id AND version = :v RETURNING {COLUMNS}"
                    ),
                    params,
                )
            ).first()
    except IntegrityError:
        raise ConnectionConflict("connection could not be updated") from None
    if row is None:
        raise ConnectionConflict("connection changed or not found")
    return as_json(row)
