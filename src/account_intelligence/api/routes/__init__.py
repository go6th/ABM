"""HTTP routes. Business endpoints under /v1; worker tasks under /tasks; probes unversioned."""

from fastapi import APIRouter

from account_intelligence.api.routes import health, runs, tasks

v1_router = APIRouter(prefix="/v1")
v1_router.include_router(runs.router)

__all__ = ["health", "runs", "tasks", "v1_router"]
