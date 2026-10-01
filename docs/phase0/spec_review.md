# T2 — Architecture Specification v2.0 review

Reviewer verdict per ADR, walked against the service design document and the nine attack
scenarios (A1–A9) and ten risk rows. Every ADR carries a status: **Accepted**, **Changed**
(materially amended for Phase 0) or **Deferred** (decision pushed to a later phase with a
trigger). Full text: `docs/adr/ADR-001..016`.

| ADR | Decision | Status | Notes |
|-----|----------|--------|-------|
| 001 | Pooled RLS tenancy with tier upgrade path | **Accepted** | Holds under all nine attack scenarios walked on paper; spike T10 verifies empirically. |
| 002 | LangGraph checkpoint as durability source of truth | **Accepted** | One retry owner per failure class fixes the v1 storm; T11 verifies resume. |
| 003 | Inngest outer trigger/schedule/slice loop | **Accepted** | Temporal kept as documented alternative; V4 verification pending. |
| 004 | pgvector halfvec(1536), hash partitions, iterative scan | **Accepted** | Fixes v1 D3/D12; V3 (Aurora pgvector build) pending. |
| 005 | Quarantine extractor for untrusted text | **Accepted** | Structural injection defence; adversarial corpus (T9/evals) is the gate. |
| 006 | Visibility labels + DB roles + leak detector | **Accepted** | Two-output rule enforced at the DB layer (verified in isolation suite). |
| 007 | Reserve/commit/refund wallet ledger | **Accepted** | Fixes v1 overspend under concurrency; invariants nightly-checked. |
| 008 | Canary + graph version pinning (no blue-green) | **Accepted** | Safe for multi-hour runs; rollback is a routing change. |
| 009 | Self-hosted Langfuse + OTel | **Accepted** | Keeps prompts in-boundary; one more service to operate is accepted. |
| 010 | Tiered model routing by config | **Accepted** | Model IDs live in config, re-verified via V1; eval gate on change. |
| 011 | Database host: managed Postgres now, Aurora at V1 | **Changed** | Spec assumed EKS+Aurora from MVP; Phase 0/1 use minimum-viable managed Postgres (T4 conflict C4). DR stays at V1 per spec 11.3. |
| 012 | Auth provider: external IdP, JWT claims | **Deferred** (provider choice) | Architecture accepted (JWT contract, membership re-check); vendor choice WorkOS vs Clerk deferred to Phase 1 procurement (T5/PO input). |
| 013 | Portal hosting: managed Next.js | **Changed** | Spec put the portal in EKS; a managed platform is chosen for Phase 1 (T4 conflict C4). Revisit trigger: residency requirement. |
| 014 | Vector store: pgvector in the primary DB | **Accepted** | One datastore, one isolation model; depends on V3. |
| 015 | Object storage: SSE-KMS, prefix-scoped, Object Lock | **Accepted** | MinIO-compatible locally; S3 in production. |
| 016 | Agent runtime: LangGraph state graphs | **Changed** | Spec choice kept but now explicit vs the service design's Claude Agent SDK proposal (T4 conflict C2); decided by ADR after T11 spike evidence. |

## Attack-scenario walk (A1–A9)

Each scenario was walked through the design; controls and owning tasks are in
`attack_scenarios.csv`. No scenario lacks a first defence and a backstop. The only
scenario whose empirical proof is still pending is A4 (ANN neighbour leakage) — the
canary-vector tests are part of the isolation suite and run once the live-DB gate is
green in CI (T10).

## Risk-register walk

All ten spec risk rows are carried into `risk_register.csv` with owners and tasks; two
rows were amended for Phase 0 (R6 scoped to Phase 1; R8 game-day deferred to T88 at
Phase 2, matching spec 11.3).
