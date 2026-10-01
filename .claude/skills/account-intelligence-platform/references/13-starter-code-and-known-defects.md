# 13 Starter code (ARCH 18) and the defects found in it

ARCH 18 gives load-bearing listings: `session.py` (`tenant_txn`), `0002_rls.sql`, `graph.py` (`ledgered_call`, graph), `runner.py` (`execute_slice`), `functions.py` (Inngest slice loop), `auth.py`, `runs.py` (`create_run`), `tests/isolation/test_rls.py`, Dockerfile, compose.yml, ci.yml. The source states the Python files were **compile-checked, not executed against live services** and SDK call shapes are [VERIFY].
**They are extracted into `assets/starter/` (see its README), with the defects below fixed or marked and a test suite that runs against a real PostgreSQL.** Port from there; do not retype from the PDF. This file lists what is wrong or missing, how each finding was established, and the fix.

## Defects
| # | Where | Problem | How established | Fix |
| --- | --- | --- | --- | --- |
| 1 | 18.4 `RunState` | `spent_usd` has no reducer, but the four `fetch_branch` tasks run in parallel and each returns `spent_usd`. LangGraph raises `InvalidUpdateError: At key 'spent_usd': Can receive only one value per step` | **Reproduced** with langgraph 1.2.12: four parallel `Send` branches writing an un-annotated key | `spent_usd: Annotated[float, operator.add]` (and make `baseline` the only place that sets 0.0, or drop that reset so a resume cannot zero the spend) |
| 2 | 18.3 `0002_rls.sql` | The tenant policy pattern has no `TO` clause, so it applies to every role including `public_reader`. Permissive policies are OR-ed, so `public_only` adds nothing and **`public_reader` can read private facts** of its own tenant. The two-output rule at the DB layer silently fails | **Reproduced** on PostgreSQL 16.13 (`scripts/fixtures/public_reader_leak_demo.sql`) | `CREATE POLICY tenant_isolation ON facts TO app_rw ...` and `public_only ... TO public_reader` on every table `public_reader` can read; lint rule R5 enforces it; add an isolation test that does `SET LOCAL ROLE public_reader` and expects zero private rows |
| 3 | 18.4 `ledgered_call`, 18.6 loop | ARCH 5.5 states the limit honestly: a crash between the vendor call and the ledger insert can repeat **one** call. The code goes further than that bound: there is no per-run lock or lease and no intent row, so **two concurrent identical calls both pay** (for example a slice retried by the orchestrator while the earlier worker is still running, or a LangGraph retry racing a slow call). The "one repeat per crash" bound does not hold. Spike 2's pass criterion (each paid call exactly once after 5 random kills) is also stricter than ARCH 5.5's own guarantee | **Reproduced**: two concurrent identical calls both pay (`xfail` test `test_concurrent_identical_calls_pay_once`) | Decide which guarantee the product promises and measure it. Add a **per-run lease** (advisory lock or a lease row) so only one worker executes a run at a time, and an **intent row** `(run_id, args_hash, status='started')` inserted before the call and finalised after; on resume, reconcile a `started` row (reuse a provider idempotency key where one exists, otherwise flag a possible duplicate for invoice reconciliation). Make spike 2 kill inside the window and also run two workers on one run |
| 4 | 18.8 `create_run` | `COST` is in **credits** (6 and 18) but is passed as `budget_usd` and stored as `{"usd": est}`. Credits and dollars are conflated. The PRD and ARCH use a broker cap of $5 per run and price by depth tier | Read (credits 6 and 18 are stored as `budget_usd`) | Two separate fields: `reserved_credits` (priced by depth tier, D-04) and `budget_usd` (cost cap derived from depth config); never reuse one for the other |
| 5 | 18.8 `create_run` | A replayed `Idempotency-Key` returns the earlier run without checking that the request body matches; the same key with a different domain silently returns the wrong run | **Reproduced** (`xfail` test `test_same_key_with_a_different_domain_is_rejected`) | Store a hash of the request body with the key; mismatch returns 422 or 409 |
| 6 | 18.2/18.3/compose | The listings reference tables never defined (`tenants`, `accounts`, `wallets`, `wallet_ledger`, `tool_calls`, `facts`); `GRANT ... ON facts` precedes its table; `app_rw` is created **without a password** while `compose.yml` logs in with one | **Reproduced**: 0002 fails without the missing tables; `app_rw` has no password until set | Migration 0001 is now in `assets/starter/migrations/`; add a bootstrap that sets role passwords from secrets; keep `migrator` separate. The checkpointer `setup()` must run **between** 0001 and 0002 |
| 7 | 18.6 / 9.4 | No settlement (commit and refund at the terminal state, including the FAILED branch), no reconciler, no outbox or stuck-run query, no nightly ledger invariant job, no webhook signer | Read | Build them (WP 1.5, 1.7, 2.2, 2.3); each has a test in `11-build-plan.md` |
| 8 | 18.6 loop | `step.wait_for_event(..., timeout=24h)` returns nothing on timeout, but the loop continues to the next slice and re-pauses at the same interrupt, repeatedly, up to the 60-slice ceiling | Read; SDK behaviour **[VERIFY] V4** | On timeout cancel the run, settle and refund |
| 9 | 18.5 runner | `set_config('app.tenant_id', ..., false)` is session-scoped. Safe only because the connection is private to one run and closed in `finally`; if anyone ever pools it, tenant context leaks | ARCH says so in a comment | Keep a test that the checkpoint connection is never reused and closed; never route it through a transaction-mode proxy |
| 10 | 19.2 vs 17 | Roadmap says "four spikes"; 19.2 lists five | Read | Treat 1 to 4 as the Phase 0 exit gate and run 5 once EKS exists |

Defects 1, 2, 3, 5 and 6 were reproduced in this session; 4, 7, 8, 9 and 10 were found by reading, so confirm them in the Phase 0 spikes before relying on them.

Also verified with the starter tests: the wallet reserve is atomic (ten parallel runs against 30 credits at 6 each gave exactly five successes and five 402s, balance 0); a 402 rolls back the account upsert as well; replaying the same key charges once; RLS on a partitioned parent does **not** protect a partition queried directly, so `app_rw` must have no grant on `chunks_pN` (tested, and checked by `check_rls_catalog.sql`); pgvector 0.6.0 has no `halfvec` while 0.8.0 does, and 0.8.0 exposes `hnsw.iterative_scan` (register item V3).

## Corrected fragments (verified)
```python
# graph state: every key written by parallel branches needs a reducer
class RunState(TypedDict, total=False):
    schema_version: int
    tenant_id: str
    run_id: str
    domain: str
    budget_usd: float
    spent_usd: Annotated[float, operator.add]                  # was un-annotated: InvalidUpdateError
    facts: Annotated[list[dict[str, Any]], operator.add]
    status: str
```
```sql
-- 0002_rls.sql: scope each policy to the role it is for
CREATE POLICY tenant_isolation ON facts TO app_rw
  USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
CREATE POLICY public_only ON facts FOR SELECT TO public_reader
  USING (tenant_id = (SELECT app_tenant()) AND visibility = 'public');
```
```python
# tests/isolation/test_rls.py: add this to the hard gate
async def test_public_reader_cannot_read_private_facts(tenant_a, private_fact_of_a):
    async with tenant_txn(tenant_a) as s:
        await s.execute(text("SET LOCAL ROLE public_reader"))
        n = (await s.execute(text("SELECT count(*) FROM facts WHERE visibility = 'private'"))).scalar_one()
    assert n == 0
```

## What to keep exactly as ARCH has it
`tenant_txn` using `set_config(..., true)` inside an explicit transaction; `app_tenant()` returning NULL when unset; `(SELECT app_tenant())` in policies; `WITH CHECK` on every policy; FORCE RLS; non-owner `app_rw`; `ON CONFLICT (tenant_id, idempotency_key) DO NOTHING` for idempotent run creation; atomic `UPDATE wallets ... WHERE balance >= :e` inside the same transaction as the run row; event id = run id; tenant from the token only; `ON CONFLICT (run_id, args_hash) DO NOTHING` for the tool ledger; thread id `<tenant>:<run>`; `saver.setup()` only in the migration job.
