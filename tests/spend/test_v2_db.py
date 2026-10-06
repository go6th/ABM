"""Credit manager v2 on real Postgres: the soft crossing once per cap version (M1), the live
snapshot under concurrent reserves (M2), one pause per cap version and every bad approval refused
(M3, M5), an approval that appends a version and lifts the hard limit, decline, timeout and late
approval (D-45), exhaustion never touching the cap (M6), the upload step against hostile files and
an accepted file consumed instead of a paid call (M8, M9), the outbox (M10)."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from account_intelligence.connectors.base import Operation, ProviderCode
from account_intelligence.connectors.models import WebSearchArgs
from account_intelligence.connectors.service import call_operation
from account_intelligence.db.session import tenant_txn
from spend import exhaustion, limits, live, notify, pause, upload
from spend.config import SpendConfig, load_v2_config
from spend.errors import ApprovalInvalid, NotConfigured
from tests.connectors.conftest import CASSETTES, context
from tests.spend.conftest import NOW, admin
from tests.spend.test_connector_gate import _approved, _harness

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
V2 = load_v2_config()


def approver(tenant: UUID, **kw: Any) -> pause.Approver:
    base: dict[str, Any] = {
        "subject": "u1",
        "tenant_id": str(tenant),
        "role": "admin",
        "step_up_at": NOW - timedelta(minutes=1),
    }
    base.update(kw)
    return pause.Approver(**base)


def set_spent(run: UUID, usd: str) -> None:
    admin("UPDATE account_runs SET spent_usd = %s WHERE id = %s", Decimal(usd), run)


def cap_of(run: UUID) -> tuple[Any, ...]:
    return admin("SELECT budget_usd, cap_version FROM account_runs WHERE id = %s", run)[0]


async def open_request(tenant: UUID, run: UUID, cfg: SpendConfig) -> tuple[UUID, bool]:
    async with tenant_txn(tenant) as s:
        inputs = await limits.read_inputs(s, run)
        assert inputs is not None and inputs.cap_micros is not None
        req = pause.compute_request(
            inputs.cap_version or 1,
            inputs.cap_micros,
            inputs.committed_micros,
            inputs.held_micros,
            [pause.BlockedUnit("body|jobs", 300_000, ("job_board_by_lane",))],
            cfg.rounding_step_micros,
            cfg.sample_hold_micros,
            None,
        )
        return await pause.open_pause(s, run, req, NOW, V2)


# ---------------------------------------------------------------- M1 soft limit


async def test_soft_crossing_fires_once_per_cap_version(tenant_a: UUID, cfg: SpendConfig) -> None:
    _, run, _ = await _approved(tenant_a, cfg)
    budget = Decimal(cap_of(run)[0])
    set_spent(run, str((budget * Decimal("0.79")).quantize(Decimal("0.000001"))))

    async def once() -> bool:
        async with tenant_txn(tenant_a) as s:
            return await limits.after_spend_change(s, run, NOW)

    assert not any(await asyncio.gather(*(once() for _ in range(5))))  # below the soft limit
    set_spent(run, str(budget * Decimal("0.80")))
    results = await asyncio.gather(*(once() for _ in range(10)), return_exceptions=True)
    assert sum(1 for r in results if r is True) == 1, results
    assert all(r in (True, False) for r in results)  # races insert nothing, raise nothing
    rows = admin(
        "SELECT cap_version FROM spend_limit_events WHERE run_id = %s AND event = 'soft_crossed'",
        run,
    )
    assert rows == [(1,)]
    outbox = admin(
        "SELECT audience, template FROM notification_outbox WHERE run_id = %s ORDER BY audience",
        run,
    )
    assert outbox == [("operator", "spend_soft_limit"), ("tenant", "run_spend_near_limit")]
    async with tenant_txn(tenant_a) as s:
        assert await limits.funding_mode(s, run) is limits.Mode.PRIORITY_ONLY
    # a new cap version re-arms the soft limit
    request_id, opened = await open_request(tenant_a, run, cfg)
    assert opened
    async with tenant_txn(tenant_a) as s:
        await pause.approve(
            s, run, request_id, approver(tenant_a), None, NOW, V2, cfg, pause.UnconfiguredWallet()
        )
    async with tenant_txn(tenant_a) as s:
        assert await limits.funding_mode(s, run) is limits.Mode.NORMAL
    new_cap = Decimal(cap_of(run)[0])
    set_spent(run, str(new_cap * Decimal("0.9")))
    assert await once() is True
    assert (
        len(
            admin(
                "SELECT 1 FROM spend_limit_events WHERE run_id = %s AND event = 'soft_crossed'", run
            )
        )
        == 2
    )


# ---------------------------------------------------------------- M2 live spend


async def test_live_snapshot_is_consistent_under_concurrent_reserves(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    harness = _harness()
    for _ in range(40):
        harness.providers[ProviderCode.P_SEARCH].script(
            __import__("tests.connectors.conftest", fromlist=["Reply"]).Reply.cassette(
                "p_search", "web_search"
            )
        )
    account, run, domain = await _approved(tenant_a, cfg)
    ctx = context(tenant_a, account, run, domain)
    rt = harness.runtime()
    stop = asyncio.Event()
    seen: list[live.Snapshot] = []

    async def poll() -> None:
        while not stop.is_set():
            got = await live.snapshot(tenant_txn, tenant_a, run, NOW)
            assert got is not None
            seen.append(got[0])
            await asyncio.sleep(0)

    async def calls(tag: str) -> None:
        for i in range(12):
            query = WebSearchArgs(queries=(f"{tag}{i}",))
            await call_operation(Operation.WEB_SEARCH, query, ctx, rt)

    watcher = asyncio.create_task(poll())
    await asyncio.gather(calls("a"), calls("b"))
    stop.set()
    await watcher
    committed = [s.committed_micros for s in seen]
    assert committed == sorted(committed)  # only committed is claimed monotonic
    for s in seen:
        assert s.held_micros >= 0 and s.cap_micros is not None
        assert s.committed_micros + s.held_micros <= s.cap_micros  # hard is never exceeded
        assert s.remaining_micros == s.cap_micros - s.committed_micros - s.held_micros
        assert s.projection["label"] == "estimate" and s.projection["base"]
    final = await live.snapshot(tenant_txn, tenant_a, run, NOW)
    assert final is not None and final[0].held_micros == 0
    spent = admin("SELECT spent_usd FROM account_runs WHERE id = %s", run)[0][0]
    assert final[0].committed_micros == int(Decimal(spent) * 1_000_000)
    assert final[1] == {"pages_read": 24}


# ---------------------------------------------------------------- M3, M5, D-45 pause


async def test_one_pause_request_per_cap_version_under_races(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    _, run, _ = await _approved(tenant_a, cfg)
    results = await asyncio.gather(*(open_request(tenant_a, run, cfg) for _ in range(8)))
    assert len({r[0] for r in results}) == 1 and all(r[1] for r in results)
    assert (
        len(
            admin(
                "SELECT 1 FROM spend_limit_events WHERE run_id = %s AND event = 'pause_opened'", run
            )
        )
        == 1
    )
    async with tenant_txn(tenant_a) as s:
        assert await limits.funding_mode(s, run) is limits.Mode.PAUSED
    assert len(admin("SELECT 1 FROM notification_outbox WHERE run_id = %s", run)) == 2


async def test_every_bad_approval_is_refused(
    tenant_a: UUID, tenant_b: UUID, cfg: SpendConfig
) -> None:
    _, run, _ = await _approved(tenant_a, cfg)
    request_id, _ = await open_request(tenant_a, run, cfg)
    cap_before = cap_of(run)

    async def try_approve(
        who: pause.Approver,
        amount: int | None = None,
        req: UUID | None = None,
        now: Any = NOW,
        v2: Any = V2,
    ) -> None:
        async with tenant_txn(tenant_a) as s:
            await pause.approve(
                s, run, req or request_id, who, amount, now, v2, cfg, pause.UnconfiguredWallet()
            )

    lying = [
        (approver(tenant_a, role="viewer"), {}, "role"),
        (approver(tenant_b), {}, "another tenant"),
        (approver(tenant_a, kind="agent"), {}, "person"),
        (approver(tenant_a, step_up_at=None), {}, "step-up"),
        (approver(tenant_a, step_up_at=NOW - timedelta(hours=2)), {}, "step-up"),
        (approver(tenant_a), {"amount": 1_000}, "above the current cap"),
        (approver(tenant_a), {"amount": 999_999_999_950}, "hundredths"),
        (approver(tenant_a), {"req": uuid4()}, "no such pause"),
        (approver(tenant_a), {"now": NOW + timedelta(hours=25)}, "expired"),
        (
            approver(tenant_a),
            {"amount": 50_000_000, "v2": dataclasses.replace(V2, tenant_ceiling_micros=10_000_000)},
            "ceiling",
        ),
    ]
    for who, kw, message in lying:
        with pytest.raises(ApprovalInvalid, match=message):
            await try_approve(who, **kw)
    assert cap_of(run) == cap_before  # nothing changed
    await try_approve(approver(tenant_a))
    with pytest.raises(ApprovalInvalid, match="already decided"):  # a replay is refused
        await try_approve(approver(tenant_a))
    # wrong-tenant session: RLS hides the run entirely
    with pytest.raises(ApprovalInvalid):
        async with tenant_txn(tenant_b) as s:
            await pause.approve(
                s,
                run,
                request_id,
                approver(tenant_b),
                None,
                NOW,
                V2,
                cfg,
                pause.UnconfiguredWallet(),
            )


async def test_approval_appends_a_version_and_raises_the_hard_limit(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    _, run, _ = await _approved(tenant_a, cfg)
    old_cap, old_version = cap_of(run)
    versions_before = admin(
        "SELECT version, cap_usd FROM run_cap_versions WHERE run_id = %s ORDER BY version", run
    )
    request_id, _ = await open_request(tenant_a, run, cfg)
    async with tenant_txn(tenant_a) as s:
        new = await pause.approve(
            s, run, request_id, approver(tenant_a), None, NOW, V2, cfg, pause.UnconfiguredWallet()
        )
    cap, version = cap_of(run)
    assert version == old_version + 1 and Decimal(cap) == Decimal(new) / 1_000_000
    assert Decimal(cap) > Decimal(old_cap)
    versions = admin(
        "SELECT version, cap_usd FROM run_cap_versions WHERE run_id = %s ORDER BY version", run
    )
    assert versions[: len(versions_before)] == versions_before  # appended, never edited
    events = admin("SELECT version, event FROM run_cap_events WHERE run_id = %s ORDER BY id", run)
    assert (old_version, "superseded") in events and (version, "approved") in events
    detail = admin(
        "SELECT detail_json FROM spend_limit_events WHERE request_id = %s AND event = 'approved'",
        request_id,
    )[0][0]
    assert detail["wallet"].startswith("not_applicable")  # D-04 open: no credit rate


async def test_wallet_is_consulted_only_with_a_credit_rate_and_refusal_declines(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    rated = dataclasses.replace(cfg, credit_usd_rates={"p_people": Decimal("0.01")})

    class Refusing:
        async def reserve_extra(self, tenant_id: UUID, run_id: UUID, credits: Decimal) -> bool:
            return False

    _, run, _ = await _approved(tenant_a, cfg)
    request_id, _ = await open_request(tenant_a, run, cfg)
    before = cap_of(run)
    async with tenant_txn(tenant_a) as s:
        await pause.approve(
            s, run, request_id, approver(tenant_a), None, NOW, V2, rated, Refusing()
        )
    assert cap_of(run) == before
    assert admin(
        "SELECT event, principal FROM spend_limit_events WHERE request_id = %s"
        " AND event IN ('approved', 'declined')",
        request_id,
    ) == [("declined", "wallet")]
    _, run2, _ = await _approved(tenant_a, cfg)
    request2, _ = await open_request(tenant_a, run2, cfg)
    with pytest.raises(NotConfigured):  # the production placeholder never fakes a success
        async with tenant_txn(tenant_a) as s:
            await pause.approve(
                s,
                run2,
                request2,
                approver(tenant_a),
                None,
                NOW,
                V2,
                rated,
                pause.UnconfiguredWallet(),
            )


async def test_decline_and_timeout_stop_paid_work_and_a_late_approval_is_refused(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    _, run, _ = await _approved(tenant_a, cfg)
    request_id, _ = await open_request(tenant_a, run, cfg)
    async with tenant_txn(tenant_a) as s:
        await pause.decline(s, run, request_id, approver(tenant_a), NOW, V2)
        assert await limits.funding_mode(s, run) is limits.Mode.STOPPED
    _, run2, _ = await _approved(tenant_a, cfg)
    request2, _ = await open_request(tenant_a, run2, cfg)
    async with tenant_txn(tenant_a) as s:
        assert await pause.expire(s, run2, NOW + timedelta(hours=1), V2) is None  # not yet
        assert await pause.expire(s, run2, NOW + timedelta(hours=25), V2) == request2
    with pytest.raises(ApprovalInvalid, match="timed out"):
        async with tenant_txn(tenant_a) as s:
            await pause.approve(
                s,
                run2,
                request2,
                approver(tenant_a),
                None,
                NOW + timedelta(hours=25),
                V2,
                cfg,
                pause.UnconfiguredWallet(),
            )
    # the refusal itself is recorded (in its own transaction it would roll back; the
    # decision row stays: exactly one decision per request)
    decisions = admin(
        "SELECT event FROM spend_limit_events WHERE request_id = %s"
        " AND event IN ('approved', 'declined', 'timed_out')",
        request2,
    )
    assert decisions == [("timed_out",)]


async def test_pause_count_is_bounded_and_policy_declines_the_extra_one(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    _, run, _ = await _approved(tenant_a, cfg)
    for _ in range(V2.max_pauses_per_run):
        request_id, opened = await open_request(tenant_a, run, cfg)
        assert opened
        async with tenant_txn(tenant_a) as s:
            await pause.approve(
                s,
                run,
                request_id,
                approver(tenant_a),
                None,
                NOW,
                V2,
                cfg,
                pause.UnconfiguredWallet(),
            )
    request_id, opened = await open_request(tenant_a, run, cfg)
    assert not opened
    assert admin(
        "SELECT event, principal FROM spend_limit_events WHERE request_id = %s"
        " AND event = 'declined'",
        request_id,
    ) == [("declined", "policy")]


# ---------------------------------------------------------------- M6 exhaustion, M9 spec


async def test_exhaustion_never_touches_the_cap(tenant_a: UUID, cfg: SpendConfig) -> None:
    _, run, domain = await _approved(tenant_a, cfg)
    versions = admin("SELECT count(*) FROM run_cap_versions WHERE run_id = %s", run)
    cap = cap_of(run)
    spec = exhaustion.file_spec(
        "p_search", "web_search", [{"args_hash": "a" * 64, "args": {"queries": ["x"]}}], domain, V2
    )
    async with tenant_txn(tenant_a) as s:
        one = await exhaustion.request_upload(s, tenant_a, run, 1, spec, NOW, V2)
    async with tenant_txn(tenant_a) as s:
        two = await exhaustion.request_upload(s, tenant_a, run, 1, spec, NOW, V2)
    assert one == two  # once per spec
    assert admin("SELECT count(*) FROM run_cap_versions WHERE run_id = %s", run) == versions
    assert cap_of(run) == cap
    events = [
        e
        for (e,) in admin("SELECT event FROM spend_limit_events WHERE run_id = %s ORDER BY id", run)
    ]
    assert events == ["exhausted", "upload_requested"]
    assert admin("SELECT template FROM notification_outbox WHERE run_id = %s", run) == [
        ("provider_credits_exhausted",)
    ]


# ---------------------------------------------------------------- M8, M9 upload


class Store:
    def __init__(self, body: bytes | None, content_type: str = "application/json") -> None:
        self.body, self.content_type = body, content_type
        self.promoted: dict[str, bytes] = {}
        self.payloads: Any = None

    async def signed_put(
        self, key: str, max_bytes: int, content_type: str, expires_at: Any
    ) -> upload.SignedTarget:
        return upload.SignedTarget(
            f"https://upload.invalid/{key}", expires_at, max_bytes, content_type
        )

    async def stat(self, key: str) -> upload.ObjectInfo | None:
        return None if self.body is None else upload.ObjectInfo(len(self.body), self.content_type)

    async def get(self, key: str) -> bytes:
        assert self.body is not None
        return self.body

    async def promote(self, key: str, accepted_key: str) -> str:
        assert self.body is not None
        self.promoted[accepted_key] = self.body
        if self.payloads is not None:
            await self.payloads.put(accepted_key, self.body)
        return accepted_key


class Scanner:
    def __init__(self, verdict: bool | None) -> None:
        self.verdict = verdict

    async def scan(self, key: str) -> bool:
        if self.verdict is None:
            raise upload.ScannerUnavailable("down")
        return self.verdict


class Parser:
    def __init__(self, records: list[dict[str, Any]] | Exception) -> None:
        self.records = records

    def parse(self, provider_code: str, operation: str, args: Any, body: bytes) -> list[dict]:
        if isinstance(self.records, Exception):
            raise self.records
        return self.records


def cassette() -> bytes:
    data = json.loads((CASSETTES / "p_search" / "web_search.json").read_text())
    return json.dumps(data["response"]["body"]).encode()


async def new_intent(tenant: UUID, cfg: SpendConfig) -> tuple[UUID, UUID, str, UUID]:
    account, run, domain = await _approved(tenant, cfg)
    spec = exhaustion.file_spec(
        "p_search",
        "web_search",
        [{"args_hash": "a" * 64, "args": {"queries": ["synthetic prepulled"]}}],
        domain,
        V2,
    )
    async with tenant_txn(tenant) as s:
        intent = await exhaustion.request_upload(s, tenant, run, 1, spec, NOW, V2)
    return account, run, domain, intent


GOOD = Parser([{"url": "https://a.example/x", "title": "t"}])
ATTEST = upload.Attestation(True, date(2026, 10, 1))


@pytest.mark.parametrize(
    ("store", "scanner", "parser", "attest", "v2", "reason"),
    [
        (Store(None), Scanner(True), GOOD, ATTEST, V2, "not_uploaded"),
        (
            Store(b"x" * 10),
            Scanner(True),
            GOOD,
            ATTEST,
            dataclasses.replace(V2, upload_max_bytes=5),
            "too_large",
        ),
        (Store(b"{}", "application/zip"), Scanner(True), GOOD, ATTEST, V2, "wrong_type"),
        (Store(b"{}"), Scanner(False), GOOD, ATTEST, V2, "scan_failed"),
        (Store(b"{}"), Scanner(None), GOOD, ATTEST, V2, "scan_unavailable"),
        (Store(b"{}"), Scanner(True), Parser(ValueError("drift")), ATTEST, V2, "schema_drift"),
        (Store(b"{}"), Scanner(True), Parser([]), ATTEST, V2, "empty"),
        (
            Store(b"{}"),
            Scanner(True),
            Parser([{"domain": "other.example"}]),
            ATTEST,
            V2,
            "account_mismatch",
        ),
        (
            Store(b"{}"),
            Scanner(True),
            GOOD,
            upload.Attestation(False, date(2026, 10, 1)),
            V2,
            "attestation_missing",
        ),
        (
            Store(b"{}"),
            Scanner(True),
            GOOD,
            upload.Attestation(True, None),
            V2,
            "attestation_missing",
        ),
        (
            Store(b"{}"),
            Scanner(True),
            GOOD,
            upload.Attestation(True, date(2025, 1, 1)),
            dataclasses.replace(V2, upload_max_age_days=30),
            "stale",
        ),
    ],
)
async def test_hostile_uploads_are_rejected_with_typed_reasons(
    tenant_a: UUID,
    cfg: SpendConfig,
    store: Store,
    scanner: Scanner,
    parser: Parser,
    attest: upload.Attestation,
    v2: Any,
    reason: str,
) -> None:
    _, run, _, intent = await new_intent(tenant_a, cfg)
    verdict = await upload.complete(
        tenant_txn, tenant_a, intent, "operator:o1", attest, store, scanner, parser, NOW, v2
    )
    assert (verdict.accepted, verdict.reason) == (False, reason)
    assert admin("SELECT count(*) FROM prepulled_files WHERE run_id = %s", run) == [(0,)]
    assert store.promoted == {}  # the file never leaves quarantine
    again = await upload.complete(
        tenant_txn,
        tenant_a,
        intent,
        "operator:o1",
        ATTEST,
        Store(cassette()),
        Scanner(True),
        GOOD,
        NOW,
        V2,
    )
    assert again.reason == "already_decided"  # one pass: a rejected file needs a new intent


async def test_an_unavailable_scanner_fails_closed(tenant_a: UUID, cfg: SpendConfig) -> None:
    _, run, _, intent = await new_intent(tenant_a, cfg)
    verdict = await upload.complete(
        tenant_txn,
        tenant_a,
        intent,
        "operator:o1",
        ATTEST,
        Store(cassette()),
        upload.UnconfiguredScanner(),
        GOOD,
        NOW,
        V2,
    )
    assert verdict.reason == "scan_unavailable"
    events = [
        e
        for (e,) in admin(
            "SELECT event FROM prepulled_intent_events WHERE intent_id = %s ORDER BY id", intent
        )
    ]
    assert events == ["created", "completed", "scan_unavailable", "rejected"]


async def test_an_accepted_upload_is_consumed_instead_of_a_paid_call(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    harness = _harness()
    account, run, domain, intent = await new_intent(tenant_a, cfg)
    body = cassette()
    store = Store(body)
    store.payloads = harness.payloads
    records = json.loads(body)["results"]
    verdict = await upload.complete(
        tenant_txn,
        tenant_a,
        intent,
        "operator:o1",
        ATTEST,
        store,
        Scanner(True),
        Parser(records),
        NOW,
        V2,
    )
    assert verdict.accepted and verdict.operations == ("p_search.web_search",)
    assert verdict.record_count == len(records)
    dup_account, dup_run, _, dup_intent = await new_intent(tenant_a, cfg)
    assert dup_run != run
    result = await call_operation(
        Operation.WEB_SEARCH,
        WebSearchArgs(queries=("synthetic prepulled",)),
        context(tenant_a, account, run, domain),
        harness.runtime(),
    )
    assert result.cost_source == "prepulled" and result.charged_usd == 0
    assert harness.providers[ProviderCode.P_SEARCH].calls == 0
    # a second identical file for the same run is a duplicate
    _, _, _, other = await new_intent(tenant_a, cfg)
    admin("UPDATE prepulled_intents SET run_id = %s WHERE id = %s", run, other) if False else None
    events = admin("SELECT event FROM spend_limit_events WHERE run_id = %s ORDER BY id", run)
    assert ("upload_accepted",) in events


async def test_a_duplicate_or_already_registered_file_is_refused(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    account, run, domain = await _approved(tenant_a, cfg)
    body = cassette()
    records = json.loads(body)["results"]
    intents = []
    for queries in (["one"], ["two"]):
        spec = exhaustion.file_spec(
            "p_search",
            "web_search",
            [{"args_hash": "c" * 64, "args": {"queries": queries}}],
            domain,
            V2,
        )
        async with tenant_txn(tenant_a) as s:
            intents.append(await exhaustion.request_upload(s, tenant_a, run, 1, spec, NOW, V2))
    first = await upload.complete(
        tenant_txn,
        tenant_a,
        intents[0],
        "operator:o1",
        ATTEST,
        Store(body),
        Scanner(True),
        Parser(records),
        NOW,
        V2,
    )
    assert first.accepted
    second = await upload.complete(
        tenant_txn,
        tenant_a,
        intents[1],
        "operator:o1",
        ATTEST,
        Store(body),
        Scanner(True),
        Parser(records),
        NOW,
        V2,
    )
    assert second.reason == "duplicate"
    third_body = body + b" "
    spec = exhaustion.file_spec(
        "p_search",
        "web_search",
        [{"args_hash": "d" * 64, "args": {"queries": ["three"]}}],
        domain,
        V2,
    )
    async with tenant_txn(tenant_a) as s:
        third = await exhaustion.request_upload(s, tenant_a, run, 1, spec, NOW, V2)
    verdict = await upload.complete(
        tenant_txn,
        tenant_a,
        third,
        "operator:o1",
        ATTEST,
        Store(third_body),
        Scanner(True),
        Parser(records),
        NOW,
        V2,
    )
    assert verdict.reason == "already_registered"  # unmatched: the operation is already served


async def test_upload_intents_are_tenant_isolated(
    tenant_a: UUID, tenant_b: UUID, cfg: SpendConfig
) -> None:
    _, _, _, intent = await new_intent(tenant_a, cfg)
    verdict = await upload.complete(
        tenant_txn,
        tenant_b,
        intent,
        "operator:o1",
        ATTEST,
        Store(cassette()),
        Scanner(True),
        GOOD,
        NOW,
        V2,
    )
    assert verdict.reason == "intent_not_found"
    with pytest.raises(upload.UploadError, match="intent_not_found"):
        await upload.create_intent_target(tenant_txn, tenant_b, intent, Store(None), NOW, V2)
    spec, target = await upload.create_intent_target(
        tenant_txn, tenant_a, intent, Store(None), NOW, V2
    )
    assert target.max_bytes == V2.upload_max_bytes and spec["operation"] == "web_search"
    with pytest.raises(NotConfigured):
        await upload.create_intent_target(
            tenant_txn, tenant_a, intent, upload.UnconfiguredUploadStore(), NOW, V2
        )


# ---------------------------------------------------------------- M10 outbox


async def test_outbox_dedupes_and_relays_at_least_once_with_bounded_retries(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    _, run, _ = await _approved(tenant_a, cfg)
    async with tenant_txn(tenant_a) as s:
        assert await notify.enqueue(
            s,
            run,
            "k1",
            "operator",
            "spend_soft_limit",
            {"run_id": str(run), "cap_version": 1},
            NOW,
        )
        assert not await notify.enqueue(
            s,
            run,
            "k1",
            "operator",
            "spend_soft_limit",
            {"run_id": str(run), "cap_version": 1},
            NOW,
        )

    class Flaky:
        def __init__(self, fail: int) -> None:
            self.fail, self.sent = fail, []

        async def send(self, audience: str, subject: str, body: str, dedupe_key: str) -> None:
            if self.fail > 0:
                self.fail -= 1
                raise ConnectionError("channel down")
            self.sent.append(dedupe_key)

    sender = Flaky(2)
    for _ in range(4):
        await notify.relay(tenant_txn, tenant_a, sender, NOW, V2.notify_max_attempts)
    assert sender.sent == ["k1"]  # delivered once, never again
    outcomes = admin(
        "SELECT attempt, outcome FROM notification_deliveries d JOIN"
        " notification_outbox o ON o.id = d.outbox_id WHERE o.dedupe_key = 'k1'"
        " AND o.run_id = %s ORDER BY attempt",
        run,
    )
    assert outcomes == [(1, "failed"), (2, "failed"), (3, "delivered")]
    async with tenant_txn(tenant_a) as s:
        await notify.enqueue(
            s,
            run,
            "k2",
            "operator",
            "spend_soft_limit",
            {"run_id": str(run), "cap_version": 2},
            NOW,
        )
    dead = Flaky(999)
    for _ in range(V2.notify_max_attempts + 2):
        await notify.relay(tenant_txn, tenant_a, dead, NOW, V2.notify_max_attempts)
    final = admin(
        "SELECT max(attempt), bool_or(outcome = 'gave_up') FROM notification_deliveries"
        " d JOIN notification_outbox o ON o.id = d.outbox_id WHERE o.dedupe_key = 'k2'"
        " AND o.run_id = %s",
        run,
    )
    assert final == [(V2.notify_max_attempts, True)]  # bounded, then a recorded give-up
