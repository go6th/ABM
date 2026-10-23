"""The connector scope table (CN1): which scopes each source may hold, and the CRM object and
field allow-lists. A grant whose data scopes differ from the table (wider, or changed on refresh)
is refused; sign-in scopes (openid, profile, email, offline_access, User.Read) are ignored.
``config/connectors.yaml`` (status: proposal, scopes [VERIFY]).

GUARANTEE(test_scopes_are_exactly_the_table_and_crm_fields_are_allow_listed)
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import yaml

from account_intelligence.config import CONFIG_DIR

KINDS = (
    "google_drive",
    "salesforce",
    "hubspot",
    "gmail",
    "call_notes",
    "outlook",
    "onedrive",
    "sharepoint",
)


@dataclass(frozen=True)
class ConnectorSpec:
    """One source's configuration."""

    kind: str
    enabled: bool
    scopes: frozenset[str]
    authorize_endpoint: str
    grant_kinds: frozenset[str]
    objects: dict[str, frozenset[str]]
    # Mail and drive sources: "mail" or "drive", the OAuth provider and its token endpoints
    family: str | None = None
    provider: str | None = None
    token_endpoint: str | None = None
    revoke_endpoint: str | None = None


@dataclass(frozen=True)
class Limits:
    """Pass and consent bounds."""

    oauth_state_ttl_s: int
    refresh_attempts: int
    max_pages_per_pass: int
    max_items_per_pass: int
    max_bytes_per_item: int
    max_ancestry_depth: int
    max_folder_depth: int


@lru_cache
def load() -> tuple[dict[str, ConnectorSpec], Limits]:
    """The connector table and limits (read once)."""
    raw: dict[str, Any] = yaml.safe_load((CONFIG_DIR / "connectors.yaml").read_text())
    specs = {
        kind: ConnectorSpec(
            kind=kind,
            enabled=bool(c["enabled"]),
            scopes=frozenset(c["scopes"]),
            authorize_endpoint=str(c["authorize_endpoint"]),
            grant_kinds=frozenset(c.get("grant_kinds") or ()),
            objects={o: frozenset(f) for o, f in (c.get("objects") or {}).items()},
            family=c.get("family"),
            provider=c.get("provider"),
            token_endpoint=c.get("token_endpoint"),
            revoke_endpoint=c.get("revoke_endpoint"),
        )
        for kind, c in raw["connectors"].items()
    }
    if set(specs) != set(KINDS):
        raise ValueError("config/connectors.yaml must describe exactly the known sources")
    return specs, Limits(**{k: int(v) for k, v in raw["limits"].items()})


def spec(kind: str) -> ConnectorSpec:
    """One source's configuration (KeyError for an unknown source)."""
    return load()[0][kind]


# Sign-in scopes an identity platform adds to (or reports with) a grant. They read no mail and
# no files, so a grant may carry them or leave them out; ``offline_access`` is Microsoft's
# refresh-token scope (the refresh token's presence is checked separately).
IDENTITY_SCOPES = frozenset(
    {
        "openid",
        "profile",
        "email",
        "offline_access",
        "user.read",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    }
)
_GRAPH = "https://graph.microsoft.com/"


def normalise_scope(scope: str) -> str:
    """One scope in a comparable form: Microsoft returns Graph scopes either as full URLs
    (``https://graph.microsoft.com/Mail.Read``) or short names, case-insensitively; Google
    scopes are URLs. Lower-cased, with the Graph resource prefix removed."""
    value = scope.strip().lower()
    return value[len(_GRAPH) :] if value.startswith(_GRAPH) else value


def _resource_scopes(scopes: tuple[str, ...] | frozenset[str]) -> frozenset[str]:
    return frozenset(normalise_scope(s) for s in scopes if s.strip()) - IDENTITY_SCOPES


def scopes_ok(kind: str, granted: tuple[str, ...] | frozenset[str]) -> bool:
    """True when every scope the table requires is granted and nothing wider is: the granted
    scopes, normalised and without sign-in scopes (``IDENTITY_SCOPES``), are exactly the table's
    (a wider or other data scope is refused)."""
    return _resource_scopes(granted) == _resource_scopes(spec(kind).scopes)


def allowed_fields(
    kind: str, obj: str, granted_objects: set[str], granted_fields: set[str]
) -> tuple[str, ...]:
    """The fields of ``obj`` that both the table and the tenant admin allowed (empty: none)."""
    table = spec(kind).objects.get(obj)
    if table is None or obj not in granted_objects:
        return ()
    return tuple(sorted(f for f in table if f"{obj}.{f}" in granted_fields))
