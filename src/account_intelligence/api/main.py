"""ASGI entry point: ``uvicorn account_intelligence.api.main:app``."""

from __future__ import annotations

import inngest.fast_api
from fastapi import FastAPI

from account_intelligence.api.domains import (
    UnconfiguredBlockedEntityPort,
    UnconfiguredDomainResolver,
)
from account_intelligence.api.membership import DatabaseMembershipPort, MembershipCache
from account_intelligence.api.problem import install_problem_handlers
from account_intelligence.api.routes import health, v1_router
from account_intelligence.orchestrator.functions import client, run_research


def create_app() -> FastAPI:
    app = FastAPI(title="Account Intelligence API")
    app.state.membership_cache = MembershipCache(DatabaseMembershipPort())
    app.state.domain_resolver = UnconfiguredDomainResolver()
    app.state.blocked_entity_port = UnconfiguredBlockedEntityPort()
    install_problem_handlers(app)
    app.include_router(health.router)
    app.include_router(v1_router)
    inngest.fast_api.serve(app, client, [run_research])  # mounts /api/inngest
    return app


app = create_app()
