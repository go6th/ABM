"""The research file API through the real application (cookie session, CSRF, route catalog):
chunked upload over HTTP, status, audit, analytics, a cited question, delete, and tenant B's 404."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from tests.connectors.conftest import Harness
from tests.research_files import fixtures
from tests.research_files.world import config, services
from tests.system.hermetic_stack import Stack

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


async def test_upload_process_read_ask_delete_over_http(harness: Harness, tmp_path: Path) -> None:
    stack = Stack.build(harness, "happy")
    files = services(tmp_path, cfg=config(upload={"part_bytes": 1024}))
    stack.plane.app.state.container.files = files
    a = await stack.client("files-a")
    b = await stack.client("files-b")
    data = fixtures.csv_bytes([["k", "v"]] + [[str(i), f"value {i}"] for i in range(300)])
    opened = await a.browser.call(
        "POST", "/v1/files/uploads", json={"name": "data.csv", "size": len(data)}
    )
    assert opened.status_code == 201, opened.text
    up = opened.json()
    for n in range(up["parts_total"]):
        part = data[n * up["part_size"] : (n + 1) * up["part_size"]]
        sent = await a.browser.call(
            "PUT",
            f"/v1/files/uploads/{up['upload_id']}/parts/{n}",
            content=part,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert sent.status_code == 200, sent.text
    state = await a.browser.call("GET", f"/v1/files/uploads/{up['upload_id']}")
    assert state.json()["missing"] == []
    done = await a.browser.call("POST", f"/v1/files/uploads/{up['upload_id']}/complete")
    assert done.status_code == 200 and not done.json()["deduplicated"]
    file_id = done.json()["file_id"]
    assert files.scheduler.files == [(a.tenant, UUID(file_id))]  # queued for a worker
    await files.processor().process(a.tenant, UUID(file_id))
    status = (await a.browser.call("GET", f"/v1/files/{file_id}")).json()
    assert status["status"] == "ANALYZED" and status["units_done"] >= 1
    audit = (await a.browser.call("GET", f"/v1/files/{file_id}/audit")).json()
    assert audit["counts"] is not None
    analytics = (await a.browser.call("GET", f"/v1/files/{file_id}/analytics")).json()
    assert analytics["analytics"]["tables"][0]["rows"] == 300
    answer = await a.browser.call("POST", f"/v1/files/{file_id}/ask", json={"question": "value 42"})
    assert answer.status_code == 200 and answer.json()["citations"]
    listed = (await a.browser.call("GET", "/v1/files")).json()["files"]
    assert [f["file_id"] for f in listed] == [file_id]
    # the same bytes again: deduplicated, nothing queued
    again = await a.browser.call(
        "POST", "/v1/files/uploads", json={"name": "copy.csv", "size": len(data)}
    )
    up2 = again.json()
    for n in range(up2["parts_total"]):
        part = data[n * up2["part_size"] : (n + 1) * up2["part_size"]]
        await a.browser.call("PUT", f"/v1/files/uploads/{up2['upload_id']}/parts/{n}", content=part)
    dup = (await a.browser.call("POST", f"/v1/files/uploads/{up2['upload_id']}/complete")).json()
    assert dup == {"file_id": file_id, "status": "ANALYZED", "deduplicated": True}
    assert len(files.scheduler.files) == 1
    # tenant B: every read and action on A's file is "not found"
    for method, path in (
        ("GET", f"/v1/files/{file_id}"),
        ("GET", f"/v1/files/{file_id}/audit"),
        ("GET", f"/v1/files/{file_id}/analytics"),
        ("POST", f"/v1/files/{file_id}/resume"),
        ("DELETE", f"/v1/files/{file_id}"),
    ):
        assert (await b.browser.call(method, path)).status_code == 404, path
    assert (await b.browser.call("GET", "/v1/files")).json()["files"] == []
    gone = await a.browser.call("DELETE", f"/v1/files/{file_id}")
    assert gone.status_code == 204
    assert (await a.browser.call("GET", f"/v1/files/{file_id}")).status_code == 404
    await stack.close(a, b)
