"""The nightly isolation check (admin console safeguard; platform rows in ``isolation_checks``).

One pass proves, against the live database and the live API, that no client can read another
client's data:

1. **Structure**: every table with a ``tenant_id`` has row level security enabled and forced, and
   the runtime role neither bypasses it nor owns a table (the RLS catalog check, read from the
   system catalog).
2. **Tables**: inside the probe client's own transaction, every tenant table the runtime role can
   read shows no row of any other client. Every other client's rows (real clients included) are
   the canaries, so nothing is planted in a real client.
3. **Routes**: a real signed-in session of the probe client's check user calls every tenant read
   route that takes no identifier, and the routes that take an account domain with the canary
   client's account; the canary never appears and that account is never found.

The probe and canary clients are a dedicated pair (``isolation_check_pair``), created on the
first pass through the normal provisioning path; the console and the platform jobs leave them
out. Each pass writes one ``isolation_checks`` row (passed, clients covered, failures, and the
failing table or route names only); a failure is logged at error level so the operators' alert
fires, and the console's pill turns red.

``latest`` is what the console shows: Passed or Failed with the time, or Not run yet.

GUARANTEE(test_the_nightly_isolation_check_records_a_pass_and_catches_a_leak)
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text

from obs.logging import logger

log = logger(__name__)
PROBE_USER = "system:isolation-check"
# Read routes a probe never calls: event streams (they stay open) and file downloads (bodies are
# files, not text to scan); they are proven by the isolation suite on every change.
NOT_PROBED = frozenset(
    {
        "/v1/runs/{run_id}/events",
        "/v1/accounts/{domain}/deliverables/download",
        "/v1/accounts/{domain}/sheets/{sheet}/xlsx",
    }
)

STRUCTURE_SQL = (
    "SELECT 'no forced RLS: ' || c.relname FROM pg_class c"
    " JOIN pg_namespace n ON n.oid = c.relnamespace"
    " JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped"
    " WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition"
    " AND NOT (c.relrowsecurity AND c.relforcerowsecurity)"
    " UNION ALL SELECT 'runtime role bypasses RLS' FROM pg_roles"
    " WHERE rolname = current_user AND (rolsuper OR rolbypassrls)"
    " UNION ALL SELECT 'runtime role owns ' || c.relname FROM pg_class c"
    " JOIN pg_roles r ON r.oid = c.relowner WHERE r.rolname = current_user"
    " AND c.relkind IN ('r', 'p', 'v', 'm')"
)
TENANT_TABLES_SQL = (
    "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
    " JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped"
    " WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition"
    " AND has_table_privilege(current_user, c.oid, 'SELECT') ORDER BY c.relname"
)


@dataclass
class CheckResult:
    """One pass: what was covered and what failed (names only, never row content)."""

    clients: int = 0
    tables: int = 0
    routes: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """No failure of any kind."""
        return not self.failures


async def latest(plain: Any) -> dict[str, Any]:
    """The most recent pass (read without tenant context: the table is platform-level)."""
    row = (
        await plain.execute(
            text(
                "SELECT ran_at, passed, workspaces_checked, failures FROM isolation_checks"
                " ORDER BY ran_at DESC LIMIT 1"
            )
        )
    ).first()
    if row is None:
        return {"status": "not_run", "ran_at": None, "workspaces_checked": 0, "failures": 0}
    return {
        "status": "passed" if row.passed else "failed",
        "ran_at": row.ran_at.isoformat(),
        "workspaces_checked": int(row.workspaces_checked),
        "failures": int(row.failures),
    }


async def check_clients(plain: Any) -> set[UUID]:
    """The two check clients (empty before the first pass); every list of real clients leaves
    them out."""
    row = (
        await plain.execute(text("SELECT probe_client, canary_client FROM isolation_check_pair"))
    ).first()
    return set() if row is None else {UUID(str(row.probe_client)), UUID(str(row.canary_client))}


async def ensure_pair(plain_factory: Any, txn: Any, now: datetime) -> tuple[UUID, UUID, str]:
    """The probe client, the canary client and the canary word; created on the first pass (the
    canary client holds one account whose domain carries the canary)."""
    from account_intelligence.runs import lifecycle
    from admin import tenants
    from identity import membership

    async with plain_factory() as plain:
        row = (
            await plain.execute(
                text("SELECT probe_client, canary_client, canary FROM isolation_check_pair")
            )
        ).first()
        if row is not None:
            return UUID(str(row.probe_client)), UUID(str(row.canary_client)), str(row.canary)
        canary = f"isolation-canary-{secrets.token_hex(6)}"
        probe = await tenants.create(plain, name="6th isolation check (probe)")
        target = await tenants.create(plain, name="6th isolation check (canary)")
    async with txn(probe) as s:
        await membership.add(s, user_id=PROBE_USER, role="analyst", actor="system", now=now)
    async with txn(target) as s:
        await lifecycle.add_account(s, target, f"{canary}.example")
    async with plain_factory() as plain:
        await plain.execute(
            text(
                "INSERT INTO isolation_check_pair (probe_client, canary_client, canary)"
                " VALUES (:p, :c, :w)"
            ),
            {"p": probe, "c": target, "w": canary},
        )
    return probe, target, canary


async def probe_tables(txn: Any, probe: UUID) -> tuple[int, list[str]]:
    """Inside the probe client's transaction, count other clients' rows in every tenant table."""
    failures: list[str] = []
    async with txn(probe) as s:
        tables = [str(r[0]) for r in await s.execute(text(TENANT_TABLES_SQL))]
        failures += [str(r[0]) for r in await s.execute(text(STRUCTURE_SQL))]
        for table in tables:
            # the name comes from the system catalog, quoted as an identifier
            quoted = '"' + table.replace('"', '""') + '"'
            seen = await s.scalar(
                text(f"SELECT count(*) FROM {quoted} WHERE tenant_id <> :p"),  # nosec B608 -- catalog name
                {"p": probe},
            )
            if seen:
                failures.append(f"table {table} shows another client's rows")
    return len(tables), failures


def read_routes() -> list[str]:
    """Every tenant read route a session may call that takes no identifier or only an account
    domain (the catalog in ``api.policy``)."""
    from account_intelligence.api.policy import ROUTE_POLICIES

    out = []
    for (method, path), policy in sorted(ROUTE_POLICIES.items()):
        if method != "GET" or "session" not in policy.principal_types or path in NOT_PROBED:
            continue
        params = {p.split("}")[0] for p in path.split("{")[1:]}
        if params <= {"domain"} and path.startswith("/v1/"):
            out.append(path)
    return out


async def probe_routes(
    app: Any, txn: Any, probe: UUID, canary: str, now: datetime
) -> tuple[int, list[str]]:
    """Call the read routes as the probe client's check user, through the real sign-in session."""
    import httpx

    from identity import sessions

    policy = sessions.load_policy()
    async with txn(probe) as s:
        token, _ = await sessions.create(s, user_id=PROBE_USER, now=now, policy=policy)
    failures: list[str] = []
    routes = read_routes()
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://isolation-check.internal",
            cookies={policy.session_cookie: token},
            timeout=30,
        ) as client:
            # LOOP(isolation route probe): cap=the read routes of the catalog; exit=all probed
            for path in routes:
                url = path.replace("{domain}", f"{canary}.example")
                reply = await client.get(url)
                if canary in reply.text:
                    failures.append(f"route {path} shows the canary client's data")
                elif "{domain}" in path and reply.status_code == 200:
                    failures.append(f"route {path} found the canary client's account")
    finally:
        async with txn(probe) as s:
            await sessions.revoke_all(s, PROBE_USER, "isolation_check_done", now)
    return len(routes), failures


async def run_check(app: Any, plain_factory: Any, txn: Any, now: datetime) -> CheckResult:
    """One pass (see the module docstring); records it and returns what it found."""
    probe, _target, canary = await ensure_pair(plain_factory, txn, now)
    result = CheckResult()
    async with plain_factory() as plain:
        others = {
            UUID(str(r.tenant_id))
            for r in await plain.execute(text("SELECT tenant_id FROM operator_tenant_overview()"))
        }
        result.clients = len(others - await check_clients(plain))
    try:
        result.tables, failures = await probe_tables(txn, probe)
        result.failures += failures
        result.routes, failures = await probe_routes(app, txn, probe, canary, now)
        result.failures += failures
    except Exception as error:  # a pass that could not finish is a failed pass, never a pass
        result.failures.append(f"check could not finish ({type(error).__name__})")
    detail = "; ".join(result.failures)[:2000] or None
    async with plain_factory() as plain:
        await plain.execute(
            text(
                "INSERT INTO isolation_checks (ran_at, passed, workspaces_checked, failures,"
                " detail) VALUES (:at, :ok, :n, :f, :d)"
            ),
            {
                "at": now,
                "ok": result.passed,
                "n": result.clients,
                "f": len(result.failures),
                "d": detail,
            },
        )
    if result.passed:
        log.info(
            "isolation_check_passed",
            extra={"tables": result.tables, "routes": result.routes, "clients": result.clients},
        )
    else:
        log.error("isolation_check_failed", extra={"failures": len(result.failures)})
    return result
