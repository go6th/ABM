"""Turn the gated feature flags on for test tenants (``account_intelligence.api.flags``): the
connector, CRM and monitoring routes answer 404 for a client without its flag (fix prompt B,
task 4), so a suite that exercises them first enables the flag for its own tenants. A new
insert-only version keeps every tenant the current rule already lists."""

from __future__ import annotations

import json
from collections.abc import Iterable
from uuid import UUID

import psycopg

from account_intelligence.api.flags import GATED_FLAGS


def enable_flags(
    dsn: str,
    tenants: Iterable[UUID],
    flags: Iterable[str] = GATED_FLAGS,
    *,
    everyone: bool = False,
) -> None:
    """Add ``tenants`` to each flag's rule (a new version per flag); ``everyone``: switch the flag
    on for every client instead (percent 100), naming no tenant (the operator's flag listing
    shows the rules, so a suite that plants canary tenants must not put their ids there)."""
    wanted = [str(t) for t in tenants]
    with psycopg.connect(dsn, autocommit=True) as c, c.transaction():
        for flag in flags:
            c.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"flag:{flag}",))
            row = c.execute(
                "SELECT version, rule FROM feature_flags WHERE flag = %s"
                " ORDER BY version DESC LIMIT 1",
                (flag,),
            ).fetchone()
            version, rule = (row[0], dict(row[1])) if row else (0, {})
            listed = list(
                dict.fromkeys([*(rule.get("tenants") or []), *([] if everyone else wanted)])
            )
            new = {
                "tenants": listed[-500:],
                "plans": rule.get("plans") or [],
                "percent": 100 if everyone else int(rule.get("percent") or 0),
            }
            c.execute(
                "INSERT INTO feature_flags (flag, version, rule, created_by, created_at)"
                " VALUES (%s, %s, %s::jsonb, 'test', now())",
                (flag, version + 1, json.dumps(new)),
            )
