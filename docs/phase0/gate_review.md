# T14 — Phase 0 gate review

Review date: 2026-10-01
Scope: all Phase 0 tasks T1–T14 against their written exit criteria (roadmap PDF, tasks 1–14).

## Verdict: **AMEND** (conditional pass — one hard blocker, no design failures)

Recommendation: proceed to Phase 1 **only after** the single amendment below is cleared.
No architectural decision from ADR-001..016 needs to change.

## Gate items

| # | Item | Exit criterion | Status | Evidence |
|---|------|----------------|--------|----------|
| 1 | T1 reference set | 33 coverage rows (5+28), 4 grades, 13-step run documented | **PASS** | `python scripts/check_reference.py` → OK |
| 2 | T2 spec review + registers | 16 ADRs reviewed; A1–A9 attacks; R1–R10 risks | **PASS** | `docs/phase0/spec_review.md`, `attack_scenarios.csv`, `risk_register.csv` |
| 3 | T3 requirements | ≥ 120 REQ-IDs across 16 prefixes, machine-checked | **PASS** | `python scripts/check_requirements.py` → 131 REQ-IDs |
| 4 | T4 conflict register | C1–C10 decided or deferred-with-trigger | **PASS** | `python scripts/check_conflicts.py` → OK |
| 5 | T5 product-owner decisions | PO-1..PO-10 signed; margin ≥ 70% recomputed | **PASS** | `product_owner_decisions.md` ($23.48 / $19.08 / $15.62); sign-off lines present |
| 6 | T6 legal | briefing, subprocessor register, DPA template w/ counsel placeholder | **PASS (docs)** | `docs/legal/*`; V5/V7/V10 remain open by design |
| 7 | T7 verification register | V1–V11 with owner + target date | **PASS** | `python scripts/check_verification.py` → 11 items |
| 8 | T8 ADRs | 16 ADRs Accepted; cost ≤ $600/mo | **PASS** | `python scripts/check_adrs.py` → OK ($550) |
| 9 | T9 golden set | protocol + schema + template + agreement ≥ 0.80 harness | **PASS** | `python scripts/check_golden_set.py` → OK |
| 10 | T10 isolation | live-Postgres RLS suite green, incl. 100-tenant storm | **BLOCKED** | see below — code complete, live run not yet possible |
| 11 | T11 durability | exactly-once ledger across 5 random kills | **PASS (offline)** | `pytest tests/spike -q -m spike` → 3 passed, 2 live tests skipped; `simulate_kills.py` ready |
| 12 | T12 prompt-cache ratio | min ratio call 2+ ≥ 0.70 over 10 calls | **PASS (dry-run)** | `spikes/spike3_cache/run.py` → 1.00; `--invalidate` demo FAILs as designed; live pending |
| 13 | T13 broker economics | 20×5 metered shim; coverage ≥ 0.80; ≤ $6/run; people decision | **PASS (dry-run)** | `spikes/spike4_economics/run.py` → coverage 0.85, mean $2.99 ≤ $6; live pending on V7 |
| 14 | T14 gate review | this document | **PASS** | this file |

## The blocker (T10 live verification)

**What is done.** Migration `0001_initial.py` (tables, ENABLE+FORCE RLS, fail-closed
`app_tenant()`, role grants), runtime role `app_rw` (NOBYPASSRLS, non-owner), `tenant_txn`
transaction-local `set_config`, and a 10-scenario isolation suite including the
100-tenant concurrent storm, pool-leak and checkpoint-prefix checks
(`tests/isolation/test_rls.py`). All static/role checks in the suite pass offline.

**What is missing.** The suite must run against live Postgres 16 + pgvector
(`docker compose up -d db && alembic upgrade head && pytest tests/isolation`).
The daemon runs, but the executing user is not in the `docker` group
(`/var/run/docker.sock` is `root:docker` 0660; `sudo -n` requires a password), so the
container cannot be started from this session.

**Required input (one of):**
1. `sudo usermod -aG docker sakib` then re-login (or `newgrp docker`), **or**
2. an operator runs `make db-up && make migrate && make isolation` once.

**Consequence if skipped:** the strongest isolation invariants (I1/I2/I9) remain
design-verified but not evidence-verified. The T14 gate must not flip to **Go** until the
isolation suite passes on live Postgres.

## Risks accepted at this gate

| Risk | Register | Accepted at gate? |
|------|----------|-------------------|
| Live isolation evidence outstanding | R2 (RLE misconfig) | **No** — blocks Go |
| Live cache/economics numbers outstanding | V1, V7 | Yes for dry-run gate; live values due by target dates |
| Legal confirmations outstanding | V5, V7, V10 | Yes — already enforced as product blockers, not documentation debts |

## Sign-off

- Recommendation: **Amend** → clear the T10 live run, then re-issue this review as **Go**.
- Product owner: ____________________  Date: __________
- Tech lead: ____________________  Date: __________
