"""Replay keys must not drift silently (ADR-0129). SYNTHETIC data only.

A resumed run reuses what its source run paid for only when the keys match: a provider call by
its ledger ``CallKey.args_hash`` (provider, operation, arguments: the search template of
``config/skill/pull_plan.yaml`` and the canonical URL are part of the arguments), a model reply
by ``llm.reply_cache.reply_key`` (task kind, tier and effort, token limit, system prompt, user
text with the run id normalised out). A change to a prompt, a template or a canonicalisation rule
changes the keys, and every run started before it re-pays those calls on resume.

``test_replay_keys_stable`` runs one fixed hermetic company research (the hermetic model and the
hermetic providers, the offline stack of ``test_offline_end_to_end``) and diffs every key against
``tests/golden/replay_keys.json``. The test's own tenant, account and run ids are replaced by
placeholders before hashing (they differ on every test run; in production a resume is always the
same tenant and account, and the run id is already normalised out), so the golden keys are the
production keys of a fixed tenant. The distill and sense-check requests are left out: they list
the run's facts by database id and in id order, so their keys differ on every run (in production
those two replies are never reused across runs either; recorded in ADR-0129, not changed here).
``REPLAY_KEYS_REGEN=1`` rewrites the file: do that only with ``replay_key_version`` bumped in
``config/research_cache.yaml`` and the change recorded in the ADR.

GUARANTEE(test_replay_keys_stable)
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from account_intelligence.connectors import ledger, service
from account_intelligence.connectors.cache import load_research_cache_config
from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import credits as CR
from account_intelligence.replica import jobs
from account_intelligence.runs import lifecycle
from llm import reply_cache
from tests.connectors.conftest import Harness
from tests.system.hermetic_stack import admin
from tests.system.test_offline_end_to_end import offline

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
# requests that list the run's facts by their database ids, in id order: their keys differ on
# every run (never reused across runs in production either), so they cannot drift silently and
# are left out (ADR-0129)
PER_RUN_KINDS = frozenset({"abm_distill", "abm_sense_check"})
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "replay_keys.json"
CHANGED = (
    "replay keys changed: resumed runs started before this commit will re-pay these calls;"
    " bump REPLAY_KEY_VERSION and record it in the ADR"
)


@pytest.fixture(autouse=True)
def _unsigned_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_TASKS", "1")


class Recorder:
    """Every call key and reply key the run computes, with the test's ids normalised."""

    def __init__(self) -> None:
        self.ids: dict[str, str] = {}
        self.calls: set[str] = set()
        self.replies: set[str] = set()

    def scrub(self, value: Any) -> Any:
        text = json.dumps(value, sort_keys=True, default=str)
        for real, placeholder in self.ids.items():
            text = text.replace(real, placeholder)
        # any other id the database made this run: a placeholder, so the golden keys follow the
        # request's shape and text
        return json.loads(UUID_RE.sub("{uuid}", text))

    def call(self, provider_code: str, operation: str, args: Any) -> str:
        # the ledger key of a page ({"args", "cursor"}); the in-flight dedupe key is not one
        if isinstance(args, dict) and set(args) == {"args", "cursor"}:
            key = REAL_ARGS_HASH(provider_code, operation, self.scrub(args))
            self.calls.add(f"{provider_code}:{operation}:{key}")
        return REAL_ARGS_HASH(provider_code, operation, args)

    def reply(self, task_kind: str, tier: str, max_tokens: int, system: str, user: str) -> str:
        if task_kind not in PER_RUN_KINDS:
            key = REAL_REPLY_KEY(task_kind, tier, max_tokens, self.scrub(system), self.scrub(user))
            self.replies.add(f"{task_kind}:{tier}:{key}")
        return REAL_REPLY_KEY(task_kind, tier, max_tokens, system, user)


REAL_ARGS_HASH = ledger.args_hash
REAL_REPLY_KEY = reply_cache.reply_key


async def _record(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    rec = Recorder()
    monkeypatch.setattr(service, "args_hash", rec.call)
    monkeypatch.setattr(reply_cache, "reply_key", rec.reply)
    stack = offline(harness)
    a = await stack.client("replay-keys")
    container = stack.plane.app.state.container
    async with tenant_txn(a.tenant) as s:
        account, _ = await lifecycle.add_account(s, a.tenant, "acme.example", added_by="u1")
        job, _ = await CR.reserve(
            s,
            account_id=account,
            kind="new_research",
            scope="full",
            credits=200,
            actor="u1",
            now=datetime.now(UTC),
        )
    rec.ids = {str(a.tenant): "{tenant}", str(account): "{account}"}
    # the run id is known once the job starts; keys computed before it are id-free
    started = jobs.run_job(a.tenant, job, jobs.GraphEngine(container))
    assert await started == "started"
    (run,) = admin("SELECT run_id FROM research_jobs WHERE id = %s", job)[0]
    rec.ids[str(run)] = "{run}"
    await stack.drain(a, str(run))
    await stack.close(a)
    return {
        "replay_key_version": load_research_cache_config().replay_key_version,
        "call_keys": sorted(rec.calls),
        "reply_keys": sorted(rec.replies),
    }


async def test_replay_keys_stable(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    now = await _record(harness, monkeypatch)
    assert now["call_keys"] and now["reply_keys"]  # the run paid for calls and replies
    if os.environ.get("REPLAY_KEYS_REGEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(now, indent=1, sort_keys=True) + "\n")
    golden = json.loads(GOLDEN.read_text())
    gone = {
        part: sorted(set(golden[part]) - set(now[part])) for part in ("call_keys", "reply_keys")
    }
    new = {part: sorted(set(now[part]) - set(golden[part])) for part in ("call_keys", "reply_keys")}
    assert not any(gone.values()) and not any(new.values()), (
        f"{CHANGED} (version {golden['replay_key_version']} recorded); keys no longer computed:"
        f" {gone}; keys new: {new}"
    )
    assert now["replay_key_version"] == golden["replay_key_version"], (
        "replay_key_version changed without new golden keys: regenerate with REPLAY_KEYS_REGEN=1"
    )
