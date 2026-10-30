"""A referentially consistent SYNTHETIC world for the account purge tests (migration 0096).

``plant_scope`` fills one account of one tenant with rows in every table the purge plan classifies
as erase, scrub or keep (``config/deletion.yaml`` ``account_purge``), parents first, with the
foreign keys and the logical links (fact ids, thread ids, run ids) pointing at each other, so a
delete in the wrong order or a predicate that misses a link shows up as a real database error or a
row left behind. Rows are planted by the superuser with triggers and foreign keys off (the same
catalog-driven planter the isolation matrix uses); the purge itself then runs with every
constraint on.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import yaml

from tenancy import lifecycle
from tests.isolation.matrix.plant import Table, plant, tenant_tables

CANARY = "CANARY-PURGE"
NOW = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
# columns that point at a row of another table without a foreign key
LOGICAL = {
    "fact_id": "facts",
    "alert_id": "alerts",
    "event_id": "signal_events",
    "event_a": "signal_events",
    "event_b": "signal_events",
    "delta_id": "deltas",
    "write_id": "crm_writes",
    "package_id": "export_packages",
    "lead_verification_id": "lead_verifications",
    "intent_id": "prepulled_intents",
    "outbox_id": "notification_outbox",
    "refresh_run_id": "refresh_runs",
    "document_id": "documents",
    "table_id": "document_tables",
}
TERMINAL = {
    "account_runs": {"status": "COMPLETED", "finished_at": "2026-10-01T00:00:00+00:00"},
    "runs": {
        "status": "completed",
        "started_at": "2026-10-01T00:00:00+00:00",
        "finished_at": "2026-10-01T01:00:00+00:00",
        "config_version": "v1",
        "credit_cap": 10,
        "run_type": "full",
    },
    "research_jobs": {"status": "done", "finished_at": "2026-10-01T00:00:00+00:00"},
}


@dataclass
class Scope:
    """One planted account: its ids and the rows planted per table (primary key plus values)."""

    tenant: UUID
    account: UUID
    run: UUID  # the account_runs id; ``runs`` holds a second run id in ``legacy_run``
    legacy_run: UUID
    domain: str
    rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def count(self, table: str) -> int:
        """Rows planted in ``table`` for this account."""
        return len(self.rows.get(table, []))


def matrix_plants() -> dict[str, dict[str, Any]]:
    """The per-table column overrides of the isolation matrix."""
    with open("tests/isolation/matrix/matrix.yaml") as f:
        tables = yaml.safe_load(f)["tables"]
    return {k: dict((v or {}).get("plant") or {}) for k, v in tables.items()}


def _fks(admin: psycopg.Connection[Any]) -> dict[str, list[tuple[list[str], str, list[str]]]]:
    """Foreign keys per child table: (child columns, parent table, parent columns)."""
    out: dict[str, list[tuple[list[str], str, list[str]]]] = {}
    rows = admin.execute(
        """
        SELECT cl.relname,
               array(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY k(n, o)
                      JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.n
                     ORDER BY k.o),
               cf.relname,
               array(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(n, o)
                      JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.n
                     ORDER BY k.o)
          FROM pg_constraint con JOIN pg_class cl ON cl.oid = con.conrelid
          JOIN pg_class cf ON cf.oid = con.confrelid
          JOIN pg_namespace n ON n.oid = cl.relnamespace
         WHERE con.contype = 'f' AND n.nspname = 'public' AND cl.relname <> cf.relname
        """
    ).fetchall()
    for child, ccols, parent, pcols in rows:
        out.setdefault(child, []).append((list(ccols), parent, list(pcols)))
    return out


def plant_scope(
    admin: psycopg.Connection[Any],
    tenant: UUID,
    domain: str,
    *,
    deleted: bool = True,
    per_table: int = 2,
) -> Scope:
    """Plant one account of ``tenant`` with a COMPLETED run and rows in every planned table."""
    plan = lifecycle.load_account_purge_plan()
    tables: dict[str, Table] = {t.name: t for t in tenant_tables(admin)}
    matrix = matrix_plants()
    fks = _fks(admin)
    owner = f"owner-{secrets.token_hex(3)}"
    account_over: dict[str, Any] = {
        "domain": domain,
        "owner_user_id": owner,
        "name": f"{CANARY} Name",
        "industry": f"{CANARY} industry",
        "pursuing_client": f"{CANARY} client",
        "careers_url": "https://careers.synthetic.example/jobs",
        "careers_url_verified_at": "2026-10-01T00:00:00+00:00",
        "careers_fetch_ref": f"{CANARY}-ref",
        "refresh_cadence": "weekly",
        "next_refresh_at": "2030-01-01T00:00:00+00:00",
        "primary_lane": "salesforce",
        "lanes": ["salesforce"],
        "watch_config_json": psycopg.types.json.Jsonb({"canary": CANARY}),
        "entity_lock": psycopg.types.json.Jsonb({"canary": CANARY}),
        "entity_lock_at": "2026-10-01T00:00:00+00:00",
    }
    if deleted:
        account_over |= {"deleted_at": "2026-10-20T00:00:00+00:00", "deleted_by": owner}
    acct = plant(admin, tables["accounts"], tenant, account_over)
    scope = Scope(tenant, acct["id"], uuid4(), uuid4(), domain)
    scope.rows["accounts"] = [acct]
    base = {"owner_user_id": owner}

    def put(name: str, extra: dict[str, Any] | None = None) -> None:
        table = tables[name]
        cols = {c.name for c in table.columns}
        over: dict[str, Any] = {**matrix.get(name, {}), **base}
        if "account_id" in cols:
            over["account_id"] = scope.account
        if "run_id" in cols:
            over["run_id"] = scope.run
        if table.key_column == "thread_id":
            over["thread_id"] = f"{tenant}:{scope.run}"
        for col, parent in LOGICAL.items():
            if col in cols and scope.rows.get(parent):
                over[col] = scope.rows[parent][0]["id"]
        for ccols, parent, pcols in fks.get(name, []):
            if parent in scope.rows and scope.rows[parent]:
                for c, p in zip(ccols, pcols, strict=True):
                    if c != "tenant_id" and p in scope.rows[parent][0]:
                        over[c] = scope.rows[parent][0][p]
        over |= TERMINAL.get(name, {})
        over |= extra or {}
        for _ in range(per_table):
            fresh = {k: (v() if callable(v) else v) for k, v in over.items()}
            try:
                row = plant(admin, table, tenant, fresh)
            except psycopg.errors.UniqueViolation:
                break  # the table's own key allows one row per account
            scope.rows.setdefault(name, []).append(row)

    # the runs first: every other table points at them
    run_ids = {"account_runs": scope.run, "runs": scope.legacy_run}
    for name, rid in run_ids.items():
        table = tables[name]
        over = {**matrix.get(name, {}), **base, "account_id": scope.account, "id": rid}
        over |= TERMINAL[name]
        if name == "account_runs":
            over |= {
                "run_input": psycopg.types.json.Jsonb({"goal": f"{CANARY} goal"}),
                "stop_detail": f"{CANARY} stop",
            }
        else:
            over |= {"status": "failed", "error": f"{CANARY} error"}
            over.pop("started_at", None)
            over |= {"started_at": "2026-10-01T00:00:00+00:00"}
        row = plant(admin, table, tenant, over)
        scope.rows[name] = [row]
    # parents first = the erase list reversed (it is ordered children first)
    order = [t for t in reversed(plan.erase) if t not in ("accounts",)]
    special = {
        "documents": {"sha256": secrets.token_hex(32)},
        "sources": {
            "first_seen_run_id": None,
            "last_seen_run_id": None,
            "first_seen_account_run_id": scope.run,
            "last_seen_account_run_id": scope.run,
        },
    }
    for name in order:
        extra = dict(special.get(name, {}))
        if name == "sources":
            extra["url"] = f"https://sources.synthetic.example/{secrets.token_hex(4)}"
            extra["url_normalized"] = extra["url"]
            extra["url_hash"] = hashlib.sha256(extra["url"].encode()).hexdigest()
        if name == "signal_events":
            extra["fingerprint"] = lambda: secrets.token_hex(32)
        if name == "signal_event_links" and len(scope.rows.get("signal_events", [])) > 1:
            first, second = sorted(str(r["id"]) for r in scope.rows["signal_events"][:2])
            extra |= {"event_a": first, "event_b": second}
        if name == "raw_files" and scope.rows.get("documents"):
            extra["sha256"] = scope.rows["documents"][0]["sha256"]
        put(name, extra)
    for name in plan.scrub:
        if name in ("account_runs", "runs"):
            continue
        extra = {}
        if name == "research_jobs":
            extra = {"goal": f"{CANARY} goal", "failure_reason": f"{CANARY} why"}
        if name == "tool_calls":
            extra = {
                "response": psycopg.types.json.Jsonb({"canary": CANARY}),
                "response_ref": f"tenants/{tenant}/accounts/{scope.account}/runs/{scope.run}/raw/x",
                "args_hash": secrets.token_hex(32),
            }
        if name == "llm_calls":
            extra = {"output_json": psycopg.types.json.Jsonb({"canary": CANARY})}
        put(name, extra)
    for name in plan.keep:
        extra = {}
        if name == "credit_entries" and scope.rows.get("research_jobs"):
            extra = {"job_id": scope.rows["research_jobs"][0]["id"]}
        put(name, extra)
    return scope


def snapshot(admin: psycopg.Connection[Any], tenant: UUID) -> dict[str, int]:
    """Rows per tenant table (checkpoint tables by the tenant prefix of their thread id)."""
    out: dict[str, int] = {}
    for t in tenant_tables(admin):
        if t.key_column == "tenant_id":
            sql = f"SELECT count(*) FROM {t.name} WHERE tenant_id = %s"  # noqa: S608
            out[t.name] = int(admin.execute(sql, (tenant,)).fetchone()[0])  # type: ignore[index]
        else:
            sql = f"SELECT count(*) FROM {t.name} WHERE starts_with(thread_id, %s)"  # noqa: S608
            out[t.name] = int(admin.execute(sql, (f"{tenant}:",)).fetchone()[0])  # type: ignore[index]
    return out
