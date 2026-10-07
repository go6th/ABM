"""HTTP routes. Business endpoints under /v1; operator endpoints under /operator/v1 (fail closed
until the operator plane exists); worker tasks under /tasks; probes unversioned."""

from fastapi import APIRouter, Depends

from account_intelligence.api.guard import authorize, route_guard
from account_intelligence.api.routes import (
    accounts,
    audit,
    connectors,
    exports,
    health,
    ops,
    runs,
    settings,
    spend,
    spend_v2,
    sso,
    tasks,
    users,
    views,
)

# The route catalog is enforced for every route below (default deny, TN2), and every caller is
# authorised against it (catalog roles, membership, CSRF on cookie sessions, step-up).
v1_router = APIRouter(prefix="/v1", dependencies=[Depends(route_guard), Depends(authorize)])
v1_router.include_router(runs.router)
v1_router.include_router(spend.router)
v1_router.include_router(spend_v2.router)
v1_router.include_router(sso.router)
v1_router.include_router(users.router)
v1_router.include_router(settings.router)
v1_router.include_router(settings.branding_router)
v1_router.include_router(audit.router)
v1_router.include_router(accounts.router)
v1_router.include_router(views.router)
v1_router.include_router(exports.router)
v1_router.include_router(connectors.router)
# Anonymous sign-in routes: the catalog guard applies (they are registered ANONYMOUS), but no
# principal is resolved; the tenant comes from the IdP connection the URL names.
public_v1_router = APIRouter(prefix="/v1", dependencies=[Depends(route_guard)])
public_v1_router.include_router(sso.public_router)
operator_router = APIRouter(dependencies=[Depends(route_guard)])
operator_router.include_router(spend_v2.operator_router)
operator_router.include_router(ops.router)

__all__ = [
    "accounts",
    "audit",
    "connectors",
    "exports",
    "views",
    "health",
    "public_v1_router",
    "settings",
    "sso",
    "users",
    "operator_router",
    "ops",
    "runs",
    "spend",
    "spend_v2",
    "tasks",
    "v1_router",
]
