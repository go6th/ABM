"""The Research screen's data, live (``replica.live``).

* ``GET /v1/research/live``: the snapshot once (the page's first paint, and the fallback when a
  stream cannot be opened). ``?selected=<job or run id>`` picks the research shown in detail.
* ``GET /v1/research/live/stream``: server-sent events. The snapshot is read again every
  ``live.poll_s`` (one second) and sent as a ``snapshot`` event only when it changed, with a
  heartbeat while nothing changes. A stream lasts at most ``live.max_duration_s`` (the membership
  cache bound): the browser reconnects within a second (``retry``) and is authorised again. With
  no research running it sends one snapshot and ends, and the browser asks again after
  ``live.idle_retry_s`` (an open stream would keep an instance awake for nothing).

Each read is its own short tenant transaction (forced RLS); no connection is held while waiting.

GUARANTEE(test_live_stream_sends_a_new_snapshot_when_a_fact_is_stored)
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from account_intelligence.api import contract as C
from account_intelligence.api.auth import Identity
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import live
from obs.logging import logger

_log = logger(__name__)

router = APIRouter(prefix="/research", tags=["research"])
register("GET", "/v1/research/live", tenant_route("read_public", "read", step_up=False))
register("GET", "/v1/research/live/stream", tenant_route("read_public", "read", step_up=False))


def _selected(value: str | None) -> UUID | None:
    """The selected research's job or run id; empty is none, anything else must be an id."""
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(422, "selected must be a research id") from None


async def _read(request: Request, ident: Identity, selected: UUID | None) -> dict[str, Any]:
    from agents.config import skill_config

    container = request.app.state.container
    spec = container.spec
    async with tenant_txn(ident.tenant_id) as s:
        return await live.snapshot(
            s,
            ident.tenant_id,
            stages=[(st.id, st.name) for st in spec.stages],
            owners=spec.coverage_owners,
            labels=skill_config().row_labels,
            selected=selected,
            cfg=live.load_settings(),
            now=datetime.now(UTC),
        )


@router.get("/live", response_model=C.ResearchLive)
async def research_live(
    request: Request,
    selected: str | None = Query(default=None, max_length=36),
    ident: Identity = Depends(authorize),
) -> dict[str, Any]:
    """Every web research running now and finished recently, and the selected one in detail."""
    body = await _read(request, ident, _selected(selected))
    return C.ResearchLive.model_validate(body).model_dump(mode="json")


@router.get("/live/stream")
async def research_live_stream(
    request: Request,
    selected: str | None = Query(default=None, max_length=36),
    ident: Identity = Depends(authorize),
) -> StreamingResponse:
    """The snapshot as server-sent events (see the module docstring)."""
    cfg = live.load_settings()
    picked = _selected(selected)
    first = C.ResearchLive.model_validate(await _read(request, ident, picked))

    async def stream() -> AsyncIterator[bytes]:
        """Snapshots when they change, heartbeats between, within the duration cap."""
        # the browser waits this long before reconnecting after the stream ends
        yield f"retry: {int(cfg.poll_s * 1000)}\n\n".encode()
        body: C.ResearchLive | None = first
        last: str | None = None
        started = beat = time.monotonic()
        # LOOP(research live stream): cap=live.max_duration_s, polled every live.poll_s;
        # exit=max duration, client gone
        while time.monotonic() - started < cfg.max_duration_s:
            if body is not None:
                data = body.model_dump(mode="json")
                tag = live.version(data)
                if tag != last:
                    payload = json.dumps(data, separators=(",", ":"))
                    yield f"id: {tag}\nevent: snapshot\ndata: {payload}\n\n".encode()
                    last, beat = tag, time.monotonic()
                if not body.running:
                    # nothing to follow: ask again later instead of holding the stream open
                    yield f"retry: {int(cfg.idle_retry_s * 1000)}\n\n".encode()
                    return
                elif time.monotonic() - beat >= cfg.heartbeat_s:
                    yield b": heartbeat\n\n"
                    beat = time.monotonic()
            if await request.is_disconnected():
                return
            await asyncio.sleep(cfg.poll_s)
            try:
                body = C.ResearchLive.model_validate(await _read(request, ident, picked))
            except Exception:  # noqa: BLE001 - one failed read keeps the stream; the next retries
                _log.warning("research_live.read_failed", exc_info=True)
                body = None
        yield b"event: reconnect\ndata: {}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
