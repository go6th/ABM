"""The model choice through the real routes (owner decision 2026-10-14): the menus with their
reasons, remembered preferences, Kimi K3 refused until the client is allowed it, and what a run
and an answer record. The fake keys only make the models "set up"; nothing calls a provider."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import psycopg
import pytest

from llm.choice import catalogue
from tests.conftest import ADMIN_DSN
from tests.replica.test_workspace_api import _call, _plane

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
OPUS, FABLE, KIMI = catalogue().ids


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-key")
    monkeypatch.setenv("MOONSHOT_API_KEY", "test-not-a-key")


def _allow(tenant: Any, models: list[str] | None) -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute(
            "UPDATE client_settings SET allowed_models = %s WHERE tenant_id = %s", (models, tenant)
        )


async def test_the_menus_grey_out_what_cannot_be_chosen(
    world: dict[str, Any], keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    plane = _plane(world["a"], f"menu-{uuid4().hex[:6]}", world["payloads"])
    got = (await _call(plane, "GET", "/v1/models/choices")).json()
    assert (got["research"], got["analysis"]) == (OPUS, FABLE)
    by_id = {m["id"]: m for m in got["models"]}
    assert by_id[OPUS]["available"] and by_id[OPUS]["line"] == "Most capable, slower"
    # every enabled model is allowed by default (owner decision 2026-10-15)
    assert by_id[KIMI]["available"] and by_id[KIMI]["reason"] is None
    _allow(world["a"], [OPUS, FABLE])  # the admin takes Kimi K3 off for this client
    try:
        got = (await _call(plane, "GET", "/v1/models/choices")).json()
        kimi = {m["id"]: m for m in got["models"]}[KIMI]
        assert (kimi["available"], kimi["reason"]) == (False, "Not enabled for your workspace")
        monkeypatch.delenv("MOONSHOT_API_KEY")
        _allow(world["a"], [OPUS, FABLE, KIMI])
        got = (await _call(plane, "GET", "/v1/models/choices")).json()
        assert {m["id"]: m["reason"] for m in got["models"]}[KIMI] == "Not set up"
    finally:
        _allow(world["a"], None)


async def test_preferences_are_remembered_and_checked(world: dict[str, Any], keys: None) -> None:
    user = f"pref-{uuid4().hex[:6]}"
    plane = _plane(world["a"], user, world["payloads"])
    _allow(world["a"], [OPUS, FABLE])
    try:
        refused = await _call(
            plane, "PUT", "/v1/me/model-preferences", json={"research_model": KIMI}
        )
        assert refused.status_code == 422 and "Not enabled for your workspace" in refused.text
    finally:
        _allow(world["a"], None)
    try:
        saved = await _call(plane, "PUT", "/v1/me/model-preferences", json={"research_model": KIMI})
        assert saved.status_code == 200, saved.text
        assert (saved.json()["research"], saved.json()["analysis"]) == (KIMI, FABLE)
        # private to the user: a colleague still has the defaults
        other = _plane(world["a"], f"pref-{uuid4().hex[:6]}", world["payloads"])
        assert (await _call(other, "GET", "/v1/models/choices")).json()["research"] == OPUS
    finally:
        _allow(world["a"], [OPUS, FABLE])
    try:
        # once the admin withdraws Kimi K3, the user quietly falls back to the default
        assert (await _call(plane, "GET", "/v1/models/choices")).json()["research"] == OPUS
    finally:
        _allow(world["a"], None)


async def test_ask_refuses_a_model_before_it_starts(world: dict[str, Any], keys: None) -> None:
    plane = _plane(world["a"], "priya-test", world["payloads"])
    body = {"request_id": str(uuid4()), "question": "What changed?", "model": KIMI}
    _allow(world["a"], [OPUS, FABLE])  # a client whose admin took Kimi K3 off
    try:
        got = await _call(plane, "POST", "/v1/chat/ask", json=body)
    finally:
        _allow(world["a"], None)
    assert got.status_code == 422 and "Not enabled for your workspace" in got.text


async def test_a_confirmed_refresh_records_the_chosen_models(
    world: dict[str, Any], keys: None
) -> None:
    plane = _plane(world["a"], "priya-test", world["payloads"])
    view = (await _call(plane, "GET", "/v1/accounts/medica.example/refresh")).json()
    full = next(o for o in view["options"] if o["scope"] == "full")
    _allow(world["a"], [OPUS, FABLE])  # a client whose admin took Kimi K3 off
    try:
        refused = await _call(
            plane,
            "POST",
            "/v1/accounts/medica.example/refresh",
            json={"request_id": str(uuid4()), "scope": "full", "credits": full["credits"],
                  "research_model": KIMI},
        )  # fmt: skip
    finally:
        _allow(world["a"], None)
    assert refused.status_code == 422
    ok = await _call(
        plane,
        "POST",
        "/v1/accounts/medica.example/refresh",
        json={"request_id": str(uuid4()), "scope": "full", "credits": full["credits"],
              "research_model": FABLE, "analysis_model": OPUS},
    )  # fmt: skip
    assert ok.status_code == 201, ok.text
    with psycopg.connect(ADMIN_DSN) as c:
        models = c.execute(
            "SELECT models FROM research_jobs WHERE id = %s", (ok.json()["job_id"],)
        ).fetchone()[0]
    assert (models["research"], models["analysis"]) == (FABLE, OPUS)
    assert json.dumps(models)  # stored as plain JSON for the run
