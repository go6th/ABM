"""SD-07: readiness shows whether Redis answers, without ever failing on it.

``/readyz`` is the api and worker startup probe, so a Redis outage must not fail it (that would
turn a Redis outage into a platform outage, which SD-07 removes). Redis is pinged with a short
timeout and reported among the informational components: ``ok``, ``unreachable (paid calls
pace in-process)`` or ``not used`` (no REDIS_URL).

GUARANTEE(test_readiness_reports_redis_without_failing_on_it)
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from account_intelligence.api.routes import health


class _Client:
    def __init__(self, behaviour: str) -> None:
        self.behaviour = behaviour

    async def ping(self) -> bool:
        if self.behaviour == "down":
            raise ConnectionError("redis down")
        if self.behaviour == "hang":
            await asyncio.sleep(30)
        return True


@pytest.mark.asyncio
async def test_readiness_reports_redis_without_failing_on_it() -> None:
    env = {"REDIS_URL": "rediss://10.0.0.3:6378/0"}

    def factory(behaviour: str) -> Any:
        return lambda _url, _env: _Client(behaviour)

    assert await health.redis_state(env, factory("up")) == "ok"
    down = await health.redis_state(env, factory("down"))
    assert down == "unreachable (paid calls pace in-process)"
    hung = await asyncio.wait_for(health.redis_state(env, factory("hang"), timeout_s=0.05), 2)
    assert hung == down
    assert await health.redis_state({}, factory("up")) == "not used"
