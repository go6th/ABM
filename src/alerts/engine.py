"""The alert engine (AL1 to AL5, DS1, DS3, D-74 to D-76): from one signal event to alerts, their
deliveries and their recorded suppressions, in one transaction.

For each rule watching the event's signal type (the registry, then the tenant's own rules for this
account):

1. The event must carry a delta item (``fact_delta_id``): without one no alert can exist (AL1;
   also a NOT NULL foreign key).
2. Lead routing (``signals.lead``): a Lead, or anything not read in this refresh, is digest-only
   and labelled; below the rule's grade floor it is not alerted (recorded).
3. ``leadership_change`` needs two sources or one tier-one source; otherwise recorded, not sent.
4. Sensitive events (breach, layoff, lawsuit) are digest-only and rendered from fixed templates.
5. The alert row is unique per (rule, account, fingerprint): a second arrival of the same event
   creates nothing (one event, one alert).
6. Per recipient: subscribe-up resolution, then noise caps for immediate delivery (send, batch to
   the digest, or suppress). Every non-delivery is an ``alert_suppressions`` row with its reason;
   the event itself is never deleted (thresholds filter, they do not delete).

The one writer of ``alerts``, ``alert_outbox`` (creation) and ``alert_suppressions``.

GUARANTEE(test_every_rule_fires_on_its_scenario_and_stays_quiet_otherwise)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text

from alerts import caps as caps_mod
from alerts import subscriptions, templates
from alerts.rules import Rule, extra_config, rules_for
from signals import lead


@dataclass(frozen=True)
class Outcome:
    """What one evaluation did."""

    alerts: tuple[UUID, ...]
    deliveries: int
    suppressed: tuple[tuple[str, str, str], ...]  # (rule, recipient, reason)


def _caps() -> caps_mod.Caps:
    c = extra_config()["caps"]
    return caps_mod.Caps(
        int(c["tier_one_news_per_account_per_day"]),
        int(c["per_user_per_hour"]),
        int(c["per_user_per_day"]),
        int(c["digest_batch_threshold"]),
    )


async def _tenant_rules(s: Any, account_id: UUID, signal_type: str) -> list[Rule]:
    rows = (
        await s.execute(
            text(
                "SELECT r.id, r.grade_floor FROM tenant_rules r JOIN tenant_rule_accounts a"
                " ON a.rule_id = r.id WHERE a.account_id = :a AND r.signal_type = :t ORDER BY r.id"
            ),
            {"a": account_id, "t": signal_type},
        )
    ).all()
    return [
        Rule(f"tenant:{r.id}", "digest", ("weekly_digest",), (signal_type,), r.grade_floor, True)
        for r in rows
    ]


async def _sent_counts(s: Any, recipient: str, account_id: UUID, now: datetime) -> caps_mod.Sent:
    day, hour = now - timedelta(days=1), now - timedelta(hours=1)
    row = (
        await s.execute(
            text(
                "SELECT"
                " count(*) FILTER (WHERE a.rule_id = 'tier_one_news' AND a.account_id = :acc"
                "   AND o.recipient = :r AND a.created_at >= :day) AS news,"
                " count(*) FILTER (WHERE o.recipient = :r AND a.created_at >= :hour) AS hr,"
                " count(*) FILTER (WHERE o.recipient = :r AND a.created_at >= :day) AS dy,"
                " count(DISTINCT a.id) FILTER (WHERE a.created_at >= :day) AS tenant_day"
                " FROM alert_outbox o JOIN alerts a ON a.id = o.alert_id"
                " WHERE o.channel = 'email' AND a.tier = 'immediate'"
            ),
            {"acc": account_id, "r": recipient, "day": day, "hour": hour},
        )
    ).one()
    return caps_mod.Sent(int(row.news), int(row.hr), int(row.dy), int(row.tenant_day))


async def _suppress(
    s: Any,
    out: list[tuple[str, str, str]],
    event_id: UUID,
    rule: str,
    recipient: str,
    reason: str,
    now: datetime,
) -> None:
    out.append((rule, recipient, reason))
    await s.execute(
        text(
            "INSERT INTO alert_suppressions (event_id, rule_id, recipient, reason, at)"
            " VALUES (:e, :r, :u, :why, :now)"
        ),
        {"e": event_id, "r": rule, "u": recipient, "why": reason, "now": now},
    )


async def evaluate(
    s: Any,
    event_id: UUID,
    *,
    recipients: Sequence[str],
    account_label: str,
    read_in_this_refresh: bool,
    app_base: str,
    now: datetime,
) -> Outcome:
    """Evaluate one event (see the module docstring)."""
    ev = (
        await s.execute(
            text(
                "SELECT e.*, (SELECT count(*) FROM signal_event_evidence x WHERE x.event_id = e.id)"
                " AS sources, (SELECT count(*) FROM signal_event_evidence x WHERE x.event_id = e.id"
                " AND x.source_tier = 't1') AS t1, (SELECT min(source_code) FROM"
                " signal_event_evidence x"
                " WHERE x.event_id = e.id) AS code FROM signal_events e WHERE e.id = :e"
            ),
            {"e": event_id},
        )
    ).one()
    suppressed: list[tuple[str, str, str]] = []
    created: list[UUID] = []
    deliveries = 0
    rules = rules_for(ev.signal_type) + await _tenant_rules(s, ev.account_id, ev.signal_type)
    for rule in rules:
        if ev.delta_item_id is None:
            await _suppress(s, suppressed, event_id, rule.id, "*", "no_evidence", now)
            continue
        route = lead.route(
            lead.Candidate(ev.grade, read_in_this_refresh, int(ev.sources) > 0),
            rule_tier=rule.tier,
            grade_floor=rule.grade_floor,
        )
        if route == "drop":
            await _suppress(s, suppressed, event_id, rule.id, "*", "below_grade_floor", now)
            continue
        if (
            rule.id == "leadership_change"
            and route == "immediate"
            and int(ev.sources) < 2
            and int(ev.t1) == 0
        ):
            await _suppress(s, suppressed, event_id, rule.id, "*", "needs_two_sources", now)
            continue
        is_lead = route == "digest_as_lead"
        tier = "immediate" if route == "immediate" and not ev.sensitive else "digest"
        msg = templates.render(
            rule.id if not rule.id.startswith("tenant:") else "tier_one_news",
            account=account_label,
            summary=ev.title,
            grade=ev.grade,
            source_code=ev.code,
            event_type=ev.event_type,
            is_lead=is_lead,
            app_link=f"{app_base}/alerts/{event_id}",
        )
        row = (
            await s.execute(
                text(
                    "INSERT INTO alerts (account_id, rule_id, fingerprint, event_id,"
                    " fact_delta_id, tier,"
                    " score, lead, subject, body, signal_detected_at, created_at) VALUES (:a, :r,"
                    " :fp,"
                    " :e, :d, :t, :sc, :l, :sub, :body, :det, :now)"
                    " ON CONFLICT (tenant_id, rule_id, account_id, fingerprint) DO NOTHING"
                    " RETURNING id"
                ),
                {
                    "a": ev.account_id,
                    "r": rule.id,
                    "fp": ev.fingerprint,
                    "e": event_id,
                    "d": ev.delta_item_id,
                    "t": tier,
                    "sc": ev.score,
                    "l": is_lead,
                    "sub": msg.subject,
                    "body": msg.body,
                    "det": ev.detected_at,
                    "now": now,
                },
            )
        ).first()
        if row is None:
            continue  # one event, one alert: this rule already alerted on this event
        created.append(row.id)
        eff_rule = replace(rule, tier=tier)  # type: ignore[arg-type]
        for user in recipients:  # LOOP(alert fan-out): cap=len(recipients); exit=end
            sub = await subscriptions.current(s, user)
            chans, why = subscriptions.channels(
                sub,
                eff_rule,
                account=str(ev.account_id),
                score=int(ev.score),
                now_ts=now.timestamp(),
            )
            if not chans:
                await _suppress(
                    s, suppressed, event_id, rule.id, user, why or "not_subscribed", now
                )
                continue
            if "email" in chans:
                decision, reason = caps_mod.decide(
                    rule.id, tier, await _sent_counts(s, user, ev.account_id, now), _caps()
                )
                if decision == "suppress":
                    await _suppress(s, suppressed, event_id, rule.id, user, reason or "x", now)
                    continue
                if decision == "batch":
                    chans = ["in_app", "digest"]
                    await _suppress(s, suppressed, event_id, rule.id, user, reason or "x", now)
            for ch in chans:
                deliveries += 1
                await s.execute(
                    text(
                        "INSERT INTO alert_outbox (alert_id, channel, recipient, next_attempt_at)"
                        " VALUES (:a, :c, :u, :now) ON CONFLICT DO NOTHING"
                    ),
                    {"a": row.id, "c": ch, "u": user, "now": now},
                )
    return Outcome(tuple(created), deliveries, tuple(suppressed))
