"""HTTP routes. Business endpoints under /v1; operator endpoints under /operator/v1 (fail closed
until the operator plane exists); worker tasks under /tasks; probes unversioned."""

from fastapi import APIRouter

from account_intelligence.api.routes import health, runs, spend, spend_v2, tasks

v1_router = APIRouter(prefix="/v1")
v1_router.include_router(runs.router)
v1_router.include_router(spend.router)
v1_router.include_router(spend_v2.router)
operator_router = spend_v2.operator_router

__all__ = ["health", "operator_router", "runs", "spend", "spend_v2", "tasks", "v1_router"]
