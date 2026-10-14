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
import signal
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
    app.state.draining = False
    container = build_container()
    app.state.container = container
    from account_intelligence.replica import jobs as research_jobs

    research_jobs.configure(container.scheduler, research_jobs.engine_from_env(container.env))
    from account_intelligence.chat.registry import get_chat_registry
    from account_intelligence.chat.service import AnthropicChatProvider, ChatService

    # Fail closed: with no model client the chat routes answer 503. Never substitute canned
    # text; an answer without a source must not be shown as account intelligence.
    client = getattr(container.llm, "client", None)
    provider = AnthropicChatProvider(client) if client is not None else None
    app.state.chat_service = ChatService(get_chat_registry(), provider)
    from account_intelligence.chat.credentials import TenantModelKeyStore

    app.state.tenant_model_keys = TenantModelKeyStore(settings.byok_kms_key)
    connector = None
    if container.env in ("staging", "production"):
        from account_intelligence.db.session import bind_engine
        from account_intelligence.platform.gcp.adapters import cloud_sql_engine

        assert settings.cloud_sql_instance and settings.db_iam_user and settings.db_name
        engine, connector = await cloud_sql_engine(
            settings.cloud_sql_instance, settings.db_iam_user, settings.db_name
        )
        bind_engine(engine)
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

    from account_intelligence.db.session import SessionLocal, tenant_txn
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
        SessionLocal,
        tenant_txn,
        tracker_for,
        now or datetime.now(UTC),
        orphan_after_s=float(settings.orphan_after_s),
    )


async def _sweep_files(app: FastAPI, settings: Any) -> None:
    """Research files whose worker died end PARTIAL ``worker_lost`` (resume continues them)."""
    from datetime import UTC, datetime

    from account_intelligence.db.session import SessionLocal, tenant_txn
    from research_files.config import load_config
    from research_files.pipeline import sweep_files

    if getattr(app.state.container, "files", None) is None:
        return
    await sweep_files(
        SessionLocal,
        tenant_txn,
        datetime.now(UTC),
        orphan_after_s=load_config().parsing.orphan_after_s,
    )


async def _sweep_documents(app: FastAPI) -> None:
    """Uploaded documents whose worker died mid-scan, mid-parse or mid-index are enqueued again."""
    from datetime import UTC, datetime

    from account_intelligence.db.session import SessionLocal
    from documents.config import load_config as load_documents_config
    from documents.service import sweep_documents

    container = app.state.container
    if getattr(container, "documents", None) is None:
        return
    await sweep_documents(
        SessionLocal,
        container.scheduler,
        datetime.now(UTC),
        orphan_after_s=load_documents_config().parsing.orphan_after_s,
    )


async def _sweep_loop(app: FastAPI, settings: Any) -> None:
    """Startup sweep, then one every ``sweep_interval_s``; a failing pass is logged, never fatal."""
    # LOOP(orphan sweep loop): cap=none, one pass per interval for the life of the worker;
    # exit=task cancelled at shutdown
    while True:
        try:
            await _sweep_files(app, settings)
        except Exception as error:  # noqa: BLE001 - one bad pass never stops the sweeper
            _logger.warning("files.sweep_failed", extra={"error_class": type(error).__name__})
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
        await asyncio.sleep(max(1, int(settings.sweep_interval_s)))


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


def create_app() -> FastAPI:
    """The FastAPI application with probes, the /v1 routes and the /tasks worker routes."""
    app = FastAPI(title="Account Intelligence API", lifespan=lifespan)

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
        _observe(request.url.path, response.status_code, time.monotonic() - started)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        # JSON only: nothing may load or frame. The nonce CSP for HTML is set by the web BFF.
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
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
