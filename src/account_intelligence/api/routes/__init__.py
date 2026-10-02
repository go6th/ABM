"""HTTP routes. Versioned business endpoints mount under /v1; probes stay unversioned."""

from fastapi import APIRouter

from account_intelligence.api.routes import health, runs

v1_router = APIRouter(prefix="/v1")
v1_router.include_router(runs.router)

__all__ = ["health", "runs", "v1_router"]
