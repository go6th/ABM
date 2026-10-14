"""Seed two SYNTHETIC pilot tenants (Phase 3 WS9, PL2) for onboarding rehearsals and demos.

Each tenant is provisioned through the operator plane's definer function, assigned the pilot plan,
and given three to five synthetic accounts (reserved ``.example`` domains) through the same
limit check, account write and usage event the API uses. No run is started, no provider is
called, nothing is sent. It is NOT the real pilot: "two pilot tenants live" stays Blocked until
real onboarding (docs/phase3/pilot_onboarding.md).

Refuses unless ``APP_ENV`` is ``dev``, ``local`` or ``test``; every domain must end in
``.example``. Prints the tenant ids as JSON.

Usage: ``APP_ENV=dev DATABASE_URL=... python scripts/seed_pilot.py [--accounts 3,5]``
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from typing import Any

ALLOWED_ENVS = frozenset({"dev", "local", "test"})


def plan_tenants(counts: tuple[int, int]) -> list[dict[str, Any]]:
    """The two synthetic tenants and their account domains (pure)."""
    out = []
    for label, n in zip(("a", "b"), counts, strict=True):
        if not 3 <= n <= 5:
            raise ValueError("a pilot tenant has three to five accounts")
        out.append(
            {
                "name": f"Synthetic Pilot {label.upper()}",
                "domains": [f"pilot-{label}-{i}.example" for i in range(1, n + 1)],
            }
        )
    return out


async def seed(counts: tuple[int, int], now: datetime) -> list[dict[str, Any]]:
    """Provision and fill the tenants; returns their ids and domains."""
    from account_intelligence.db.session import tenant_txn, untenanted_session
    from account_intelligence.runs.lifecycle import add_account
    from admin import quotas, tenants
    from metering import events, limits
    from tenancy import lifecycle

    made = []
    for t in plan_tenants(counts):
        if not all(d.endswith(".example") for d in t["domains"]):
            raise ValueError("synthetic domains only")
        async with untenanted_session() as plain:
            tid = await tenants.create(plain, name=t["name"])
        async with tenant_txn(tid) as s:
            st = await lifecycle.state(s)
            await lifecycle.append(s, st, "created", actor="seed_pilot", plane="system", now=now)
            await quotas.assign_plan(
                s, plan_code="pilot", assigned_by="seed_pilot", reason_code="pilot_seed", now=now
            )
            for domain in t["domains"]:
                await limits.consume(s, "accounts", now)
                _, created = await add_account(s, tid, domain)
                if created:
                    await events.record(s, "account_added", domain, now)
        made.append({"tenant_id": str(tid), "name": t["name"], "domains": t["domains"]})
    return made


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--accounts", default="3,5", help="accounts per tenant, e.g. 3,5")
    args = ap.parse_args(argv)
    if os.environ.get("APP_ENV") not in ALLOWED_ENVS or any(
        os.environ.get(name) for name in ("K_SERVICE", "CLOUD_RUN_JOB")  # never on Cloud Run
    ):
        print("seed_pilot: refused (APP_ENV must be dev, local or test)", file=sys.stderr)
        return 2
    a, b = (int(x) for x in args.accounts.split(","))
    print(json.dumps(asyncio.run(seed((a, b), datetime.now(UTC))), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
