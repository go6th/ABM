# 03 Tenancy, isolation and security (ARCH ch. 4, 14; SAAS ch. 3)

Isolation is the paramount constraint. Build it **first** and test it on every build. Any single layer failing must not produce a cross-tenant disclosure.

## Six layers
L1 edge + identity (JWT signature, tenant claim, WAF, SSO/SAML, short-lived tokens) · L2 service authorization (RBAC admin/seller/viewer; **tenant from the token only, never from the payload**) · L3 database RLS (FORCE RLS, non-owner role, txn-local set_config, fail-closed) · L4 vector and checkpoint isolation (hash partitions, `thread_id` prefix policy) · L5 cryptographic isolation (per-tenant KMS keys; crypto-shredding) · L6 output firewall (visibility labels, public-role renderer, leak detector). **L3 to L6 must hold even if L2 has a bug.**

## Tiers
Pooled (default; shared cluster, forced RLS, hash-partitioned vectors, shared KMS key with tenant encryption context) · Bridge (dedicated vector partitions + per-tenant KMS key) · Silo (dedicated Aurora cluster, optional dedicated node pool and namespace, per-tenant key; priced into the plan).

## RLS correctness checklist (each item enforced by a test or a migration lint)
| Rule | Why | Enforced by |
| --- | --- | --- |
| Services connect as a **non-owner role without BYPASSRLS** | Owners and BYPASSRLS skip policies | Migration lint; startup self-check query |
| `FORCE ROW LEVEL SECURITY` on every tenant table | Applies policies even to the owner | Migration lint |
| Tenant set with `set_config('app.tenant_id', :tid, **true**)` inside an **explicit transaction** | Transaction-local; cannot leak through the pool | `tenant_txn()` is the only DB entry point; pooled-leak test |
| Policy reads a fail-closed accessor (`NULLIF(...,'')::uuid`) | Unset value matches no rows | Unset-context test |
| Policy uses `(SELECT app_tenant())` | Evaluated once per query; keeps index plans | Query-plan regression test |
| `WITH CHECK` on every policy | Blocks writing rows into another tenant | Cross-tenant write test |
| Views are `security_invoker`; `SECURITY DEFINER` functions reviewed and listed | Definer runs with owner rights | CODEOWNERS on migrations |
| **Agents have no SQL tool** | An LLM must never compose a query | Tool registry contract test |
| Cross-tenant jobs (reconciler) use a narrow definer function returning **IDs only** | No broad BYPASSRLS role in the runtime path | Role inventory test |
| **Every policy names its role (`TO app_rw`, `TO public_reader`)**; never leave a tenant policy unscoped on a table that `public_reader` can read | Permissive policies are OR-ed and an unscoped policy applies to every role, so the public renderer would read private rows (**reproduced**, ARCH 18.3 pattern) | Lint rule R5; `check_rls_catalog.sql`; `test_public_reader_cannot_read_private_facts` |
| `app_rw` has **no grant on partition children** (`chunks_pN`), only on the parent | RLS on a partitioned parent does not protect a partition queried directly (tested) | `check_rls_catalog.sql`; partition test |
| Catalog check after migrating: every table with a `tenant_id` or `thread_id` column has RLS enabled **and forced** | Regex lint cannot see DO-loop DDL | `scripts/check_rls_catalog.sql` in CI |

**Pooling trap.** `SET LOCAL` outside an explicit transaction is a no-op (this was a v1 defect). Request-path code uses transaction-local settings (compatible with transaction pooling). Workers use a **dedicated connection per run**, direct to the writer or via a session-mode pooler, with session-scoped `set_config(..., false)` set on a connection that is private to one run and closed in `finally`.

## Checkpoint, vector, object isolation
- **Checkpoints:** LangGraph tables are keyed by `thread_id` and have no FK to join. Use `thread_id = '<tenant_uuid>:<run_uuid>'` and a **prefix policy** (`starts_with(thread_id, (SELECT app_tenant())::text || ':')`) on `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`.
- **Vectors:** `chunks` hash-partitioned by tenant (16 partitions; large tenants get dedicated); iterative index scans so filtered ANN still returns k rows; per-tenant recall and leakage tests with planted canary vectors.
- **Objects:** `tenant_id/account_id/` prefixes, SSE-KMS with encryption context carrying the tenant id; worker IAM only through short-lived, prefix-scoped credentials issued per job; presigned URLs short-lived and single-object.

## Cryptographic isolation and offboarding
Aurora and S3 encrypted with KMS; bridge and silo add a per-tenant customer-managed key. Offboarding schedules key deletion after the contractual retention window (crypto-shredding), which covers backups without rewriting them. Deletion on request removes derived data, raw broker results and embeddings, confirmed to the client; public cache entries are not tenant data.

## Attack scenarios the design must survive
A1 edited JWT tenant claim (signature, issuer, audience, expiry checks; membership re-check; RLS binds to the verified tenant) · A2 injected web text tells the agent to fetch another tenant's data (quarantine extractor; tools take **no tenant parameter**; alert on RLS-denied spikes) · A3 agent coaxed into SQL (no SQL tool; non-owner role) · A4 ANN leaks neighbours (partitions + tenant policy; canary vectors) · A5 Redis key collision (keys prefixed with tenant id and purpose; Redis holds no authoritative or private data) · A6 private text in logs (structured logging with **field allow-list**; prompt capture only in self-hosted tracing; log scanner for planted canary strings) · A7 guessed or replayed presigned URL · A8 support impersonation (just-in-time access with reason code and customer-visible audit entry) · A9 private facts in the public deliverable (public renderer role cannot read private rows; leak detector blocks release).

## Prompt-injection defence by structure
1. **Quarantine:** raw web pages, job posts and private documents are read only by the extractor tier, which has **no tools** and must emit JSON matching a schema. Planner and tool-calling nodes see only validated structured facts.
2. **Typed tool arguments:** a domain matching a pattern, an enum branch; never free text built from untrusted content; **no tool accepts a tenant id**.
3. **Output sanitising:** extracted strings length-capped, control characters stripped; **cited URLs must resolve to the domain the source claimed**.
4. **Canary and anomaly checks:** the extractor carries a canary instruction it must never follow; an output echoing it flags the source hostile and drops it.
5. **No dangerous capability beside untrusted text:** nodes that read untrusted content cannot send, write or fetch.

Guardrail layers: input (domain pattern validated and resolved before spending; blocked-entity list; credentials scanned before indexing); model (static system prompts; extractor has no tools; JSON schema; fixed max tokens and temperature per node); tools (typed args; allow-listed brokers; no SQL, shell or arbitrary URL fetch; egress via allow-listed proxy; per-run call caps); output (every fact has at least one evidence reference, claims without evidence dropped; URLs match cited domains); release (public renderer role + leak detector); human (approval gate before any outbound or state-mutating action; approvals audited).

## STRIDE and OWASP LLM mapping (2025 edition numbering; re-verify)
Spoofing (signature and audience checks, short tokens, MFA for admins, key hashing) · Tampering (insert-only grants on ledger and audit, before/after hashes, signed webhooks) · Repudiation (immutable audit with Object Lock) · Information disclosure (six layers, leak detector, log scrubbing) · DoS (rate limits, budgets, per-tenant concurrency, circuit breakers) · Elevation (quarantine, typed tools, approval gates, least-privilege IAM). LLM01 injection, LLM02 disclosure, LLM03 supply chain, LLM04 poisoning, LLM05 improper output handling, LLM06 excessive agency, LLM07 prompt leakage (no secrets in prompts), LLM08 vector weaknesses, LLM09 misinformation (grades, citations, judge), LLM10 unbounded consumption (reservation wallet, budget guard).

## Data-flow and sub-processor register
LLM provider (public-fact prompts; private snippets only for the internal view, inside quarantine; zero-retention and no-training terms **[VERIFY]**) · embedding and rerank (chunk text and queries; redact before send) · speech vendor (audio in batch, deleted after transcription **[VERIFY]**) · **data brokers (company domain and name only; never tenant or private data)** · workflow engine (events carry identifiers only, never content) · observability SaaS (metrics and scrubbed spans, field allow-list) · LLM tracing (self-hosted, inside the VPC) · payments and email.

## Compliance and audit
SOC 2 evidence from MVP, Type I around month 6. People data about identifiable individuals: review India's DPDP Act and, where applicable, GDPR; records of processing, lawful-basis analysis, notice and objection handling, DSAR workflows. **This is not legal advice; counsel review required before storing people data at scale.** Every mutation records actor, action, resource, time and a hash of before and after state in an append-only table archived under Object Lock (90 days queryable, 7 years archived). Annual external penetration test plus a focused RLS and prompt-injection assessment before the first enterprise customer. Test data policy: synthetic tenants and generated documents only; planted canary strings and vectors in every test tenant.
