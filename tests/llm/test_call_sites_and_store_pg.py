"""R2 static scan of every model call site, and the Postgres call store (R9 replay, isolation)."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import BaseModel

from llm.adapters.fake import ScriptedAdapter
from llm.config import build
from llm.health import PgHealth
from llm.models import Scope
from llm.port import Router
from llm.record import PgCallStore
from llm.tasks import LEGACY_TIER
from tests.llm.conftest import raw_config

ROOT = Path(__file__).resolve().parents[2]
REGISTERED = set(LEGACY_TIER)
# where a task kind is passed: the callable name -> the positional index of the task kind
SITES = {"model": 0, "ask_json": 1, "LlmPostingClassifier": 1, "complete_task": 1}


def test_every_model_call_site_names_a_registered_task_kind() -> None:
    """R2: no module but the routing layer calls a model port directly; every call site passes
    a literal, registered task kind (or forwards its own ``task_kind`` parameter)."""
    direct: list[str] = []
    bad: list[str] = []
    seen = 0
    for path in (ROOT / "src").rglob("*.py"):
        rel = str(path.relative_to(ROOT))
        if "/rag/" in rel:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if (
                name == "complete"
                and isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "llm"
                and rel != "src/account_intelligence/workflow/deps.py"
            ):
                direct.append(f"{rel}:{node.lineno}")
            if name not in SITES or len(node.args) <= SITES[name]:
                continue
            arg = node.args[SITES[name]]
            seen += 1
            if isinstance(arg, ast.Constant):
                if arg.value not in REGISTERED:
                    bad.append(f"{rel}:{node.lineno} {arg.value!r}")
            elif not (isinstance(arg, ast.Name) and arg.id == "task_kind") and not (
                isinstance(arg, ast.Attribute) and arg.attr == "task_kind"
            ):
                bad.append(f"{rel}:{node.lineno} non-literal task kind")
    assert direct == [] and bad == []
    assert seen >= 7  # planner, extractor, judge, lead queries, two posting passes, people


class Out(BaseModel):
    """Synthetic schema."""

    answer: str


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_resumed_call_replays_and_never_pays_twice(tenant_a, tenant_b) -> None:  # type: ignore[no-untyped-def]
    from account_intelligence.db.session import tenant_txn

    cfg = build(raw_config(), b"synthetic")
    run = uuid4()
    adapter = ScriptedAdapter(lambda c, s, u: '{"answer": "stored"}')
    health = PgHealth(
        tenant_txn, tenant_a, cfg.health_threshold, cfg.health_window_s, cfg.health_cooldown_s
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)

    def router(tenant: object) -> Router:
        return Router(
            cfg, {"fake": adapter}, PgCallStore(tenant_txn, tenant, run), health, lambda: now
        )

    sc = Scope(tenant_a, run)
    first = await router(tenant_a).call("extract_facts", "p", "h", {"q": 1}, Out, sc)
    # a new worker after a crash: a fresh router over the same stored rows
    second = await router(tenant_a).call("extract_facts", "p", "h", {"q": 1}, Out, sc)
    assert not first.replayed and second.replayed and len(adapter.calls) == 1
    assert second.record.usage == first.record.usage and second.output == first.output
    assert await PgCallStore(tenant_txn, tenant_a, run).tokens_used() > 0
    # another tenant sees nothing of it (RLS) and would pay for its own call
    assert await PgCallStore(tenant_txn, tenant_b, run).get(first.record.call_key) is None
    await health.record("small", "failure", now)
    await health.record("small", "failure", now)
    assert await health.open_circuits(now) == frozenset({"small"})
    await health.record("small", "success", now)
    assert await health.open_circuits(now) == frozenset()
