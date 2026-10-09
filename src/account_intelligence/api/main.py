"""ASGI entry point: ``uvicorn account_intelligence.api.main:app``.

The same image serves the API and the worker (Cloud Run services ``api`` and ``worker``). On
startup the container is built from ``APP_ENV`` (``app.container``: the single wiring point) and,
in staging and production, the Cloud SQL connector engine is bound. SIGTERM (Cloud Run sends it
before stopping an instance) flips ``/readyz`` to 503 so no new work is routed here; an in-flight
slice runs to its checkpoint and Cloud Tasks redelivers anything that did not finish (the ledger
replays paid calls).
"""

from __future__ import annotations

import signal
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

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
    from account_intelligence.chat.registry import get_chat_registry
    from account_intelligence.chat.service import AnthropicChatProvider, ChatService

    client = getattr(container.llm, "client", None)
    app.state.chat_service = ChatService(
        get_chat_registry(), AnthropicChatProvider(client) if client is not None else None
    )
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
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)
        if connector is not None:
            await connector.close_async()


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
        """Attach a fresh request id and baseline browser security headers to every response."""
        request_id = str(uuid4())
        request.state.request_id = request_id
        started = time.monotonic()
        response = await call_next(request)
        _observe(request.url.path, response.status_code, time.monotonic() - started)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
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
            },
            status_code=error.status,
            media_type="application/problem+json",
            headers=error.headers or None,
        )

    @app.exception_handler(PlatformNotConfigured)
    async def not_configured(request: Request, _error: PlatformNotConfigured) -> JSONResponse:
        """A production adapter that is not wired: fail closed with 503, never a fake success."""
        request_id = getattr(request.state, "request_id", "unavailable")
        return JSONResponse(
            problem_body(503, request_id),
            status_code=503,
            media_type="application/problem+json",
        )

    @app.exception_handler(Exception)
    async def internal_problem(request: Request, _error: Exception) -> JSONResponse:
        """Return no exception text or traceback to clients."""
        request_id = getattr(request.state, "request_id", "unavailable")
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
