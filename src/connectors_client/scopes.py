"""The connector scope table (CN1): which scopes each source may hold, and the CRM object and
field allow-lists. A grant whose data scopes differ from the table (wider, or changed on refresh)
is refused; sign-in scopes (openid, profile, email) are ignored.
``config/connectors.yaml`` (status: proposal, scopes [VERIFY]).

GUARANTEE(test_scopes_are_exactly_the_table_and_crm_fields_are_allow_listed)
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import yaml

from account_intelligence.config import CONFIG_DIR

KINDS = ("salesforce", "hubspot", "call_notes")


@dataclass(frozen=True)
class ConnectorSpec:
    """One source's configuration."""

    kind: str
    enabled: bool
    scopes: frozenset[str]
    authorize_endpoint: str
    grant_kinds: frozenset[str]
    objects: dict[str, frozenset[str]]


@dataclass(frozen=True)
class Limits:
    """Pass and consent bounds."""

    oauth_state_ttl_s: int
    refresh_attempts: int
    max_pages_per_pass: int


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
        )
        for kind, c in raw["connectors"].items()
    }
    if set(specs) != set(KINDS):
        raise ValueError("config/connectors.yaml must describe exactly the known sources")
    return specs, Limits(**{k: int(v) for k, v in raw["limits"].items()})


def spec(kind: str) -> ConnectorSpec:
    """One source's configuration (KeyError for an unknown source)."""
    return load()[0][kind]


# Sign-in scopes an identity platform adds to (or reports with) a grant. They read no source
# data, so a grant may carry them or leave them out.
IDENTITY_SCOPES = frozenset({"openid", "profile", "email"})


def _resource_scopes(scopes: tuple[str, ...] | frozenset[str]) -> frozenset[str]:
    return frozenset(s.strip().lower() for s in scopes if s.strip()) - IDENTITY_SCOPES


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
