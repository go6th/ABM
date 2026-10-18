"""Probes. ``/healthz`` is liveness (the process answers). ``/readyz`` is readiness and FAILS
CLOSED (W5): it answers 200 only when every check passes (database reachable under the runtime
role, configuration loads, the runtime assertion holds, the process is not draining after
SIGTERM); any exception or failed check is a 503 that names the failed check, never a 200.
"""

from __future__ import annotations

import os
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


def components(container: Any | None, env: Any) -> dict[str, str]:
    """Report dependency readiness using status words only; never return names or credentials."""
    from account_intelligence.connectors.factory import provider_configured
    from account_intelligence.connectors.settings import load_providers_config
    from account_intelligence.platform.hermetic import UnconfiguredLlm

    providers = load_providers_config().providers
    report = {
        "payload_store": "present"
        if getattr(container, "payloads", None) is not None
        else "absent",
        "rate_limit_store": "shared" if env.get("REDIS_HOST") else "local",
        "llm": "present"
        if getattr(container, "llm", None) is not None
        and not isinstance(getattr(container, "llm", None), UnconfiguredLlm)
        else "absent",
    }
    connectors_on = getattr(container, "connectors", None) is not None
    for code, config in providers.items():
        suffix = code.value.removeprefix("p_")
        report[f"provider.{suffix}"] = (
            "off"
            if not connectors_on
            else "configured"
            if provider_configured(config, env)
            else "unconfigured"
        )
    return report


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
        component_status = components(container, os.environ)
    except Exception as error:  # noqa: BLE001 - readiness fails closed on config drift
        checks["components"] = f"failed: {type(error).__name__}"
        component_status = {"status": "unavailable"}
    try:
        await _database()
        checks["database"] = "ok"
    except Exception as error:  # noqa: BLE001
        checks["database"] = f"failed: {type(error).__name__}"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"ready": ok, "checks": checks, "components": component_status},
        status_code=200 if ok else 503,
    )
