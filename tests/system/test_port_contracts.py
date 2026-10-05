"""One contract suite per platform port, run against the hermetic AND the Google Cloud adapter.

The GCP adapters run against local emulators (fake-gcs-server for Cloud Storage, the Cloud Tasks
emulator), started with ``make emulators``. When an emulator is not reachable those cases are
skipped and say so; nothing here calls a real Google API.
GUARANTEE(test_payload_store_contract) GUARANTEE(test_scheduler_contract)
"""

from __future__ import annotations

import os
import socket
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from account_intelligence.platform.hermetic import InProcessScheduler, MemoryPayloadStore

GCS = os.getenv("GCS_EMULATOR", "localhost:4443")
TASKS = os.getenv("TASKS_EMULATOR", "localhost:8123")
QUEUE = ("dev", "here", "runs")


def _up(address: str) -> bool:
    host, port = address.split(":")
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, int(port))) == 0


def _gcs_store():  # type: ignore[no-untyped-def]
    if not _up(GCS):
        pytest.skip(f"GCS emulator not reachable at {GCS} (run make emulators)")
    os.environ["STORAGE_EMULATOR_HOST"] = f"http://{GCS}"
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import storage

    from account_intelligence.platform.gcp.adapters import GcsPayloadStore

    client = storage.Client(project="dev", credentials=AnonymousCredentials())
    name = f"contract-{uuid4().hex[:8]}"
    client.create_bucket(name)
    return GcsPayloadStore(client, name)


def _tasks():  # type: ignore[no-untyped-def]
    if not _up(TASKS):
        pytest.skip(f"Cloud Tasks emulator not reachable at {TASKS} (run make emulators)")
    import grpc
    from google.cloud import tasks_v2
    from google.cloud.tasks_v2.services.cloud_tasks.transports import CloudTasksGrpcTransport

    from account_intelligence.platform.gcp.adapters import CloudTasksScheduler

    client = tasks_v2.CloudTasksClient(
        transport=CloudTasksGrpcTransport(channel=grpc.insecure_channel(TASKS))
    )
    scheduler = CloudTasksScheduler(
        client, *QUEUE, "http://127.0.0.1:9/worker", "runner@dev.iam.example", "aud"
    )
    return scheduler, client


@pytest.fixture(params=["memory", "gcs"])
def store(request: pytest.FixtureRequest):  # type: ignore[no-untyped-def]
    return MemoryPayloadStore() if request.param == "memory" else _gcs_store()


@pytest.mark.asyncio
async def test_payload_store_contract(store) -> None:  # type: ignore[no-untyped-def]
    key = f"tenants/t/accounts/a/runs/r/raw/{uuid4().hex}.json.gz"
    body = b'{"synthetic": true}'
    assert await store.put(key, body) == key
    assert await store.get(key) == body
    assert await store.put(key, body) == key  # a second put of the same key is harmless
    assert await store.get(key) == body
    with pytest.raises(Exception):  # noqa: B017 - a missing key is an error, never empty bytes
        await store.get(key + ".missing")


@pytest.mark.asyncio
async def test_scheduler_contract_in_process() -> None:
    sched = InProcessScheduler()
    t, r = uuid4(), uuid4()
    await sched.enqueue_slice(t, r, 0)
    await sched.enqueue_slice(t, r, 0)  # duplicate: one task
    await sched.enqueue_slice(t, r, 1)
    assert sched.queued == [(t, r, 0), (t, r, 1)]
    at = datetime.now(UTC) + timedelta(hours=24)
    await sched.schedule_timeout(t, r, at)
    await sched.schedule_timeout(t, r, at + timedelta(hours=1))
    assert sched.timeouts[r] == at  # armed once


@pytest.mark.asyncio
async def test_scheduler_contract() -> None:
    """Cloud Tasks adapter: named tasks, ids-only payload, OIDC token, idempotent enqueue."""
    import json

    from google.cloud import tasks_v2

    scheduler, client = _tasks()
    t, r = uuid4(), uuid4()
    await scheduler.enqueue_slice(t, r, 0, delay_s=3600)
    await scheduler.enqueue_slice(t, r, 0, delay_s=3600)  # AlreadyExists: treated as success
    await scheduler.schedule_timeout(t, r, datetime.now(UTC) + timedelta(hours=24))
    parent = client.queue_path(*QUEUE)
    tasks = [
        x
        for x in client.list_tasks(
            request=tasks_v2.ListTasksRequest(parent=parent, response_view=tasks_v2.Task.View.FULL)
        )
        if str(r) in x.name
    ]
    assert sorted(x.name.rsplit("/", 1)[1] for x in tasks) == [
        f"run-{r}-approval-timeout",
        f"run-{r}-slice-0",
    ]
    slice_task = next(x for x in tasks if x.name.endswith("slice-0"))
    assert json.loads(slice_task.http_request.body) == {
        "run_id": str(r),
        "slice": "0",
        "tenant_id": str(t),
    }
    assert slice_task.http_request.oidc_token.audience == "aud"


def _anthropic(status: int, calls: list[dict[str, object]]):  # type: ignore[no-untyped-def]
    """The real ``anthropic`` SDK over a mock HTTP transport (no network)."""
    import json

    import anthropic
    import httpx2

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        calls.append(body)
        if status != 200:
            return httpx2.Response(
                status,
                json={"type": "error", "error": {"type": "overloaded_error", "message": "x"}},
            )
        return httpx2.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": body["model"],
                "content": [{"type": "text", "text": '{"ok": true}'}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 5,
                    "cache_read_input_tokens": 3,
                    "cache_creation_input_tokens": 0,
                },
            },
        )

    return anthropic.AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ["scripted", "anthropic"])
async def test_llm_port_contract(adapter: str) -> None:
    from account_intelligence.platform.hermetic import ScriptedLlm
    from account_intelligence.platform.llm import AnthropicLlm, load_routes

    calls: list[dict[str, object]] = []
    llm = (
        ScriptedLlm(lambda tier, system, user: '{"ok": true}')
        if adapter == "scripted"
        else AnthropicLlm(_anthropic(200, calls), load_routes(), "anthropic_api")
    )
    result = await llm.complete("extractor", "system text", "user text", max_tokens=64)
    assert result.text == '{"ok": true}' and result.usage.output_tokens >= 0
    if calls:  # the request carries only verified parameters, model from config
        assert sorted(calls[0]) == ["max_tokens", "messages", "model", "system"]
        assert calls[0]["model"] == load_routes()["extractor"].model
        assert result.usage.cache_read_input_tokens == 3


@pytest.mark.asyncio
async def test_llm_sdk_does_not_retry_w6() -> None:
    """W6: the SDK client never retries (max_retries=0); the node RetryPolicy owns retries."""
    import anthropic

    from account_intelligence.platform.llm import AnthropicLlm, load_routes

    calls: list[dict[str, object]] = []
    llm = AnthropicLlm(_anthropic(529, calls), load_routes(), "anthropic_api")
    with pytest.raises(anthropic.APIStatusError):
        await llm.complete("extractor", "s", "u", max_tokens=8)
    assert len(calls) == 1


def test_model_ids_only_in_config_and_judge_check() -> None:
    from account_intelligence.platform.llm import judge_differs_from_generator, load_routes

    routes = load_routes()
    assert set(routes) == {"planner", "extractor", "classifier", "synthesis", "narrative", "judge"}
    # active arm "single" routes every task to one model: the judge is NOT distinct, so the
    # grading layer caps judged claims (G12). Reported as an open decision, not hidden.
    assert judge_differs_from_generator(routes) is False
