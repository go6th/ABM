"""Probes. ``/healthz`` is liveness (the process answers). ``/readyz`` is readiness and FAILS
CLOSED (W5): it answers 200 only when every check passes (database reachable under the runtime
role, the schema is at the code's revision or later, configuration loads, the runtime assertion
holds, the process is not draining after SIGTERM); any exception or failed check is a 503 that
names the failed check, never a 200.
``components`` (informational, never a reason for 503) reports each external component by a
status word only: each provider ``off``, ``configured`` or ``unconfigured`` (the request path's
own rule), the model ``present``, ``fake (LLM_FAKE)`` or ``not configured (ANTHROPIC_API_KEY)``
(the missing key's name, never a value), the payload, rate-limit and scanner adapters.
No variable's value ever appears.

GUARANTEE(test_readiness_reports_every_component_by_status_word_only)
"""

from __future__ import annotations

import os
from collections.abc import Mapping
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


# The schema revision this code needs (the newest migration it was written with). Readiness
# fails while the database is older: a revision rolled out before its migration job finished
# never takes traffic. GUARANTEE(test_readiness_needs_the_schema_revision_of_the_code)
EXPECTED_REVISION = "0093"


async def _database() -> None:
    from account_intelligence.db.session import _sessions

    async with _sessions()() as session:
        await session.execute(text("SELECT 1"))


async def _migrations() -> str:
    """``ok`` when the database's revision is at or after ``EXPECTED_REVISION``."""
    from account_intelligence.db.session import _sessions

    async with _sessions()() as session:
        found = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    return revision_check(found)


def revision_check(found: object) -> str:
    """Compare the database revision with the code's (revisions are zero-padded numbers)."""
    if not isinstance(found, str) or not found.isdigit():
        return "failed: no schema revision"
    if int(found) < int(EXPECTED_REVISION):
        return f"failed: schema {found} is older than {EXPECTED_REVISION}"
    return "ok"


def _config() -> None:
    from account_intelligence.config_files import load_all

    load_all()


def components(container: Any, env: Mapping[str, str]) -> dict[str, str]:
    """Each external component by a status word (see the module docstring)."""
    from account_intelligence.connectors.base import ProviderCode
    from account_intelligence.connectors.factory import provider_configured
    from account_intelligence.connectors.settings import load_providers_config

    out: dict[str, str] = {}
    providers = load_providers_config()
    on = getattr(container, "connectors", None) is not None
    for code in ProviderCode:
        try:
            cfg = providers.provider(code)
        except (KeyError, LookupError):
            continue
        state = "configured" if provider_configured(cfg, env) else "unconfigured"
        cloud_api = env.get("SERVICE_ROLE") == "api" and env.get("APP_ENV") in CLOUD_PROFILES
        if cloud_api and code is not ProviderCode.P_PEOPLE:
            # provider keys are mounted on the worker only (least privilege): the api cannot see
            # them, so it names where they are checked instead of reporting "unconfigured"
            state = "checked on the worker"
        elif env.get("CONNECTORS") == "hermetic":
            state = "hermetic (synthetic replies, no outside call)"
        elif code is ProviderCode.P_PEOPLE:
            # no documented people-search API: it runs a grid the owner builds (FIX_LOG D11)
            state = PEOPLE_DISABLED
        out[f"provider.{code.value.removeprefix('p_')}"] = state if on else "off"
    out["llm"] = llm_state(getattr(container, "llm", None))
    payloads = getattr(container, "payloads", None)
    out["payload_store"] = "absent" if payloads is None else _adapter(payloads, "PayloadStore")
    out["rate_limit_store"] = "redis" if env.get("REDIS_URL") else "in_process"
    try:
        from account_intelligence.replica.jobs import engine_state

        out["research_engine"] = engine_state(str(getattr(container, "env", "dev")))
    except RuntimeError:
        out["research_engine"] = "misconfigured (RESEARCH_ENGINE)"
    documents = getattr(container, "documents", None)
    scanner = getattr(documents, "scanner", None) if documents is not None else None
    out["document_scanner"] = str(getattr(scanner, "engine", "none")) if scanner else "none"
    return out


LLM_MISSING = "not configured (ANTHROPIC_API_KEY)"
CLOUD_PROFILES = ("staging", "production")
PEOPLE_DISABLED = (
    "disabled (needs the owner's people-search grid; people rows use the org source and web search)"
)


def llm_state(llm: Any) -> str:
    """The model in plain words: ``present`` (a real model), ``fake (LLM_FAKE)`` (the hermetic
    model, dev and test only) or ``not configured (ANTHROPIC_API_KEY)`` (names the missing key)."""
    name = type(llm).__name__
    if name in ("NoneType", "UnconfiguredLlm"):
        return LLM_MISSING
    if name == "FakeModel":
        return "fake (LLM_FAKE)"
    return "present"


def _adapter(value: Any, suffix: str) -> str:
    name = type(value).__name__.removesuffix(suffix).lower()
    return name or "present"


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
    except Exception as error:  # noqa: BLE001 - fail closed on anything, naming the class only
        checks["database"] = f"failed: {type(error).__name__}"
    if checks["database"] == "ok":
        try:
            checks["migrations"] = await _migrations()
        except Exception as error:  # noqa: BLE001 - fail closed on anything, naming the class only
            checks["migrations"] = f"failed: {type(error).__name__}"
    ok = all(v == "ok" for v in checks.values())
    body: dict[str, Any] = {"ready": ok, "checks": checks}
    if container is not None:
        try:
            body["components"] = components(container, os.environ)
        except Exception:  # noqa: BLE001 - informational only; never changes readiness
            body["components"] = {}
    return JSONResponse(body, status_code=200 if ok else 503)
