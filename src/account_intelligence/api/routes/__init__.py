"""HTTP routes. Business endpoints under /v1; operator endpoints under /operator/v1 (fail closed
until the operator plane exists); worker tasks under /tasks; probes unversioned."""

from fastapi import APIRouter, Depends

from account_intelligence.api.guard import route_guard
from account_intelligence.api.routes import health, ops, runs, spend, spend_v2, tasks

# The route catalog is enforced for every route below (default deny, TN2).
v1_router = APIRouter(prefix="/v1", dependencies=[Depends(route_guard)])
v1_router.include_router(runs.router)
v1_router.include_router(spend.router)
v1_router.include_router(spend_v2.router)
operator_router = APIRouter(dependencies=[Depends(route_guard)])
operator_router.include_router(spend_v2.operator_router)
operator_router.include_router(ops.router)

__all__ = [
    "health",
    "operator_router",
    "ops",
    "runs",
    "spend",
    "spend_v2",
    "tasks",
    "v1_router",
]
