"""The tenant's audit export (AU2, D-69 proposal; SaaS 3.1 rule 7 reconciled with rule 6).

A CSV a tenant admin downloads: every audit event (sequence, time, actor, actor kind, class,
action, resource, route, request id, outcome, support grant), every sign-in event, every connector
access (the connection, the source's own resource id, purpose and outcome), and, per run,
how many provider calls were made in each neutral CATEGORY (``web_search``, ``people_search``,
...). Never a provider name, a credit count, a cost, a token or any content. Every line passes
the client-output scanner and every cell is neutralised against spreadsheet formula injection.

GUARANTEE(test_audit_export_is_client_safe_and_tenant_scoped)
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.grading.config import load_grading_config
from account_intelligence.safety.client_output import assert_client_safe
from views.render_safe import csv_cell

HEADER = (
    "kind",
    "seq",
    "at",
    "actor",
    "actor_kind",
    "class",
    "action",
    "resource_type",
    "resource_id",
    "route",
    "request_id",
    "outcome",
    "support_grant",
    "category",
    "count",
)


def _row(**values: Any) -> list[str]:
    return [csv_cell(values.get(h)) for h in HEADER]


async def build_csv(
    s: AsyncSession, *, since: datetime | None, until: datetime | None, max_rows: int
) -> tuple[bytes, int]:
    """The export for the context tenant between ``since`` and ``until``; returns the CSV bytes
    and the row count. Raises ``ClientOutputLeak`` if any line would carry a forbidden term."""
    window = {"since": since, "until": until, "n": max_rows}
    where = (
        "(CAST(:since AS timestamptz) IS NULL OR at >= :since)"
        " AND (CAST(:until AS timestamptz) IS NULL OR at < :until)"
    )
    events = (
        await s.execute(
            text(
                "SELECT seq, at, actor, actor_kind, audit_class, action, resource_type,"  # nosec B608 -- fixed SQL; values are bound.
                f" resource_id, route, request_id, outcome, support_grant_id FROM audit_events"
                f" WHERE {where} ORDER BY seq LIMIT :n"  # noqa: S608 - fixed clause
            ),
            window,
        )
    ).all()
    sign_ins = (
        await s.execute(
            text(
                "SELECT id, at, user_id, event, reason_code, request_id FROM auth_events"  # nosec B608 -- fixed SQL; values are bound.
                f" WHERE {where} ORDER BY id LIMIT :n"  # noqa: S608 - fixed clause
            ),
            window,
        )
    ).all()
    accesses = (
        await s.execute(
            text(
                "SELECT id, at, principal, kind, purpose, outcome, resource_id, connection_id"  # nosec B608 -- fixed SQL; values are bound.
                f" FROM connector_access_log WHERE {where} ORDER BY id LIMIT :n"  # noqa: S608
            ),
            window,
        )
    ).all()
    calls = (
        await s.execute(
            text(
                "SELECT e.run_id, e.operation, count(*) AS n, min(e.created_at) AS first_at"
                " FROM tool_call_events e WHERE e.event = 'done'"
                " AND (CAST(:since AS timestamptz) IS NULL OR e.created_at >= :since)"
                " AND (CAST(:until AS timestamptz) IS NULL OR e.created_at < :until)"
                " GROUP BY e.run_id, e.operation ORDER BY e.run_id, e.operation LIMIT :n"
            ),
            window,
        )
    ).all()
    category_of = load_grading_config().category_by_operation
    lines: list[list[str]] = [list(HEADER)]
    for e in events:
        lines.append(
            _row(
                kind="event",
                seq=int(e.seq),
                at=e.at.isoformat(),
                actor=e.actor,
                actor_kind=e.actor_kind,
                **{"class": e.audit_class},
                action=e.action,
                resource_type=e.resource_type,
                resource_id=e.resource_id,
                route=e.route,
                request_id=e.request_id,
                outcome=e.outcome,
                support_grant=str(e.support_grant_id) if e.support_grant_id else None,
            )
        )
    for a in sign_ins:
        lines.append(
            _row(
                kind="sign_in",
                seq=int(a.id),
                at=a.at.isoformat(),
                actor=a.user_id or "unknown",
                actor_kind="user",
                **{"class": "access"},
                action=f"auth.{a.event}",
                resource_type="session",
                outcome="denied" if a.event in ("login_failed", "csrf_refused") else "ok",
                request_id=a.request_id,
                category=a.reason_code,
            )
        )
    for x in accesses:
        lines.append(
            _row(
                kind="connector_access",
                seq=int(x.id),
                at=x.at.isoformat(),
                actor=x.principal,
                actor_kind="user",
                **{"class": "access"},
                action=f"connector.{x.purpose}",
                resource_type=x.kind,
                resource_id=x.resource_id,
                outcome=x.outcome,
                route=str(x.connection_id),
            )
        )
    per_category: dict[tuple[str, str], tuple[int, datetime]] = {}
    for c in calls:
        key = (str(c.run_id), category_of.get(str(c.operation), "other"))
        n, first = per_category.get(key, (0, c.first_at))
        per_category[key] = (n + int(c.n), min(first, c.first_at))
    for (run_id, category), (n, first) in sorted(per_category.items()):
        lines.append(
            _row(
                kind="provider_calls",
                at=first.isoformat(),
                actor="6th agents",
                actor_kind="system",
                **{"class": "run"},
                action="run.provider_calls",
                resource_type="run",
                resource_id=run_id,
                outcome="ok",
                category=category,
                count=n,
            )
        )
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerows(lines)
    body = out.getvalue()
    assert_client_safe(body.splitlines())
    return body.encode(), len(lines) - 1
