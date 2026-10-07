"""WS5 alerting (AL1 to AL8, DS1, DS3). Lying scenarios: an alert without a delta, a Lead as an
immediate alert, a leadership change from one low-tier source, the same event twice, a tenant rule
naming another tenant's account, a new user receiving immediate alerts, a flood past the caps, a
crash between send and record, a sensitive event in an immediate subject, a rendered message with a
provider name, a usefulness target shown as a result."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
import pytest

from account_intelligence.db.session import tenant_txn
from account_intelligence.safety.client_output import scan_text
from alerts import (
    digest,
    engine,
    feedback,
    freshness,
    outbox,
    rules,
    subscriptions,
)
from refresh import delta, records
from signals import events, fingerprint, score
from tests.conftest import ADMIN_DSN
from tests.phase4.conftest import NOW, db, fact, tenant_with_account

pytestmark = pytest.mark.requires_postgres
APP = "https://app.test"
USERS = ("u-digest", "u-immediate")


async def make_event(
    t: UUID,
    a: UUID,
    *,
    signal: str,
    event_type: str = "expansion",
    grade: str = "confirmed",
    lead_flag: bool = False,
    sensitive: bool = False,
    title: str = "Acme opens a plant",
    sources: tuple[tuple[str, str], ...] = (("https://a.example/1", "t1"),),
    with_delta: bool = True,
) -> UUID:
    f = fact(t, a, grade=grade if grade != "lead" else "lead")
    async with tenant_txn(t) as s:
        item_id = None
        if with_delta:
            run = await records.record_run(
                s,
                slot_id=None,
                account_id=a,
                kind="probe",
                parent_run_id=None,
                decision={},
                now=NOW,
            )
            d = delta.compute(
                [],
                [delta.FactView(str(f), title, signal, grade, "S-0002")],
                [],
                {},
                {},
                checked=["company_news"],
                checked_at=NOW,
            )
            _, ids = await records.record_delta(s, run_id=run, account_id=a, delta=d, score=1)
            item_id = ids[0]
        first = None
        for url, tier in sources:
            sc = score.score(score.Inputs(signal, grade, tier, NOW.date(), True, False), NOW.date())
            r = await events.record(
                s,
                events.EventInput(
                    a,
                    fingerprint.fingerprint(
                        signal, "Acme", {"t": title}, NOW.date(), bucket_days=7
                    ),
                    signal,
                    event_type,
                    "Acme",
                    title,
                    fingerprint.date_bucket(NOW.date(), 7),
                    grade,
                    lead_flag or grade == "lead",
                    sensitive,
                    f,
                    item_id,
                    sc,
                    url,
                    "S-0002",
                    tier,
                    "refresh",
                    NOW,
                ),
                same=0.8,
                ambiguous=0.5,
            )
            first = first or r.event_id
    assert first is not None
    return first


async def evaluate(
    t: UUID, ev: UUID, *, read: bool = True, users: tuple[str, ...] = USERS, at: datetime = NOW
) -> engine.Outcome:
    async with tenant_txn(t) as s:
        return await engine.evaluate(
            s,
            ev,
            recipients=users,
            account_label="acme.example",
            read_in_this_refresh=read,
            app_base=APP,
            now=at,
        )


async def subscribe_immediate(t: UUID, user: str = "u-immediate", **kw: Any) -> None:
    sub = subscriptions.Subscription(immediate_rules=frozenset(rules.load()), **kw)
    async with tenant_txn(t) as s:
        await subscriptions.save(s, user, sub, set_by=user, bulk=False, now=NOW)


def outbox_rows(t: UUID) -> list[tuple[str, str, str]]:
    return sorted(
        db(
            "SELECT a.rule_id, o.channel, o.recipient FROM alert_outbox o JOIN alerts a"
            " ON a.id = o.alert_id WHERE o.tenant_id = %s",
            t,
        )
    )


# --- registry and subscribe-up -----------------------------------------------------------------


# --- the engine --------------------------------------------------------------------------------


@dataclass
class Case:
    signal: str
    fires: str  # rule id expected
    grade: str = "confirmed"
    event_type: str = "expansion"
    quiet_grade: str | None = None  # a grade below the rule's floor
    extra: dict[str, Any] = field(default_factory=dict)


CASES = [
    Case("build_role_posting", "new_build_role", quiet_grade="hypothesis"),
    Case("leadership_change", "leadership_change", quiet_grade="hypothesis"),
    Case("news_event", "tier_one_news", quiet_grade="probable"),
    Case("funding_ma", "funding_ma", quiet_grade="hypothesis"),
    Case("tech_detection", "new_tech_detection", grade="probable"),
    Case("exec_speaking", "exec_speaking", grade="probable"),
    Case("priority_a_post", "priority_a_posts", grade="probable"),
    Case("lead_regraded", "lead_regraded_up", grade="confirmed", quiet_grade="hypothesis"),
    Case("confidence_drop", "confidence_drop", grade="hypothesis"),
    Case("facility_expansion", "facility_expansion", grade="probable", quiet_grade="hypothesis"),
    Case(
        "vendor_customer_story", "vendor_customer_story", grade="probable", quiet_grade="hypothesis"
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=[c.fires for c in CASES])
async def test_every_rule_fires_on_its_scenario_and_stays_quiet_otherwise(case: Case) -> None:
    t, a = tenant_with_account(f"p4-rule-{case.fires}")
    await subscribe_immediate(t)
    ev = await make_event(
        t, a, signal=case.signal, grade=case.grade, sources=(("https://a.example/1", "t1"),)
    )
    out = await evaluate(t, ev)
    tier = rules.load()[case.fires].tier
    assert len(out.alerts) == 1, out
    got = db("SELECT rule_id, tier, fact_delta_id IS NOT NULL FROM alerts WHERE tenant_id = %s", t)
    assert got == [(case.fires, tier, True)]
    chans = {(ch, u) for _, ch, u in outbox_rows(t)}
    assert ("digest", "u-digest") in chans  # subscribe up: the new user gets the digest
    if tier == "immediate":
        assert ("email", "u-immediate") in chans and ("email", "u-digest") not in chans
    else:
        assert not any(ch == "email" for ch, _ in chans)  # digest rules are never real time
    # a second arrival of the same event creates nothing (one event, one alert)
    again = await evaluate(t, ev)
    assert again.alerts == () and db("SELECT count(*) FROM alerts WHERE tenant_id = %s", t) == [
        (1,)
    ]
    if case.quiet_grade:
        t2, a2 = tenant_with_account(f"p4-quiet-{case.fires}")
        ev2 = await make_event(t2, a2, signal=case.signal, grade=case.quiet_grade)
        quiet = await evaluate(t2, ev2)
        if tier == "immediate":  # below the floor and read: not alerted, recorded
            assert quiet.alerts == ()
            assert "below_grade_floor" in {r for _, _, r in quiet.suppressed}
        assert db("SELECT count(*) FROM signal_events WHERE tenant_id = %s", t2) == [(1,)]


@pytest.mark.asyncio
async def test_leads_unread_and_single_low_tier_leadership_stay_out_of_immediate() -> None:
    t, a = tenant_with_account("p4-leads")
    await subscribe_immediate(t)
    lead_ev = await make_event(t, a, signal="build_role_posting", grade="lead", title="kw match")
    await evaluate(t, lead_ev)
    unread = await make_event(t, a, signal="funding_ma", grade="confirmed", title="raised")
    await evaluate(t, unread, read=False)
    one = await make_event(
        t,
        a,
        signal="leadership_change",
        grade="confirmed",
        title="new CIO",
        sources=(("https://blog.example/1", "t3"),),
    )
    out = await evaluate(t, one)
    assert "needs_two_sources" in {r for _, _, r in out.suppressed}
    two = await make_event(
        t,
        a,
        signal="leadership_change",
        grade="confirmed",
        title="new CFO",
        sources=(("https://b1.example/1", "t3"), ("https://b2.example/2", "t2")),
    )
    assert len((await evaluate(t, two)).alerts) == 1
    rows = db(
        "SELECT rule_id, tier, lead, body FROM alerts WHERE tenant_id = %s ORDER BY rule_id", t
    )
    by_rule = {r[0]: r for r in rows}
    assert by_rule["new_build_role"][1:3] == ("digest", True)
    assert "Lead, not yet verified" in by_rule["new_build_role"][3]
    assert by_rule["funding_ma"][1] == "digest"  # not read in this refresh: never immediate
    assert by_rule["leadership_change"][1] == "immediate"
    immediate = {r for r, ch, _ in outbox_rows(t) if ch == "email"}
    assert immediate == {"leadership_change"}


@pytest.mark.asyncio
async def test_an_alert_without_a_delta_cannot_exist() -> None:
    t, a = tenant_with_account("p4-nodelta")
    ev = await make_event(t, a, signal="build_role_posting", with_delta=False)
    out = await evaluate(t, ev)
    assert out.alerts == () and {r for _, _, r in out.suppressed} == {"no_evidence"}
    with psycopg.connect(ADMIN_DSN) as c, pytest.raises(psycopg.errors.NotNullViolation):
        c.execute(
            "INSERT INTO alerts (tenant_id, account_id, rule_id, fingerprint, event_id,"
            " tier, score, lead, subject, body, signal_detected_at, created_at) VALUES"
            " (%s, %s, 'new_build_role', 'x', %s, 'digest', 1, false, 's', 'b', now(),"
            " now())",
            (t, a, ev),
        )


@pytest.mark.asyncio
async def test_sensitive_events_are_preparation_only_in_every_channel() -> None:
    t, a = tenant_with_account("p4-sensitive")
    await subscribe_immediate(t)
    for et, sig in (
        ("data_breach", "news_event"),
        ("layoff", "news_event"),
        ("lawsuit", "news_event"),
    ):
        ev = await make_event(t, a, signal=sig, event_type=et, sensitive=True, title=f"{et} story")
        await evaluate(t, ev)
    rows = db("SELECT tier, subject, body FROM alerts WHERE tenant_id = %s", t)
    assert len(rows) == 3
    for tier, subject, body in rows:
        assert tier == "digest"  # never in an immediate subject line
        assert subject == "Account context update"
        assert body.startswith("Preparation only, not an opening line.")
        assert "breach" not in subject.lower() and "story" not in body


@pytest.mark.asyncio
async def test_tenant_rules_can_only_name_their_own_accounts() -> None:
    t, a = tenant_with_account("p4-trule")
    other_t, other_a = tenant_with_account("p4-trule-other")
    async with tenant_txn(t) as s:
        from sqlalchemy import text

        rid = (
            await s.execute(
                text(
                    "INSERT INTO tenant_rules (name, signal_type, grade_floor, min_score,"
                    " created_by,"
                    " created_at) VALUES ('mine', 'news_event', 'probable', 0, 'u', now())"
                    " RETURNING id"
                )
            )
        ).scalar_one()
        await s.execute(
            text("INSERT INTO tenant_rule_accounts (rule_id, account_id) VALUES (:r, :a)"),
            {"r": rid, "a": a},
        )
    import sqlalchemy.exc

    with pytest.raises(sqlalchemy.exc.IntegrityError):  # another tenant's account
        async with tenant_txn(t) as s:
            await s.execute(
                text("INSERT INTO tenant_rule_accounts (rule_id, account_id) VALUES (:r, :a)"),
                {"r": rid, "a": other_a},
            )
    ev = await make_event(t, a, signal="news_event", grade="probable")
    out = await evaluate(t, ev)
    assert any(str(x) for x in out.alerts)
    assert db(
        "SELECT count(*) FROM alerts WHERE tenant_id = %s AND rule_id LIKE 'tenant:%%'", t
    ) == [(1,)]


@pytest.mark.asyncio
async def test_caps_hold_under_a_flood() -> None:
    t, a = tenant_with_account("p4-flood")
    await subscribe_immediate(t)
    for i in range(6):
        ev = await make_event(t, a, signal="build_role_posting", title=f"role {i}")
        await evaluate(t, ev)
    emails = [r for r in outbox_rows(t) if r[1] == "email"]
    assert len(emails) == 3  # per-user hourly cap (proposal 3)
    reasons = [r for (r,) in db("SELECT reason FROM alert_suppressions WHERE tenant_id = %s", t)]
    assert reasons.count("user_hourly_cap") == 3  # batched into the digest, recorded
    for i in range(2):
        ev = await make_event(t, a, signal="news_event", title=f"big news {i}")
        await evaluate(t, ev, users=("u-immediate",), at=NOW + timedelta(hours=2))
    news = [r for r in outbox_rows(t) if r[0] == "tier_one_news" and r[1] == "email"]
    assert len(news) == 1  # per-account daily cap on tier-one news


# --- delivery ----------------------------------------------------------------------------------


@dataclass
class FlakySlack:
    fail: int = 0
    posts: list[tuple[str, str]] = field(default_factory=list)

    async def post(self, recipient: str, text: str, *, idempotency_key: str) -> str:
        if self.fail:
            self.fail -= 1
            raise RuntimeError("slack down")
        self.posts.append((recipient, idempotency_key))
        return "m"


@dataclass
class Hook:
    received: list[tuple[bytes, dict[str, str]]] = field(default_factory=list)

    async def post(self, body: bytes, headers: Any) -> int:
        self.received.append((body, dict(headers)))
        return 200


@pytest.mark.asyncio
async def test_the_outbox_delivers_once_per_event_id_and_retries_within_24h() -> None:
    from tests.fakes.phase3 import FakeMailer

    t, a = tenant_with_account("p4-outbox")
    await subscribe_immediate(t, slack=True)
    ev = await make_event(t, a, signal="build_role_posting")
    await evaluate(t, ev, users=("u-immediate",))
    async with tenant_txn(t) as s:
        await s.execute(
            __import__("sqlalchemy").text(
                "INSERT INTO alert_outbox (alert_id, channel, recipient, next_attempt_at)"
                " SELECT id, 'webhook', 'endpoint', :now FROM alerts"
            ),
            {"now": NOW},
        )
    mailer, slack, hook = FakeMailer(), FlakySlack(fail=2), Hook()
    ch = outbox.Channels(mailer, slack, hook, webhook_key=b"k" * 32)
    first = await outbox.relay(tenant_txn, t, ch, now=NOW)
    assert (first.sent, first.retried) == (3, 1)  # in-app, email and webhook sent; slack retried
    again = await outbox.relay(tenant_txn, t, ch, now=NOW)  # nothing due: no double send
    assert again == outbox.PassResult(0, 0, 0)
    later = NOW
    for _ in range(3):
        later += timedelta(hours=1)
        await outbox.relay(tenant_txn, t, ch, now=later)
    assert len(mailer.sent) == 1 and len(slack.posts) == 1 and len(hook.received) == 1
    body, headers = hook.received[0]
    from signals.webhooks import sign

    assert headers["X-Signature"] == sign(b"k" * 32, int(headers["X-Timestamp"]), body)
    assert headers["X-Event-Id"] in body.decode()
    # a channel that never recovers ends failed (operator-visible), never blocking anything
    t2, a2 = tenant_with_account("p4-outbox-dead")
    await subscribe_immediate(t2, slack=True)
    await evaluate(
        t2, await make_event(t2, a2, signal="build_role_posting"), users=("u-immediate",)
    )
    dead = FlakySlack(fail=10_000)
    at = NOW
    for _ in range(20):  # LOOP(test drive): bounded
        await outbox.relay(tenant_txn, t2, outbox.Channels(FakeMailer(), dead, hook), now=at)
        at += timedelta(hours=6)
    assert db(
        "SELECT status, last_error FROM alert_outbox WHERE tenant_id = %s AND channel = 'slack'", t2
    ) == [("failed", "channel_error")]
    async with tenant_txn(t) as s:
        fr = freshness.compute(await outbox.freshness_rows(s), NOW + timedelta(days=2))
    assert fr.judged == 1 and fr.good == 1 and fr.meets_slo


@pytest.mark.asyncio
async def test_digests_are_bounded_and_deterministic() -> None:
    t, a = tenant_with_account("p4-digest")
    for i in range(7):
        await evaluate(
            t,
            await make_event(t, a, signal="tech_detection", grade="probable", title=f"tech {i}"),
            users=("u-digest",),
        )
    async with tenant_txn(t) as s:
        built = await digest.build(s, user_id="u-digest", period="2026-W10", limit=5, now=NOW)
        twice = await digest.build(s, user_id="u-digest", period="2026-W10", limit=5, now=NOW)
    assert built is not None and (built.items, built.more) == (5, 2) and twice is None
    assert "and 2 more in the app" in built.body and not scan_text(built.body)
    items = [digest.Item(f"fp{i}", i % 3, NOW, f"line {i}") for i in range(6)]
    assert digest.assemble(items, limit=10) == digest.assemble(list(reversed(items)), limit=10)


@pytest.mark.asyncio
async def test_alert_usefulness_is_measured_with_its_base() -> None:
    t, a = tenant_with_account("p4-useful")
    await subscribe_immediate(t)
    out = await evaluate(t, await make_event(t, a, signal="build_role_posting"))
    async with tenant_txn(t) as s:
        await outbox.deliver_in_app(s, now=NOW)
        await feedback.rate(
            s,
            alert_id=out.alerts[0],
            user_id="u-immediate",
            verdict="useful",
            reason="acted_on",
            now=NOW,
        )
        await feedback.rate(
            s,
            alert_id=out.alerts[0],
            user_id="u-digest",
            verdict="not_useful",
            reason="already_known",
            now=NOW,
        )
        await feedback.rate(
            s,
            alert_id=out.alerts[0],
            user_id="u-digest",
            verdict="useful",
            reason="new_to_me",
            now=NOW,
        )  # changed mind: newest wins
        with pytest.raises(ValueError):
            await feedback.rate(
                s, alert_id=out.alerts[0], user_id="u", verdict="useful", reason="wrong", now=NOW
            )
        m = await feedback.metric(s)
    assert (m["useful"], m["rated"], m["delivered"], m["share"]) == (2, 2, 1, 1.0)
    assert m["small_base"] and m["target"] == 0.7 and m["target_is_a_result"] is False
