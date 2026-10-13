"""A run exposes what it holds and what is missing through the API, and another tenant sees
nothing of it (hermetic stack, fake model)."""

from __future__ import annotations

import pytest

from tests.connectors.conftest import Harness
from tests.system.hermetic_stack import Stack

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _tasks_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_TASKS", "1")


async def test_a_run_exposes_its_stored_data_and_missing_rows(harness: Harness) -> None:
    stack = Stack.build(harness, "happy")
    a = await stack.client("collected-a")
    b = await stack.client("collected-b")
    run = await stack.start(a)
    status = await a.browser.call("GET", f"/v1/runs/{run}")
    assert status.status_code == 200, status.text
    body = status.json()
    assert set(body["stored"]) == {
        "paid_calls",
        "cached_calls",
        "model_replies",
        "facts",
        "drafts",
        "deliverables",
    }
    assert body["stored"]["paid_calls"] > 0
    assert body["domain"]  # the account, so the client can start a resume
    assert isinstance(body["missing"], list)
    assert all({"row", "status", "reason"} <= set(m) for m in body["missing"])
    assert all(m["status"] not in ("Done", "None") for m in body["missing"])
    collected = await a.browser.call("GET", f"/v1/runs/{run}/collected")
    assert collected.status_code == 200, collected.text
    data = collected.json()
    assert data["status"] == body["status"] and data["stored"] == body["stored"]
    assert data["pulls"] and all(p["calls"] >= 1 for p in data["pulls"])
    assert all("p_" not in p["tool_category"] for p in data["pulls"])  # never a provider code
    assert isinstance(data["facts"], list) and set(data["drafts"]) == {
        "hypotheses",
        "questions",
        "narrative",
    }
    assert data["missing"] == body["missing"]
    text = collected.text.lower()
    for forbidden in ("p_search", "p_scrape", "credit"):
        assert forbidden not in text
    # another tenant: not found, never a body
    assert (await b.browser.call("GET", f"/v1/runs/{run}/collected")).status_code == 404
    assert (await b.browser.call("GET", f"/v1/runs/{run}")).status_code == 404
    await stack.close(a, b)
