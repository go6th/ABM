"""The connector scope table (CN1): which scopes each source may hold, and the CRM object and
field allow-lists. A grant whose scopes differ from the table (wider, or changed on refresh) is
refused. ``config/connectors.yaml`` (status: proposal, scopes [VERIFY]).

GUARANTEE(test_scopes_are_exactly_the_table_and_crm_fields_are_allow_listed)
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import yaml

from account_intelligence.config import CONFIG_DIR

KINDS = ("google_drive", "salesforce", "hubspot", "gmail", "call_notes")


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
        )
        for kind, c in raw["connectors"].items()
    }
    if set(specs) != set(KINDS):
        raise ValueError("config/connectors.yaml must describe exactly the known sources")
    return specs, Limits(**{k: int(v) for k, v in raw["limits"].items()})


def spec(kind: str) -> ConnectorSpec:
    """One source's configuration (KeyError for an unknown source)."""
    return load()[0][kind]


def scopes_ok(kind: str, granted: tuple[str, ...] | frozenset[str]) -> bool:
    """True only when the granted scopes are exactly the table's (never wider, never other)."""
    return frozenset(granted) == spec(kind).scopes


def allowed_fields(
    kind: str, obj: str, granted_objects: set[str], granted_fields: set[str]
) -> tuple[str, ...]:
    """The fields of ``obj`` that both the table and the tenant admin allowed (empty: none)."""
    table = spec(kind).objects.get(obj)
    if table is None or obj not in granted_objects:
        return ()
    return tuple(sorted(f for f in table if f"{obj}.{f}" in granted_fields))
