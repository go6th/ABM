"""ASGI entry point: ``uvicorn account_intelligence.api.main:app``.

The same image serves the API and the worker (Cloud Run services ``api`` and ``worker``). On
startup the container is built from ``APP_ENV`` (``app.container``: the single wiring point) and,
in staging and production, the Cloud SQL connector engine is bound. SIGTERM (Cloud Run sends it
before stopping an instance) flips ``/readyz`` to 503 so no new work is routed here; an in-flight
slice runs to its checkpoint and Cloud Tasks redelivers anything that did not finish (the ledger
replays paid calls).
"""

from __future__ import annotations

import os
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from account_intelligence.api.problem import DependencyUnavailable, problem_body
from account_intelligence.api.routes import health, tasks, v1_router


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

    app.state.draining = False
    container = build_container()
    app.state.container = container
    connector = None
    if container.env in ("staging", "production"):
        from account_intelligence.db.session import bind_engine
        from account_intelligence.platform.gcp.adapters import cloud_sql_engine

        engine, connector = await cloud_sql_engine(
            os.environ["CLOUD_SQL_INSTANCE"], os.environ["DB_IAM_USER"], os.environ["DB_NAME"]
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


def create_app() -> FastAPI:
    """The FastAPI application with probes, the /v1 routes and the /tasks worker routes."""
    app = FastAPI(title="Account Intelligence API", lifespan=lifespan)

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Any:
        """Attach a fresh request id and baseline browser security headers to every response."""
        request_id = str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
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

    @app.exception_handler(DependencyUnavailable)
    async def unavailable_problem(request: Request, _error: DependencyUnavailable) -> JSONResponse:
        """Fail closed when a required adapter cannot be reached."""
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
    app.include_router(tasks.router)
    return app


app = create_app()
