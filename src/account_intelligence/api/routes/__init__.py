"""HTTP routes. Business endpoints under /v1; operator endpoints under /operator/v1 (fail closed
until the operator plane exists); worker tasks under /tasks; probes unversioned."""

from fastapi import APIRouter, Depends

from account_intelligence.api.guard import authorize, route_guard
from account_intelligence.api.routes import (
    account_workspace,
    accounts,
    admin_console,
    ask,
    audit,
    auth,
    chat,
    connectors,
    credits,
    crm,
    documents,
    downloads,
    exports,
    feedback,
    health,
    internal_view,
    library,
    limits,
    maildrive,
    me,
    model_choice,
    operator_admin,
    operator_view,
    ops,
    research,
    runs,
    schedules,
    settings,
    signals,
    spend,
    spend_v2,
    sso,
    support,
    tasks,
    usage,
    users,
    views,
)

# The route catalog is enforced for every route below (default deny, TN2), and every caller is
# authorised against it (catalog roles, membership, CSRF on cookie sessions, step-up).
v1_router = APIRouter(prefix="/v1", dependencies=[Depends(route_guard), Depends(authorize)])
v1_router.include_router(runs.router)
v1_router.include_router(chat.router)
v1_router.include_router(ask.router)
v1_router.include_router(credits.router)
v1_router.include_router(research.router)
v1_router.include_router(library.router)
v1_router.include_router(maildrive.router)
v1_router.include_router(schedules.router)
v1_router.include_router(me.router)
v1_router.include_router(model_choice.router)
v1_router.include_router(limits.router)
v1_router.include_router(downloads.router)
v1_router.include_router(spend.router)
v1_router.include_router(spend_v2.router)
v1_router.include_router(sso.router)
v1_router.include_router(auth.router)
v1_router.include_router(users.router)
v1_router.include_router(settings.router)
v1_router.include_router(settings.branding_router)
v1_router.include_router(audit.router)
v1_router.include_router(account_workspace.router)
v1_router.include_router(accounts.router)
v1_router.include_router(views.router)
v1_router.include_router(exports.router)
v1_router.include_router(connectors.router)
v1_router.include_router(usage.router)
v1_router.include_router(support.router)
v1_router.include_router(feedback.router)
v1_router.include_router(signals.router)
v1_router.include_router(crm.router)
v1_router.include_router(documents.router)
v1_router.include_router(internal_view.router)
# Anonymous sign-in routes: the catalog guard applies (they are registered ANONYMOUS), but no
# principal is resolved; the tenant comes from the IdP connection the URL names.
public_v1_router = APIRouter(prefix="/v1", dependencies=[Depends(route_guard)])
public_v1_router.include_router(sso.public_router)
public_v1_router.include_router(auth.public_router)
operator_router = APIRouter(dependencies=[Depends(route_guard)])
operator_router.include_router(spend_v2.operator_router)
operator_router.include_router(ops.router)
operator_router.include_router(library.operator_router)
operator_router.include_router(operator_admin.router)
operator_router.include_router(admin_console.router)
operator_router.include_router(operator_view.router)
operator_router.include_router(feedback.operator_router)

__all__ = [
    "accounts",
    "audit",
    "auth",
    "chat",
    "connectors",
    "usage",
    "exports",
    "views",
    "health",
    "me",
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
