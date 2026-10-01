# 05 Data model (ARCH ch. 7 and 18.3; PRD stable IDs and tracker)

## Principles
- **Every tenant-owned table has `tenant_id NOT NULL`, an FK to `tenants`, and forced RLS with `WITH CHECK`.**
- Facts are **append-only** with `superseded_at`; history and deltas must be reconstructible.
- `wallets` holds the current balance; `wallet_ledger` is the audit trail; a nightly job verifies balance = ledger sum.
- Ledger and audit tables are **insert-only** for the application role (no UPDATE or DELETE grants).
- Two DB roles: `app_rw` (RLS enforced, NOBYPASSRLS, not owner) and `migrator` (DDL only, never at runtime). A third `public_reader` (NOLOGIN) is switched to with `SET LOCAL ROLE` for public rendering and has **no grant on private rows or on `chunks`**.
- Migrations: Alembic, **expand then contract**, each backward compatible with the previous app release; the migration job also runs the LangGraph checkpointer `setup()` (never at runtime).

## Tables (logical model)
| Table | Key columns | Notes |
| --- | --- | --- |
| `tenants` | id, plan, region, isolation_tier, kms_key_arn, policy_json | policy_json holds redaction tier, public-cache setting, etc. |
| `memberships` | tenant_id, user_id, role (admin / seller / viewer) | |
| `accounts` | id, tenant_id, domain (**unique per tenant**), watch_config_json, refresh_cadence | careers_url and its verified_at belong here |
| `account_runs` | id, tenant_id, account_id, status, graph_version, budget_json, reserved_credits, **idempotency_key (unique per tenant)**, created_at | |
| `tool_calls` (ledger) | run_id, tenant_id, tool, args_hash (**unique per run**), response / response_ref (S3), cost_usd, created_at | provider identity is internal; never exposed to clients |
| `sources` | id, tenant_id, url, domain, tier (T1 to T4), lineage_group, content_hash, **source_code (stable per account, e.g. S-0001)** | code assigned once, never renumbered or reused |
| `facts` | id, tenant_id, account_id, attribute_key, value_json, fingerprint, grade, confidence, status, **visibility (public / private)**, superseded_at | plus the product-language grade and `read_in_primary_this_run` (see D-01) |
| `fact_evidence` | fact_id, source_id, lineage_group, quote_ref, retrieved_at | **no fact without at least one evidence row** |
| `coverage_rows` | run_id, tenant_id, row_key, pass, priority, status, proof (queries, tool category, result count), last_checked_at | seeded with the 33 PRD rows; a run cannot complete while a High row has no status |
| `people` | account_id, name, profile_url (dedupe key), title, level, lane, priority (A/B/C), tenure fields, excluded + reason, merged_into | merged and excluded rows are kept, never deleted |
| `job_postings`, `tech_detections` | per PRD: class, platform sentence, full text read flag; first_seen, last_seen, cross-check result | A detection date range alone is a lead |
| `alert_rules`, `alerts` | rule type, threshold; alert: fact_delta_id, dedupe_key, delivered_at | rules cannot reference another tenant's account |
| `wallets`, `wallet_ledger` | ledger: entry (reserve / commit / refund / topup / adjustment), amount, balance_after | CHECK balance >= 0; insert-only |
| `documents`, `chunks` (Phase B) | documents: s3_key, kms_ctx, parse_status, policy. chunks: account_id, doc_id, **visibility = 'private' (CHECK)**, content, tsv (generated), `embedding halfvec(1536)` | `chunks` **hash-partitioned by tenant_id** (16), HNSW cosine m=16, ef_construction=64 per partition |
| `audit_log` | actor, action, resource, before/after hash, time | append-only; archive to S3 Object Lock |
| LangGraph `checkpoints`, `checkpoint_blobs`, `checkpoint_writes` | thread_id = `<tenant>:<run>` | prefix policy |

## Vector design
Embedding 1536 dims via the provider's dimension option (a 3072-d vector exceeds the 2000-d HNSW limit for `vector`). Column `halfvec(1536)` (16-bit float, **not** binary quantisation). HNSW cosine, `ef_search` tuned per query class. Filtered search with iterative scan so tenant and account filters still return k rows **[VERIFY pgvector version]**. Hybrid: vector top-50 + full-text top-50, reciprocal rank fusion, rerank, return top 8. Store `embedding_model` and version columns; re-embedding is dual-write and swap, a migration not an incident. Rule of thumb 6 to 10 KB per chunk; one million chunks about 6 to 10 GB; keep hot partitions in the buffer cache.

## Retention
Checkpoints: until run terminal + 7 days, then delete the thread. Raw broker payloads: 30 days hot (S3 Standard), 12 months archive, then delete or on tenant request. Facts and evidence: current + history for deltas; superseded to Parquet after 24 months; delete on tenant deletion. Chunks and documents: until the tenant deletes; cascade and propagate to S3 within 24 h. Run metadata 13 months then Parquet. **Wallet ledger: permanent** (financial record). Audit log: 90 days queryable, 7 years in S3 with Object Lock.

## Stable source IDs (PRD) on top of the ARCH schema
Assign a per-account counter code (`S-0001`...) on first sight of a normalised URL (unique on `(account_id, url_hash)`), allocate under a row lock on a counter row, **never change or reuse**. Every export (Summary, Evidence, People, Jobs, Tech) cites these codes. A refresh reuses codes so the Evidence file stays stable. Raw broker payloads are stored with neutral object keys (`tenant/account/run/<hash>.json.gz`); provider identity lives only in an internal table never exposed through client-safe views.
