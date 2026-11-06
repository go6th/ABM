"""Which process serves which routes (fix prompt B, task 1; ADR-0109). The api and the worker run
the same application; ``SERVICE_ROLE`` decides which routers it registers:

* ``api``: the probes, ``/v1/*`` and ``/operator/*``;
* ``worker``: the probes and ``/tasks/*``;
* ``job`` and ``migrate``: the probes only;
* ``all`` (the default, dev and test only; refused in staging and production): everything.

A route this role does not serve does not exist in the process: 404, never 403, so nothing says
which routes exist elsewhere. The catalogue's default deny is untouched."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from account_intelligence.api.main import create_app
from account_intelligence.api.planes import (
    ROLE_PLANES,
    UnknownServiceRole,
    plane_of_path,
    serves,
    service_role,
)


def _app(monkeypatch: pytest.MonkeyPatch, role: str | None, *, accept_tasks: bool) -> FastAPI:
    if role is None:
        monkeypatch.delenv("SERVICE_ROLE", raising=False)
    else:
        monkeypatch.setenv("SERVICE_ROLE", role)
    app = create_app()

    def verify(token: str, audience: str, account: str) -> None:
        if not accept_tasks:
            raise PermissionError("refused")

    # the task routes' OIDC check: a fake that accepts (or refuses) every token
    app.state.container = SimpleNamespace(
        verify_task_token=verify, task_audience="aud", task_service_account="sa", env="test"
    )
    return app


async def _call(app: FastAPI, method: str, path: str, **kw: Any) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="https://app.test") as client:
        return await client.request(method, path, **kw)


@pytest.mark.asyncio
async def test_worker_role_does_not_serve_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(monkeypatch, "worker", accept_tasks=False)
    assert (await _call(app, "GET", "/v1/me")).status_code == 404
    assert (await _call(app, "POST", "/v1/auth/login", json={})).status_code == 404
    assert (await _call(app, "GET", "/operator/v1/session")).status_code == 404
    # its own plane is there: the task route answers the (refused) token check, not 404
    assert (await _call(app, "POST", "/tasks/sweep", json={})).status_code == 403


@pytest.mark.asyncio
async def test_api_role_does_not_serve_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(monkeypatch, "api", accept_tasks=True)  # a valid OIDC token changes nothing
    headers = {"Authorization": "Bearer valid-oidc"}
    for path in ("/tasks/sweep", "/tasks/run-slice", "/tasks/schedules-tick"):
        assert (await _call(app, "POST", path, json={}, headers=headers)).status_code == 404
    assert (await _call(app, "GET", "/v1/me")).status_code != 404


@pytest.mark.asyncio
async def test_dev_role_serves_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(monkeypatch, None, accept_tasks=False)  # unset: the "all" default of dev and test
    assert (await _call(app, "GET", "/v1/me")).status_code != 404
    assert (await _call(app, "POST", "/tasks/sweep", json={})).status_code == 403
    assert (await _call(app, "GET", "/healthz")).status_code != 404


@pytest.mark.parametrize("role", ["api", "worker", "job", "migrate", "all"])
@pytest.mark.asyncio
async def test_every_role_serves_the_probes(monkeypatch: pytest.MonkeyPatch, role: str) -> None:
    app = _app(monkeypatch, role, accept_tasks=False)
    assert (await _call(app, "GET", "/healthz")).status_code != 404


@pytest.mark.asyncio
async def test_job_and_migrate_serve_no_product_route(monkeypatch: pytest.MonkeyPatch) -> None:
    for role in ("job", "migrate"):
        app = _app(monkeypatch, role, accept_tasks=True)
        assert (await _call(app, "GET", "/v1/me")).status_code == 404
        assert (await _call(app, "POST", "/tasks/sweep", json={})).status_code == 404


@pytest.mark.asyncio
async def test_an_unknown_role_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERVICE_ROLE", "everything")
    with pytest.raises(UnknownServiceRole):
        create_app()


def test_paths_map_to_one_plane() -> None:
    assert plane_of_path("/tasks/sweep") == "worker"
    assert plane_of_path("/v1/me") == "api"
    assert plane_of_path("/operator/v1/session") == "api"
    assert plane_of_path("/healthz") == "both"
    assert plane_of_path("/readyz") == "both"
    assert plane_of_path("/elsewhere") is None
    assert plane_of_path("/v1") is None  # a prefix needs its slash: no accidental matches
    assert serves("api", "api") and not serves("api", "worker") and serves("api", "both")
    assert serves("all", "worker") and serves("all", "api")
    assert set(ROLE_PLANES) == {"api", "worker", "job", "migrate", "all"}


def test_the_all_role_is_refused_in_the_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cloud process must say which plane it serves: unset (``all``) refuses to start."""
    for profile in ("staging", "production"):
        with pytest.raises(UnknownServiceRole, match="SERVICE_ROLE"):
            service_role({"APP_ENV": profile})
        with pytest.raises(UnknownServiceRole, match="SERVICE_ROLE"):
            service_role({"APP_ENV": profile, "SERVICE_ROLE": "all"})
        assert service_role({"APP_ENV": profile, "SERVICE_ROLE": "worker"}) == "worker"
        monkeypatch.setenv("APP_ENV", profile)
        monkeypatch.delenv("SERVICE_ROLE", raising=False)
        with pytest.raises(UnknownServiceRole):
            create_app()
    assert service_role({"APP_ENV": "dev"}) == "all"
    assert service_role({"APP_ENV": "test", "SERVICE_ROLE": "all"}) == "all"
    assert service_role({"APP_ENV": "test", "SERVICE_ROLE": " Worker "}) == "worker"


def _checker() -> Any:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import check_route_catalog

    return check_route_catalog


def test_the_catalogue_check_knows_every_routes_plane() -> None:
    assert _checker().plane_problems() == []


def test_the_catalogue_check_refuses_a_route_without_a_plane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from account_intelligence.api import planes

    monkeypatch.setattr(
        planes, "PREFIX_PLANES", tuple(p for p in planes.PREFIX_PLANES if p[0] != "/tasks/")
    )
    found = _checker().plane_problems()
    assert any(line.startswith("route without a plane: POST /tasks/sweep") for line in found)


def test_the_catalogue_check_refuses_a_tasks_route_marked_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from account_intelligence.api import planes

    marked = tuple(
        (prefix, "api" if prefix == "/tasks/" else plane) for prefix, plane in planes.PREFIX_PLANES
    )
    monkeypatch.setattr(planes, "PREFIX_PLANES", marked)
    found = _checker().plane_problems()
    assert "api route served by the worker: POST /tasks/sweep" in found
