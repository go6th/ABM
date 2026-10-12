"""Pre-pulled upload and cap expiry edges on real Postgres (M8, M9, K3).

An expired intent never gets a signed target and never registers a file; a store that lies about
an object's size is caught on the bytes; the production placeholder store fails closed; a cap
expiry that loses the race to a human decision records nothing.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import UUID

import pytest

from account_intelligence.db.session import tenant_txn
from spend import repo, service, upload
from spend.config import SpendConfig
from spend.errors import NotConfigured
from spend.upload import UploadError
from tests.fakes.spend import COSTS, PLAN, STAGES, FakeBalances
from tests.spend.conftest import NOW, admin, capped_run
from tests.spend.test_connector_gate import OK
from tests.spend.test_v2_db import ATTEST, GOOD, V2, Scanner, Store, cassette, new_intent

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


async def test_the_placeholder_upload_store_never_fakes_a_success() -> None:
    store = upload.UnconfiguredUploadStore()
    for call in (
        store.signed_put("k", 1, "application/json", NOW),
        store.stat("k"),
        store.get("k"),
        store.promote("k", "accepted"),
    ):
        with pytest.raises(NotConfigured):
            await call


async def test_an_expired_intent_gets_no_target_and_registers_nothing(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    _, run, _, intent = await new_intent(tenant_a, cfg)
    expires = admin("SELECT expires_at FROM prepulled_intents WHERE id = %s", intent)[0][0]
    with pytest.raises(UploadError, match="intent_expired"):
        await upload.create_intent_target(tenant_txn, tenant_a, intent, Store(b"{}"), expires, V2)
    store = Store(cassette())
    verdict = await upload.complete(
        tenant_txn,
        tenant_a,
        intent,
        "operator:o1",
        ATTEST,
        store,
        Scanner(True),
        GOOD,
        expires + timedelta(seconds=1),
        V2,
    )
    assert (verdict.accepted, verdict.reason) == (False, "intent_expired")
    assert store.promoted == {}
    assert admin("SELECT count(*) FROM prepulled_files WHERE run_id = %s", run) == [(0,)]


async def test_a_store_that_understates_the_size_is_caught_on_the_bytes(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    """The size the store reports is not trusted: the bytes read are measured again."""

    class Lying(Store):
        async def stat(self, key: str) -> upload.ObjectInfo | None:
            return upload.ObjectInfo(1, self.content_type)

    _, run, _, intent = await new_intent(tenant_a, cfg)
    store = Lying(b"x" * (V2.upload_max_bytes + 1))
    verdict = await upload.complete(
        tenant_txn, tenant_a, intent, "operator:o1", ATTEST, store, Scanner(True), GOOD, NOW, V2
    )
    assert (verdict.accepted, verdict.reason) == (False, "too_large")
    assert store.promoted == {}
    assert admin("SELECT count(*) FROM prepulled_files WHERE run_id = %s", run) == [(0,)]


async def test_a_cap_expiry_that_loses_the_race_to_a_decision_records_nothing(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    """A decision committed while the expiry waits wins; the expiry reports nothing expired and
    the version carries exactly one decision."""
    _, run, _ = capped_run(tenant_a)
    await service.propose(
        tenant_txn, tenant_a, run, PLAN, COSTS, FakeBalances(OK, NOW), cfg, NOW, STAGES
    )
    late = admin("SELECT created_at FROM run_cap_versions WHERE run_id = %s", run)[0][
        0
    ] + timedelta(minutes=cfg.approval_ttl_minutes + 1)
    held = asyncio.Event()
    release = asyncio.Event()

    async def decide_and_hold() -> None:
        async with tenant_txn(tenant_a) as s:
            await repo.add_event(s, run, 1, "rejected", "human:u", "test")
            held.set()
            await release.wait()

    decider = asyncio.create_task(decide_and_hold())
    await asyncio.wait_for(held.wait(), 10)
    expiry = asyncio.create_task(service.expire_pending(tenant_txn, tenant_a, run, cfg, late))
    await asyncio.sleep(0.3)  # the expiry is now blocked on the decision's unique index
    release.set()
    await asyncio.wait_for(decider, 10)
    assert await asyncio.wait_for(expiry, 10) == []
    events = admin("SELECT event FROM run_cap_events WHERE run_id = %s ORDER BY id", run)
    assert [e for (e,) in events if e in ("rejected", "expired", "approved")] == ["rejected"]
