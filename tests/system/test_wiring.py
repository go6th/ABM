"""Wiring and runtime guards (integration spec W3, W4, W5 and section 6).

GUARANTEE(test_assert_gcp_runtime_refuses_hermetic_adapters_w4)
GUARANTEE(test_readyz_fails_closed_w5)
GUARANTEE(test_tenant_never_comes_from_a_body_w3)
GUARANTEE(test_every_stage_is_wired_to_a_production_handler)
"""

from __future__ import annotations

import sys
import types
from typing import Any
from uuid import uuid4

import httpx
import pytest

from account_intelligence.app.container import (
    Container,
    RuntimeMisconfigured,
    assert_gcp_runtime,
    build_container,
)

PROD_ENV = {
    "APP_ENV": "production",
    "GCP_PROJECT": "synthetic-project",
    "GCP_REGION": "asia-south1",
    "TASKS_QUEUE": "runs",
    "WORKER_URL": "https://worker.invalid",
    "TASKS_SERVICE_ACCOUNT": "tasks@synthetic-project.iam.gserviceaccount.com",
    "TASKS_AUDIENCE": "https://worker.invalid",
    "PAYLOAD_BUCKET": "synthetic-payloads",
    "CHECKPOINT_DATABASE_URL": "postgresql://app_rw@/ai?host=/cloudsql/x",
    "LLM_ACCESS": "vertex_ai",
}


def _fake_google(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand-ins for the SDK client constructors (they would look up credentials)."""
    from google.cloud import secretmanager, tasks_v2

    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def bucket(self, name: str) -> Any:
            return types.SimpleNamespace(name=name)

    storage_mod = types.ModuleType("google.cloud.storage")
    storage_mod.Client = _Client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google.cloud.storage", storage_mod)
    import google.cloud

    monkeypatch.setattr(google.cloud, "storage", storage_mod, raising=False)
    monkeypatch.setattr(tasks_v2, "CloudTasksClient", _Client)
    monkeypatch.setattr(secretmanager, "SecretManagerServiceClient", _Client)
    import anthropic

    monkeypatch.setattr(anthropic, "AsyncAnthropicVertex", _Client)


def test_assert_gcp_runtime_refuses_hermetic_adapters_w4(monkeypatch: pytest.MonkeyPatch) -> None:
    test = build_container({"APP_ENV": "test"})
    assert_gcp_runtime(test)  # hermetic is fine outside staging and production
    for env in ("staging", "production"):
        forged = Container(**{**test.__dict__, "env": env})
        with pytest.raises(RuntimeMisconfigured, match="hermetic"):
            assert_gcp_runtime(forged)
    with pytest.raises(RuntimeMisconfigured):
        build_container({"APP_ENV": "production"})  # missing GCP settings fail fast
    with pytest.raises(RuntimeMisconfigured):
        build_container({"APP_ENV": "nowhere"})
    _fake_google(monkeypatch)
    prod = build_container(PROD_ENV)
    from account_intelligence.platform.gcp.adapters import CloudTasksScheduler, GcsPayloadStore

    assert isinstance(prod.scheduler, CloudTasksScheduler)
    assert isinstance(prod.payloads, GcsPayloadStore)
    assert prod.verify_task_token is not None
    with pytest.raises(RuntimeMisconfigured, match="LLM_ACCESS"):
        build_container({**PROD_ENV, "LLM_ACCESS": "local_model"})


def _app(container: Any) -> Any:
    from account_intelligence.api.main import create_app

    app = create_app()
    app.state.container = container
    app.state.draining = False
    return app


async def _get(app: Any, path: str, **kw: Any) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.request(kw.pop("method", "GET"), path, **kw)


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_readyz_fails_closed_w5(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(build_container({"APP_ENV": "test"}))
    ok = await _get(app, "/readyz")
    assert ok.status_code == 200 and ok.json()["ready"] is True
    app.state.draining = True
    assert (await _get(app, "/readyz")).status_code == 503  # SIGTERM received
    app.state.draining = False
    from account_intelligence.workflow import spec as spec_mod

    def broken(*a: Any, **k: Any) -> Any:
        raise ValueError("config broken")

    monkeypatch.setattr(spec_mod, "load_spec", broken)
    bad = await _get(app, "/readyz")
    assert bad.status_code == 503 and bad.json()["checks"]["config"].startswith("failed")
    app.state.container = None
    assert (await _get(app, "/readyz")).status_code == 503


@pytest.mark.asyncio
async def test_tenant_never_comes_from_a_body_w3(monkeypatch: pytest.MonkeyPatch) -> None:
    from account_intelligence.api.routes.runs import RunBody

    with pytest.raises(ValueError):
        RunBody.model_validate({"domain": "acme.example", "tenant_id": str(uuid4())})
    app = _app(build_container({"APP_ENV": "test"}))
    body = {"tenant_id": str(uuid4()), "run_id": str(uuid4()), "slice": 0}
    monkeypatch.delenv("ALLOW_UNAUTHENTICATED_TASKS", raising=False)
    denied = await _get(app, "/tasks/run-slice", method="POST", json=body)
    assert denied.status_code == 403  # no task authentication configured: refused
    container = app.state.container

    def reject(token: str, audience: str, email: str) -> None:
        raise PermissionError("bad token")

    container.verify_task_token = reject
    container.task_audience = "aud"
    forged = await _get(
        app,
        "/tasks/run-slice",
        method="POST",
        json=body,
        headers={"Authorization": "Bearer forged"},
    )
    assert forged.status_code == 403
    unauth = await _get(
        app,
        "/v1/runs",
        method="POST",
        json={"domain": "acme.example"},
        headers={"Idempotency-Key": "k-12345678"},
    )
    assert unauth.status_code == 422 or unauth.status_code == 401  # no token: never a run


def test_every_stage_is_wired_to_a_production_handler() -> None:
    from account_intelligence.platform.hermetic import ScriptedLlm
    from account_intelligence.workflow.deps import RunDeps
    from account_intelligence.workflow.engine import APPROVAL_NODE, build_graph
    from account_intelligence.workflow.handlers import build_handlers
    from account_intelligence.workflow.spec import load_spec

    spec = load_spec()
    deps = RunDeps(
        uuid4(),
        uuid4(),
        uuid4(),
        "acme.example",
        None,  # type: ignore[arg-type]
        ScriptedLlm(lambda *a: "{}"),
        None,
        None,
        spec,
    )  # type: ignore[arg-type]
    handlers = build_handlers(deps)
    assert set(handlers) == {s.id for s in spec.stages}
    graph = build_graph(handlers, spec).compile()
    edges = {(e.source, e.target) for e in graph.get_graph().edges}
    chain = [
        "__start__",
        spec.topological_order[0],
        APPROVAL_NODE,
        *spec.topological_order[1:],
        "__end__",
    ]
    assert edges == set(zip(chain, chain[1:], strict=False))  # one sequential path, every stage
