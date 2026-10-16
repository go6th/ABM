"""ASGI entry point: ``uvicorn account_intelligence.api.main:app``.

The same image serves the API and the worker (Cloud Run services ``api`` and ``worker``). On
startup the container is built from ``APP_ENV`` (``app.container``: the single wiring point) and,
in staging and production, the Cloud SQL connector engine is bound. SIGTERM (Cloud Run sends it
before stopping an instance) flips ``/readyz`` to 503 so no new work is routed here; an in-flight
slice runs to its checkpoint and Cloud Tasks redelivers anything that did not finish (the ledger
replays paid calls).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from account_intelligence.api.errors import APIProblem
from account_intelligence.api.problem import DependencyUnavailable, problem_body
from account_intelligence.api.routes import (
    health,
    operator_router,
    public_v1_router,
    tasks,
    v1_router,
)
from account_intelligence.platform.ports import NotConfigured as PlatformNotConfigured
from account_intelligence.platform.ports import PayloadMissing
from account_intelligence.replica.settings import AccountCapReached
from obs.logging import logger

_logger = logger(__name__)


def _on_sigterm(app: FastAPI, previous: Any) -> Any:
    """SIGTERM: flip /readyz to 503 (no new work), then hand over to the server's own graceful
    shutdown (uvicorn finishes in-flight requests and exits)."""

    def handler(signum: int, frame: Any) -> None:
        """Mark draining, then call the previous handler so the process still exits."""
        app.state.draining = True
        if callable(previous):
            previous(signum, frame)

    return handler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the container, bind the Cloud SQL engine in the cloud, install the SIGTERM drain."""
    from account_intelligence.app.container import build_container
    from account_intelligence.config import load_settings

    # fail fast: the whole environment is validated before anything is wired (names only)
    settings = load_settings()
    from obs.logging import configure_logging

    configure_logging(settings.log_level, service=settings.service_role)
    if settings.metrics_export == "cloud_monitoring" and settings.gcp_project:
        from obs import export

        export.install(settings.gcp_project, f"ai-{settings.service_role}")
    app.state.draining = False
    container = build_container()
    app.state.container = container
    if container.env in ("staging", "production") and settings.service_role == "api":
        from account_intelligence.api.tenant_plane import assert_signing_keys, plane_of

        await assert_signing_keys(plane_of(SimpleNamespace(app=app)))
    from account_intelligence.replica import jobs as research_jobs

    research_jobs.configure(container.scheduler, research_jobs.engine_for(container))
    from account_intelligence.chat.registry import get_chat_registry
    from account_intelligence.chat.service import AnthropicChatProvider, ChatService

    # Fail closed: with no model client the chat routes answer 503. Never substitute canned
    # text; an answer without a source must not be shown as account intelligence.
    client = getattr(container.llm, "client", None)
    provider = AnthropicChatProvider(client) if client is not None else None
    app.state.chat_service = ChatService(get_chat_registry(), provider)
    # each Ask model's provider (owner decision 2026-10-14): Kimi K3 streams from Moonshot
    from account_intelligence.chat.service import MoonshotChatProvider, ProviderByModel

    moonshot = getattr(container.llm, "moonshot", None)
    app.state.chat_providers = ProviderByModel(
        {
            **({"anthropic": provider} if provider is not None else {}),
            **({"moonshot": MoonshotChatProvider(moonshot)} if moonshot is not None else {}),
        }
    )
    from account_intelligence.chat.credentials import TenantModelKeyStore

    app.state.tenant_model_keys = TenantModelKeyStore(settings.byok_kms_key)
    from account_intelligence.db.session import bind_cloud_engine

    connector = await bind_cloud_engine(settings)
    previous = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _on_sigterm(app, previous))
    sweeper = (
        asyncio.create_task(_sweep_loop(app, settings))
        if settings.service_role == "worker" and settings.run_sweeper == "on"
        else None
    )
    try:
        yield
    finally:
        if sweeper is not None:
            sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper
        signal.signal(signal.SIGTERM, previous)
        from identity.rate_limit import close as close_identity_rate_limiter

        await close_identity_rate_limiter()
        if connector is not None:
            await connector.close_async()


async def sweep_once(app: FastAPI, now: Any | None = None) -> Any:
    """One orphan sweep with the container's wiring (``runs.sweeper.sweep``)."""
    from datetime import UTC, datetime

    from account_intelligence.db.session import session_factory, tenant_txn
    from account_intelligence.grading.config import load_grading_config
    from account_intelligence.runs.sweeper import sweep
    from account_intelligence.tracker.adapters import LedgerCallAdapter, RunInputAdapter
    from account_intelligence.tracker.repo import CoverageRepo
    from account_intelligence.tracker.service import TrackerService

    container = app.state.container
    settings = __import__("account_intelligence.config", fromlist=["load_settings"]).load_settings()

    def tracker_for(s: Any) -> TrackerService:
        """The tracker on one tenant transaction."""
        return TrackerService(
            CoverageRepo(s),
            LedgerCallAdapter(s, load_grading_config().category_by_operation),
            RunInputAdapter(s),
            owners=container.spec.coverage_owners,
        )

    return await sweep(
        session_factory(),
        tenant_txn,
        tracker_for,
        now or datetime.now(UTC),
        orphan_after_s=float(settings.orphan_after_s),
    )


async def _sweep_documents(app: FastAPI) -> None:
    """Uploaded documents whose worker died mid-scan, mid-parse or mid-index are enqueued again."""
    from datetime import UTC, datetime

    from account_intelligence.db.session import session_factory
    from documents.config import load_config as load_documents_config
    from documents.service import sweep_documents

    container = app.state.container
    if getattr(container, "documents", None) is None:
        return
    await sweep_documents(
        session_factory(),
        container.scheduler,
        datetime.now(UTC),
        orphan_after_s=load_documents_config().parsing.orphan_after_s,
    )
    # documents indexed before the deliverable import existed get their rows read, a few per
    # pass (documents.deliverables, 2026-10-16)
    from documents.deliverables import backfill_imports

    done = await backfill_imports(datetime.now(UTC))
    if done["documents"]:
        _logger.info(
            "documents.import_backfilled",
            extra={"count": done["facts"], "rows": done["documents"]},
        )


async def sweep_pass(app: FastAPI) -> None:
    """One pass of the worker's sweeper: documents, orphaned runs, stalled runs. Each step is
    independent; a failing step is logged and the next one still runs."""
    try:
        await _sweep_documents(app)
    except Exception as error:  # noqa: BLE001 - one bad pass never stops the sweeper
        _logger.warning("documents.sweep_failed", extra={"error_class": type(error).__name__})
    try:
        result = await sweep_once(app)
        if result.partial or result.failed:
            _logger.info(
                "runs.swept",
                extra={
                    "partial": len(result.partial),
                    "failed": len(result.failed),
                    "truncated": result.truncated,
                },
            )
    except Exception as error:  # noqa: BLE001 - the sweeper must outlive one bad pass
        _logger.warning("runs.sweep_failed", extra={"error_class": type(error).__name__})
    try:
        stalled = await _mark_stalled()
        if stalled:
            # the stalled-runs alert counts this event (infra observability: runs_stalled)
            _logger.warning("runs.stalled", extra={"count": len(stalled)})
    except Exception as error:  # noqa: BLE001 - the sweeper must outlive one bad pass
        _logger.warning("runs.stall_failed", extra={"error_class": type(error).__name__})
    try:
        resumed = await _auto_resume(app)
        if resumed["continued"]:
            _logger.info("runs.auto_resumed", extra={"count": resumed["continued"]})
    except Exception as error:  # noqa: BLE001 - the sweeper must outlive one bad pass
        _logger.warning("runs.auto_resume_failed", extra={"error_class": type(error).__name__})
    try:
        closed = await _close_ended_jobs()
        if closed:
            _logger.info("jobs.closed", extra={"count": closed})
    except Exception as error:  # noqa: BLE001 - the sweeper must outlive one bad pass
        _logger.warning("jobs.close_failed", extra={"error_class": type(error).__name__})


async def _close_ended_jobs(limit: int = 100) -> int:
    """Jobs still marked queued or running whose run has already ended are settled (seen on
    staging 2026-10-15: the account kept showing "Refreshing from the web" and the job held its
    reserved credits). ``settle_once`` is idempotent, so a job its run settles meanwhile is
    left as it is."""
    from sqlalchemy import text

    from account_intelligence.db.session import tenant_txn, untenanted_session
    from account_intelligence.runs.lifecycle import settle_once

    ended = "('COMPLETED', 'PARTIAL', 'BUDGET_EXCEEDED', 'FAILED', 'CANCELED')"
    async with untenanted_session() as plain:
        tenants = [
            UUID(str(r.tenant_id))
            for r in await plain.execute(text("SELECT tenant_id FROM operator_tenant_overview()"))
        ]
    closed = 0
    # LOOP(close ended jobs): cap=every client once per pass, `limit` jobs each; exit=last client
    for tenant in tenants:
        async with tenant_txn(tenant) as s:
            runs = [
                UUID(str(r.run_id))
                for r in await s.execute(
                    text(
                        "SELECT j.run_id FROM research_jobs j"
                        " JOIN account_runs r ON r.id = j.run_id"
                        " WHERE j.status IN ('queued', 'running') AND r.status IN "
                        + ended
                        + " LIMIT :n"
                    ),
                    {"n": limit},
                )
            ]
        for run_id in runs:
            try:
                async with tenant_txn(tenant) as s:
                    await settle_once(s, run_id)
                closed += 1
            except Exception as error:  # noqa: BLE001 - one job never stops the others
                _logger.warning("jobs.close_failed", extra={"error_class": type(error).__name__})
    return closed


async def _sweep_loop(app: FastAPI, settings: Any) -> None:
    """Startup sweep, then one every ``sweep_interval_s``; a failing pass is logged, never fatal.
    A system job (private workspaces, migration 0063): it sees every user's orphans."""
    from account_intelligence.db.session import set_system

    set_system()  # this task's own context only
    # LOOP(orphan sweep loop): cap=none, one pass per interval for the life of the worker;
    # exit=task cancelled at shutdown
    while True:
        await sweep_pass(app)
        await asyncio.sleep(max(1, int(settings.sweep_interval_s)))


async def _auto_resume(app: FastAPI) -> dict[str, int]:
    """Continue the research that stalled or lost its worker, the way Resume does, within the
    platform's bounds (``replica.auto_resume``; end-to-end automation, 2026-10-16)."""
    from datetime import UTC, datetime

    from account_intelligence.replica.auto_resume import auto_resume_pass

    owners = app.state.container.spec.coverage_owners
    return await auto_resume_pass(owners, datetime.now(UTC))


async def _mark_stalled() -> list[Any]:
    """Runs with no progress for the platform's stall time are marked and their owners told."""
    from datetime import UTC, datetime

    from account_intelligence.db.session import session_factory, tenant_txn
    from account_intelligence.runs.sweeper import mark_stalled

    return await mark_stalled(session_factory(), tenant_txn, datetime.now(UTC))


def _route_class(path: str) -> str:
    """A bounded route class (never the raw path: cardinality, O5)."""
    for prefix, name in (
        ("/v1/runs", "v1_runs"),
        ("/v1/", "v1_other"),
        ("/operator/", "operator"),
        ("/tasks/", "tasks"),
    ):
        if path.startswith(prefix):
            return name
    return "probe"


def _observe(path: str, status: int, seconds: float) -> None:
    """API request metrics by route and code class (never raises, O1)."""
    from obs.setup import record

    code = "429" if status == 429 else f"{status // 100}xx"
    route = _route_class(path)
    record("api_requests_total", 1, {"route_class": route, "code_class": code})
    record("api_latency_ms", seconds * 1000, {"route_class": route})


PROBES = frozenset({"/healthz", "/readyz"})


def _route_template(request: Request) -> str:
    """The matched route's template (``/v1/accounts/{domain}``), never the raw path: paths carry
    account domains and ids, and the query string carries download and upload tokens."""
    from account_intelligence.api.guard import route_template

    return route_template(request) or "unmatched"


def _log_request(request: Request, status: int, seconds: float, request_id: str) -> None:
    """The API's own request log through the allow-list logger (security audit L10): method,
    route template, status, duration and request id. No query string, header or body is ever
    logged, so uvicorn's access log can be switched off (``--no-access-log``). Probes are left
    out (Cloud Run calls them every few seconds)."""
    if request.url.path in PROBES:
        return
    _logger.info(
        "http.request",
        extra={
            "method": request.method,
            "route": _route_template(request),
            "http_status": status,
            "latency_ms": round(seconds * 1000, 1),
            "request_id": request_id,
        },
    )


def _is_https(request: Request) -> bool:
    """Served over HTTPS: directly, or behind Cloud Run's front end (``X-Forwarded-Proto``)."""
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or forwarded == "https"


def security_headers(headers: Any, *, https: bool) -> None:
    """Headers on every API response. The API serves JSON only: nothing may load, frame or cache
    it (``Cache-Control: no-store`` on every response, authenticated or not); HSTS when the request
    came over HTTPS. The nonce CSP for HTML pages is the web BFF's.

    GUARANTEE(test_every_api_response_carries_the_security_headers)"""
    headers["X-Content-Type-Options"] = "nosniff"
    headers["X-Frame-Options"] = "DENY"
    headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    headers["Referrer-Policy"] = "no-referrer"
    headers["Cache-Control"] = "no-store"
    if https:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"


def create_app() -> FastAPI:
    """The FastAPI application with probes, the /v1 routes and the /tasks worker routes."""
    # L12: no interactive docs or schema route in the cloud profiles (the API is internal-only
    # there; scripts/export_openapi.py calls app.openapi() directly, so the snapshot is the same)
    cloud = os.environ.get("APP_ENV", "dev") in ("staging", "production")
    app = FastAPI(
        title="Account Intelligence API",
        lifespan=lifespan,
        docs_url=None if cloud else "/docs",
        redoc_url=None if cloud else "/redoc",
        openapi_url=None if cloud else "/openapi.json",
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Any:
        """Keep the BFF's UUID request id or create one for direct API clients."""
        supplied_id = request.headers.get("x-request-id", "")
        try:
            parsed_id = uuid4() if not supplied_id else UUID(supplied_id)
            request_id = str(parsed_id) if supplied_id.lower() == str(parsed_id) else str(uuid4())
        except ValueError:
            request_id = str(uuid4())
        request.state.request_id = request_id
        from obs.logging import REQUEST_ID

        token = REQUEST_ID.set(request_id)
        started = time.monotonic()
        try:
            response = await call_next(request)
        finally:
            REQUEST_ID.reset(token)
        elapsed = time.monotonic() - started
        _observe(request.url.path, response.status_code, elapsed)
        _log_request(request, response.status_code, elapsed, request_id)
        response.headers["X-Request-ID"] = request_id
        security_headers(response.headers, https=_is_https(request))
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_problem(request: Request, error: StarletteHTTPException) -> JSONResponse:
        """Map framework HTTP errors to the stable problem-details contract."""
        request_id = getattr(request.state, "request_id", "unavailable")
        detail = error.detail if isinstance(error.detail, str) else None
        return JSONResponse(
            problem_body(error.status_code, request_id, detail),
            status_code=error.status_code,
            media_type="application/problem+json",
            headers={**error.headers} if error.headers else None,
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        """422 as a problem document: field locations and messages only. The submitted values are
        never echoed (they may be sensitive, and non-standard JSON such as Infinity cannot be
        serialised back, which used to turn a bad request into a 500)."""
        request_id = getattr(request.state, "request_id", "unavailable")
        errors = [
            {
                "loc": [str(p) for p in e.get("loc", ())],
                "msg": str(e.get("msg", "")),
                "type": str(e.get("type", "")),
            }
            for e in error.errors()
        ]
        return JSONResponse(
            {**problem_body(422, request_id, "invalid request"), "errors": errors[:50]},
            status_code=422,
            media_type="application/problem+json",
        )

    @app.exception_handler(PayloadMissing)
    async def payload_missing(request: Request, _error: PayloadMissing) -> JSONResponse:
        """A stored source object is gone (retention, deletion, lost write): 409, never a 500."""
        request_id = getattr(request.state, "request_id", "unavailable")
        return JSONResponse(
            {
                "type": "urn:6th:problem:source_missing",
                "title": "Source data missing",
                "status": 409,
                "detail": "A stored source for this run is no longer available.",
                "request_id": request_id,
            },
            status_code=409,
            media_type="application/problem+json",
        )

    @app.exception_handler(DependencyUnavailable)
    async def unavailable_problem(request: Request, _error: DependencyUnavailable) -> JSONResponse:
        """Fail closed when a required adapter cannot be reached."""
        request_id = getattr(request.state, "request_id", "unavailable")
        return JSONResponse(
            problem_body(503, request_id),
            status_code=503,
            media_type="application/problem+json",
        )

    @app.exception_handler(APIProblem)
    async def typed_problem(request: Request, error: APIProblem) -> JSONResponse:
        """An expected problem with its own stable type, title and safe detail."""
        request_id = getattr(request.state, "request_id", "unavailable")
        return JSONResponse(
            {
                "type": f"urn:6th:problem:{error.type}",
                "title": error.title,
                "status": error.status,
                "detail": error.detail,
                "request_id": request_id,
                **({"context": error.extra} if error.extra else {}),
            },
            status_code=error.status,
            media_type="application/problem+json",
            headers=error.headers or None,
        )

    @app.exception_handler(AccountCapReached)
    async def account_cap(request: Request, error: AccountCapReached) -> JSONResponse:
        """Every way of adding an account refuses past the user's cap with the design's words."""
        request_id = getattr(request.state, "request_id", "unavailable")
        return JSONResponse(
            {
                "type": "urn:6th:problem:account_cap_reached",
                "title": "Account cap reached",
                "status": 409,
                "detail": error.message,
                "request_id": request_id,
                "context": {"cap": error.cap},
            },
            status_code=409,
            media_type="application/problem+json",
        )

    @app.exception_handler(PlatformNotConfigured)
    async def not_configured(request: Request, error: PlatformNotConfigured) -> JSONResponse:
        """A production adapter that is not wired: fail closed with 503, never a fake success."""
        request_id = getattr(request.state, "request_id", "unavailable")
        _logger.error(
            "not configured: %s",
            error,
            extra={"request_id": request_id, "error_class": "NotConfigured"},
        )
        return JSONResponse(
            problem_body(503, request_id),
            status_code=503,
            media_type="application/problem+json",
        )

    @app.exception_handler(Exception)
    async def internal_problem(request: Request, _error: Exception) -> JSONResponse:
        """Return no exception text or traceback to clients."""
        request_id = getattr(request.state, "request_id", "unavailable")
        _logger.exception("unhandled request exception", extra={"request_id": request_id})
        return JSONResponse(
            problem_body(500, request_id),
            status_code=500,
            media_type="application/problem+json",
        )

    app.include_router(health.router)
    app.include_router(v1_router)
    app.include_router(public_v1_router)
    app.include_router(operator_router)
    app.include_router(tasks.router)
    return app


app = create_app()
