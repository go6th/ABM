# Data layer v1: plan (Phase 1, work item 1)

Status: in progress (PART A). Source of truth for this work item: the work item prompt,
read against the committed distillations of the source documents:
`references/05-data-model.md` (data model) and `references/11-build-plan.md` (WP 1.2),
plus README sections 4.1 and 16. Where the prompt and the source documents disagree,
the documents win and the difference is recorded here and reported.

## Execution protocol

One step at a time. After every step: run the tests, run the linter and type checker,
make one git commit. If a step fails, fix it before moving on. Short reports stop at
steps 10, 20 and 30 and wait for the word "continue".

## Adaptations to this repository (and why)

| Prompt says | This repo does | Why |
| --- | --- | --- |
| Create `app/db/`, `app/storage/` | Use `src/db/`, `src/storage/` | The repo layout already exists (README section 14 documents `src/db/models`); the work item says to reuse it |
| Add Postgres 16 to docker-compose | Keep the existing `db` service (pgvector/pg16), add MinIO only | Postgres 16 with healthcheck is already there |
| Initialise Alembic | Reuse the existing `migrations/` setup; extend `env.py` | Alembic 0001/0002 already exist and CI depends on them |
| Plain tables | Every new table carries `tenant_id NOT NULL`, forced RLS, `TO app_rw` policy | README 16.3: tenancy from the first migration; migration lint R1 and `check_rls_catalog.sql` enforce it |
| `accounts` table | Extend the existing `accounts` (add name, industry, primary_lane, pursuing_client; domain CHECK; index) | One accounts table; the starter already creates it |
| `runs` table | New `runs` table per the prompt; starter `account_runs` untouched | `account_runs` is the Phase 0 orchestrator spike table (graph_version, budget, idempotency). Unifying the two is WP 1.5 work, not data layer v1. Recorded as an open question below |
| `facts` table | Rename the starter `facts` to `structured_facts`, then create `facts` per the prompt | Two different concepts (README 16.10 keeps both: structured rows for computation, narrative graded rows for exports). The starter table owns the two-output-rule `visibility` tests and keeps them |
| Postgres ENUM types or CHECK text | CHECK-constrained text | The starter uses CHECK text everywhere; it avoids ALTER TYPE churn; Python side uses str Enums whose values are tested against config for drift |
| uuid7 primary keys | uuid4 via `gen_random_uuid()` server default | uuid7 is not in the Python 3.12 stdlib. Server-side default keeps raw SQL inserts (starter fixtures) working. Swap path documented in CONVENTIONS.md |

## Tables to build

| Table | One sentence |
| --- | --- |
| `accounts` (extend) | One pursued company per tenant: normalised domain, careers URL with verification date, lane and client context |
| `runs` | One full run or refresh of an account; pins the config version, carries the agreed credit cap and a parent link for refreshes |
| `sources` | Every external item ever seen for an account, with a stable per-account code (S-0001) assigned once and never changed |
| `account_source_counters` | Per-account allocator for source codes, locked per transaction so concurrent workers stay gapless |
| `facts` | Graded narrative statements, each tied to a source row and the exact sentence relied on; hypotheses must carry a confirming question; expired rows are kept, never deleted |
| `fact_verification_attempts` | The neutral queries and tool categories run to verify a Lead |
| `lead_verification_status` | View: per Lead fact, distinct query texts and tool categories seen |
| `coverage_rows` | The 33 checklist rows per run with status and proof fields; a run cannot complete while a High row has no status |
| `people` | Senior people with profile URL dedupe, exclusion reason and merge pointers; merged and excluded rows are kept |
| `job_postings` | Twelve months of open and closed postings with class, platform sentence and full text read flag |
| `tech_detections` | Technology detections per source with cross-check state; a date range alone is a lead, grading lives in facts |
| `internal.fetch_records` | Pull-once ledger of raw provider responses; provider codes and costs live only here (internal schema) |
| `client_safe.*` | Views over the tables above that omit cost, provider and object keys; client-facing code reads only these |
| `structured_facts` (renamed) | The existing engine-structured fact rows with visibility, unchanged apart from the name |

## Relationships

- tenant 1-N accounts; account 1-N runs, sources, people, job_postings, tech_detections, counters.
- run 1-N coverage_rows; facts reference the run that created them and the run that last checked them.
- sources reference first and last seen runs; facts and people, postings and detections each reference one source.
- people reference their source and optionally the person they were merged into; facts optionally the fact that superseded them.
- fetch_records reference run and account; the object key is derived from ids and content hash only.

## Open questions and the default chosen

1. `runs` and `account_runs`: two tables until WP 1.5 unifies them. Default: build `runs` per the prompt, leave `account_runs` alone.
2. Accounts `name` is nullable: starter fixtures insert domain only, and the PRD resolves the name from the domain.
3. `careers_verified_at` is renamed to `careers_url_verified_at` to match the work item and PRD wording.
4. MinIO bucket creation happens at application startup in dev only (step 21), not in compose.
5. Source URL normalisation keeps the scheme (http and https are different pages), strips fragments, tracking params and default ports, sorts query params, and strips trailing slashes except on the root path.
