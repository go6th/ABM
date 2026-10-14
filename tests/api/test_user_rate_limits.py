"""Per-user money and compute limits (security audit M4): document uploads, part retries,
resumed research, Download everything and exports each take a token from the caller's own bucket
(client and user from the verified identity), and an empty bucket is a 429 with Retry-After."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from account_intelligence.api.errors import APIProblem
from identity import rate_limit

ROOT = Path(__file__).resolve().parents[2]
ROUTES = ROOT / "src" / "account_intelligence" / "api" / "routes"
LIMITED = {
    ("documents.py", "start_upload"): "document_upload",
    ("documents.py", "retry_part"): "part_retry",
    ("documents.py", "drive_files"): "drive_list",
    ("documents.py", "import_from_drive"): "document_upload",
    ("research.py", "resume_research"): "research_resume",
    ("downloads.py", "start_everything"): "download_everything",
    ("exports.py", "create_export"): "export",
}


def test_every_limited_action_has_reviewed_values() -> None:
    limits = rate_limit.user_limits()
    assert set(LIMITED.values()) <= set(limits)
    assert all(capacity >= 1 and refill > 0 for capacity, refill in limits.values())


def test_per_user_buckets_refuse_past_capacity_per_user_and_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rate_limit, "user_limits", lambda: {"export": (3, 0.001), "x": (1, 0.001)})
    monkeypatch.setattr(rate_limit, "_user_memory", {})
    monkeypatch.setattr(rate_limit, "get_settings", lambda: type("S", (), {"redis_url": None})())
    tenant = uuid4()

    async def run() -> None:
        for _ in range(3):
            await rate_limit.consume_user(tenant, "user-a", "export")
        with pytest.raises(APIProblem) as refused:
            await rate_limit.consume_user(tenant, "user-a", "export")
        assert refused.value.status == 429
        assert int(refused.value.headers["Retry-After"]) >= 1
        # another user of the same client, the same user in another client, another action
        await rate_limit.consume_user(tenant, "user-b", "export")
        await rate_limit.consume_user(uuid4(), "user-a", "export")
        await rate_limit.consume_user(tenant, "user-a", "x")

    asyncio.run(run())


@pytest.mark.parametrize(("module", "handler"), sorted(LIMITED))
def test_each_money_or_compute_route_takes_a_token_first(module: str, handler: str) -> None:
    tree = ast.parse((ROUTES / module).read_text())
    fn = next(
        n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == handler
    )
    calls = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "limit_user"
    ]
    assert len(calls) == 1, handler
    assert isinstance(calls[0].args[1], ast.Constant)
    assert calls[0].args[1].value == LIMITED[(module, handler)]
