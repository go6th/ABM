"""Route catalog check (TN2, AU1): every route has exactly one policy and an audit class.

Fails (exit 1) when a ``/v1`` or ``/operator`` route has no entry in
``account_intelligence.api.policy.ROUTE_POLICIES``, when an entry names no existing route, when a
tenant route takes its tenant from anywhere but the session, when an operator route accepts a
tenant principal, when a route outside the sign-in allow-list is anonymous, or when a route
under ``/v1`` or ``/operator`` declares a ``tenant_id`` parameter in its path, query or body
schema (the tenant comes only from the verified session).

Usage: ``python scripts/check_route_catalog.py``
"""

from __future__ import annotations

import os
import sys
from typing import Any

_ENV_DEFAULTS = {
    "DATABASE_URL": "postgresql+asyncpg://app_rw:unused@localhost:5432/unused",
    "CHECKPOINT_DATABASE_URL": "postgresql://app_rw:unused@localhost:5432/unused",
    "JWKS_URL": "http://unused.invalid/jwks",
    "JWT_AUDIENCE": "unused",
    "JWT_ISSUER": "unused",
}
AUDIT_CLASSES = {"read", "run", "export", "admin", "none"}
# The only routes that may be called without a principal: starting and finishing a sign-in.
ANONYMOUS_ALLOWED = {
    ("GET", "/v1/sso/{slug}/start"),
    ("GET", "/v1/sso/oidc/callback"),
    ("POST", "/v1/sso/saml/acs"),
}


def _schema_names(schema: dict[str, Any], components: dict[str, Any], seen: set[str]) -> set[str]:
    """Every property name reachable from a request body schema."""
    ref = schema.get("$ref")
    if ref:
        name = ref.rsplit("/", 1)[-1]
        if name in seen:
            return set()
        seen.add(name)
        return _schema_names(components.get(name, {}), components, seen)
    names = set(schema.get("properties", {}))
    for sub in (
        list(schema.get("properties", {}).values())
        + schema.get("anyOf", [])
        + schema.get("allOf", [])
    ):
        if isinstance(sub, dict):
            names |= _schema_names(sub, components, seen)
    return names


def problems() -> list[str]:
    """Every catalog violation, as readable lines."""
    for k, v in _ENV_DEFAULTS.items():
        os.environ.setdefault(k, v)
    from account_intelligence.api.main import create_app
    from account_intelligence.api.policy import ROUTE_POLICIES

    spec = create_app().openapi()
    components = spec.get("components", {}).get("schemas", {})
    out: list[str] = []
    seen: set[tuple[str, str]] = set()
    for path, ops in spec["paths"].items():
        if not path.startswith(("/v1/", "/operator/")):
            continue
        for method, op in ops.items():
            if method in ("parameters", "head"):
                continue
            key = (method.upper(), path)
            seen.add(key)
            policy = ROUTE_POLICIES.get(key)
            if policy is None:
                out.append(f"no policy: {method.upper()} {path}")
                continue
            if policy.audit not in AUDIT_CLASSES:
                out.append(f"bad audit class {policy.audit!r}: {method.upper()} {path}")
            operator = path.startswith("/operator/")
            if operator and (
                policy.tenant_source not in ("operator_grant", "operator_target", "none")
                or policy.principal_types != frozenset({"operator_support"})
                or not policy.operator_roles
                or policy.roles
            ):
                out.append(f"operator route must use the staff plane: {method.upper()} {path}")
            if (
                operator
                and policy.tenant_source == "operator_target"
                and policy.operator_roles != {"operator"}
            ):
                out.append(f"only operators may name a tenant: {method.upper()} {path}")
            if not operator and policy.tenant_source not in ("session", "none"):
                out.append(f"tenant route must take its tenant from the session: {path}")
            if not operator and "operator_support" in policy.principal_types:
                out.append(f"tenant route accepts an operator principal: {path}")
            if "anonymous" in policy.principal_types and key not in ANONYMOUS_ALLOWED:
                out.append(f"anonymous route outside the sign-in allow-list: {path}")
            if "anonymous" in policy.principal_types and (policy.roles or policy.audit != "none"):
                out.append(f"anonymous route grants roles or audits reads: {path}")
            params = {p.get("name", "") for p in op.get("parameters", [])}
            body = op.get("requestBody", {}).get("content", {})
            for media in body.values():
                params |= _schema_names(media.get("schema", {}), components, set())
            path_params = {
                p.get("name", "") for p in op.get("parameters", []) if p.get("in") == "path"
            }
            if policy.tenant_source == "operator_target":
                # an operator names the tenant in the path, never in a query or body
                params -= path_params & {"tenant_id"}
                if "tenant_id" not in path_params:
                    out.append(f"operator target route without a tenant path: {path}")
            if {"tenant_id", "tenant", "tenantId"} & {p for p in params}:
                out.append(f"route accepts a tenant field: {method.upper()} {path}")
    for method, path in sorted(set(ROUTE_POLICIES) - seen):
        out.append(f"policy without a route: {method} {path}")
    return out


def main() -> int:
    """Print violations; exit 1 when there are any."""
    found = problems()
    for line in found:
        print(line)
    if not found:
        print("route catalog: every route has a policy, an audit class and a session tenant")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
