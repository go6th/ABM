"""HTTP routes. Business endpoints under /v1; worker tasks under /tasks; probes unversioned."""

from fastapi import APIRouter

from account_intelligence.api.routes import health, runs, spend, tasks

v1_router = APIRouter(prefix="/v1")
v1_router.include_router(runs.router)
v1_router.include_router(spend.router)

__all__ = ["health", "runs", "spend", "tasks", "v1_router"]
