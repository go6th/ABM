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

from fastapi import FastAPI

from account_intelligence.api.routes import health, tasks, v1_router


def _on_sigterm(app: FastAPI) -> Any:
    def handler(signum: int, frame: Any) -> None:
        app.state.draining = True

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
    signal.signal(signal.SIGTERM, _on_sigterm(app))
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)
        if connector is not None:
            await connector.close_async()


def create_app() -> FastAPI:
    """The FastAPI application with probes, the /v1 routes and the /tasks worker routes."""
    app = FastAPI(title="Account Intelligence API", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(v1_router)
    app.include_router(tasks.router)
    return app


app = create_app()
