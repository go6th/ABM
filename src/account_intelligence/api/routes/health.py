"""Probes. ``/healthz`` is liveness (the process answers). ``/readyz`` is readiness and FAILS
CLOSED (W5): it answers 200 only when every check passes (database reachable under the runtime
role, configuration loads, the runtime assertion holds, the process is not draining after
SIGTERM); any exception or failed check is a 503 that names the failed check, never a 200.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from account_intelligence.api import contract as C

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=C.Health)
async def healthz() -> dict[str, str]:
    """Liveness: the process answers."""
    return {"status": "ok"}


async def _database() -> None:
    from account_intelligence.db.session import _sessions

    async with _sessions()() as session:
        await session.execute(text("SELECT 1"))


def _config() -> None:
    from account_intelligence.config_files import load_all

    load_all()


@router.get("/readyz", responses={200: {"model": C.Ready}})
async def readyz(request: Request) -> JSONResponse:
    """Readiness that fails closed (W5): 200 only when every check passes, else 503 naming it."""
    checks: dict[str, str] = {}
    container: Any = getattr(request.app.state, "container", None)
    if getattr(request.app.state, "draining", False):
        checks["draining"] = "failed: SIGTERM received"
    if container is None:
        checks["container"] = "failed: not built"
    else:
        try:
            from account_intelligence.app.container import assert_gcp_runtime

            assert_gcp_runtime(container)
            checks["runtime"] = "ok"
        except Exception as error:  # noqa: BLE001 - fail closed on anything
            checks["runtime"] = f"failed: {type(error).__name__}"
    try:
        _config()
        checks["config"] = "ok"
    except Exception as error:  # noqa: BLE001
        checks["config"] = f"failed: {type(error).__name__}"
    try:
        await _database()
        checks["database"] = "ok"
    except Exception as error:  # noqa: BLE001
        checks["database"] = f"failed: {type(error).__name__}"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse({"ready": ok, "checks": checks}, status_code=200 if ok else 503)
