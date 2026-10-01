# Data layer v1: plan

Work item: roadmap section 7, "Data layer v1" (owner: Backend). Data model: roadmap section 11.1. Principles: roadmap section 3.

> Postgres schema (accounts, runs, sources, facts, people, job postings, tech detections, coverage rows). Object storage for raw broker JSON with neutral file names. Stable source IDs assigned once and reused.

Phase 1 risk this plan answers: "Over-engineering the schema early. Keep v1 narrow and add columns through migrations."

## 1. What already exists and is reused

| Asset | Use in this work item |
|---|---|
| `config/checklist.yaml` (33 rows, priorities, shelf lives) | Coverage rows are seeded from it per run; row ids (`P2.01`) and priorities come from config, not typed by hand |
| `config/grading.yaml` | The four grade names; the database CHECK constraints use the same list (stored as `confirmed` and so on), and a test keeps them in sync |
| `config/routing.yaml` | `client_banned_terms()` feeds the database check that keeps broker names out of client-readable text and object keys |
| `app/brokers/base.py` (`BrokerRequest.cache_key()`) | The request key that makes "pull once" a unique constraint on raw objects |
| `app/people.py` (merge by profile URL then name) | Supplies the normalised profile URL used for the per-run uniqueness of people |
| `app/golden/` | Golden accounts give realistic rows for loader tests |
| `src/abm_generator/db/` and `migrations/0001` (legacy) | Not reused. They model a different design (A to D grades, attribute facts, wallets, chunks). Left untouched in the `public` schema |

## 2. Postgres schemas

Two schemas, so access control (not naming) keeps internal data away from client-facing readers:

| Schema | Readers | Holds |
|---|---|---|
| `intel` | Application, exports, and later the client app | Everything a deliverable is built from |
| `intel_internal` | Operators and the pipeline only | Raw object records and run spend. No grant to any client-facing role |

Each schema has its own Alembic history (`app/db/migrations/`, version table in `intel_internal`), so it cannot collide with the legacy `public` tables (`accounts`, `facts`) or their Alembic chain.

## 3. Tables

### `intel` (client-readable)

| Table | One sentence |
|---|---|
| `accounts` | One target company: name, domain, careers URL (stored only once verified by visiting, with the verification time), industry and primary platform lane. |
| `runs` | One full run or refresh of an account: type, status, model versions and the pinned config version. |
| `sources` | The stable identity of a source per account: the source number (shown as `S001`) and its URL, assigned once and never changed or reused. |
| `source_reads` | What a run read from a source: date, grade, the exact sentence relied on, and a link to the raw fetch record. A new row per run, so refreshes never overwrite. |
| `facts` | One graded statement from a run: grade, source URL, exact supporting sentence, reasoning (Probable) and confirming question (Hypothesis), plus a status. |
| `fact_sources` | Links each fact to the stable source IDs it relies on (a fact may cite more than one source). |
| `coverage_rows` | One checklist row per run: pass, row id, priority, status, proof (queries, tool, result count), shelf life and last checked. |
| `people` | One person in a run's deduplicated list: name, profile URL, title, level, lane, priority, join date, tenure, certifications, and the source that proves them. |
| `job_postings` | One posting seen in a run: title, class, platform sentence, full text, first seen, last seen, open or closed, URL, and whether it was read or only matched. |
| `tech_detections` | One technology detection in a run: technology, first seen, last seen, source and cross-check result. |

### `intel_internal` (never read by client-facing output)

| Table | One sentence |
|---|---|
| `raw_objects` | One raw broker response in object storage: neutral object key, request key (pull once), content hash, size and the run that pulled it. No broker name. |
| `run_budgets` | The credit cap agreed for a run and the spend so far. Kept here because roadmap section 3 principle 6 keeps credit counts out of client-readable data. |

Not in v1 (later phases, per section 11.1): tenants and users, signals and alerts, exports. Broker call records with per-call cost belong to the "Broker connectors" and "Credit cap v1" work items and will add an internal table through a migration.

## 4. Relationships

```
accounts 1-* runs 1-1 run_budgets (internal)
accounts 1-* sources 1-* source_reads *-1 runs
                          source_reads *-0..1 raw_objects (internal) *-1 runs
runs 1-* facts *-* sources   (through fact_sources)
runs 1-* coverage_rows
runs 1-* people          *-0..1 sources
runs 1-* job_postings
runs 1-* tech_detections *-1 sources
```

Every `intel` table except `accounts` and `sources` hangs off a run, so one run's output is a self-contained, comparable set (the parity report in item 12 compares runs).

## 5. Rules enforced by the database, not only by Python

| Rule | Mechanism |
|---|---|
| Every fact has grade, URL and supporting sentence | `NOT NULL` plus `CHECK (length(trim(x)) > 0)` on each; `CHECK (grade IN ('confirmed','probable','hypothesis','lead'))` (section 9) |
| Hypothesis has a confirming question; Probable has reasoning | `CHECK` constraints on `facts` |
| Every fact cites at least one source, and its URL is one of those sources | Deferred constraint trigger, checked at commit |
| Source IDs assigned once per account, never reused, never renumbered | `UNIQUE (account_id, source_number)` and `UNIQUE (account_id, url)`; numbers allocated under a per-account advisory lock; trigger rejects any update of `source_number`, `url` or `account_id` |
| Never delete or overwrite | `BEFORE DELETE` trigger on every table raises; `BEFORE UPDATE` trigger allows only listed status columns (for example `runs.status`, `facts.status`) to change |
| No broker names in client-readable text or object keys | `CHECK` on `coverage_rows.tool` and `raw_objects.object_key` against the banned terms from `config/routing.yaml`; a test fails if the lists drift |
| Coverage proof present whenever a status is set | `CHECK (status IS NULL OR (queries non-empty AND tool IS NOT NULL AND result_count >= 0))` |
| A job posting is classified only after it is read | `CHECK (class IS NULL OR read)` |
| Detection dates are ordered | `CHECK (last_seen >= first_seen)` on postings and detections |
| Pull once per run | `UNIQUE (run_id, request_key)` on `raw_objects` |

The tracker state machine ("a run cannot complete with a High row missing a status") is its own Phase 1 work item; v1 supplies the columns and constraints it needs.

## 6. Object storage

- S3-compatible API via boto3; MinIO in `docker-compose.yml` for local use.
- Key format: `raw/<account_uuid>/<sha256 of content>.json`. No broker, account name, credit or cost in the key. The account UUID prefix prepares the per-tenant prefixes of Phase 3.
- Content-addressed and write-once: an existing key is never overwritten (the store checks first and uses a conditional put where the server supports it).
- An `ObjectStore` interface with an S3 implementation and an in-memory implementation for tests.

## 7. Open questions and the default chosen

| # | Question | Default chosen |
|---|---|---|
| Q1 | Section 11.1 says every entity is scoped by tenant, but tenancy is Phase 3. Add `tenant_id` now? | **Changed in step 7:** `accounts.tenant_id` (NOT NULL, default `DEFAULT_TENANT_ID`, no tenants table and no foreign key yet), so Phase 3 adds the tenants table, a foreign key and row-level security instead of rewriting rows. Domains are unique per tenant. |
| Q2 | Section 11.1 puts cap and spend on Run, but credit counts must stay out of client-readable tables. | Split into `intel_internal.run_budgets`. |
| Q3 | Section 11.1 puts grade and sentence on Source, but a stable source can be reread with a different result. | Split identity (`sources`) from each run's read (`source_reads`), so nothing is overwritten. |
| Q4 | Coverage proof records the "tool". A broker name there would reach the client tracker. | Store the neutral routing need (for example `people_by_title`) from `config/routing.yaml`; the database rejects broker names. |
| Q5 | People and postings: one row per account updated over time, or rows per run? | Rows per run. Never overwrite, and refresh diffs compare runs. |
| Q6 | The brief says Python 3.11+, but the repo targets 3.12 (`pyproject.toml`; Phase 0 code uses 3.12 syntax). | Keep 3.12. |
| Q7 | Legacy `public.accounts` and `public.facts` exist with a different design. | Leave them untouched in `public`; build in `intel` with a separate Alembic history. |
| Q8 | The Phase 0 guard test bans importing `boto3` in `app/` (because it can reach email services). | Narrow the guard to ban the email and messaging services (SES, SNS, Pinpoint) and keep allowing S3. |
| Q9 | No docker access for this session's user, so MinIO and the compose Postgres cannot run here. | Database tests start a throwaway Postgres 16 cluster from `PG_BIN` (embedded binaries, already checked to run here) or use `TEST_DATABASE_URL` in CI. S3 tests use the in-memory store plus a MinIO test that runs in CI. |
| Q10 | Data dictionary (a section 7 deliverable). | Generated from the SQLAlchemy models and column comments into `docs/data_layer/DATA_DICTIONARY.md`, with a test that fails if it drifts. |

## 8. Out of scope here

Broker connectors, credit cap logic, the tracker state machine, the grading engine, the pipelines, exports and the API. Each is its own Phase 1 work item and builds on these tables. No broker or model calls, and no outreach code.

## 9. Enumerated values: CHECK-constrained text, not Postgres ENUM types

Decision (step 6): every enumerated column is `varchar(64)` with a named CHECK constraint, `ck_<table>_<enum>`, listing the allowed values. Code: `app/db/enums.py`.

Why not native `ENUM` types:

1. **Two of the lists come from config and will change.** Lanes (`config/lanes.yaml`) and person levels (`config/people_scope.yaml`) are tenant settings in Phase 3. Postgres can add an ENUM value but cannot remove or rename one; that needs a new type and a rewrite of every column using it. A CHECK constraint is dropped and recreated by name in one transactional migration.
2. **Migrations stay simple and reversible.** `ALTER TYPE ... ADD VALUE` cannot be undone, and the new value cannot be used in the same transaction that adds it. CHECK changes have none of these limits, so every migration keeps a working downgrade.
3. **The cost is small.** Text takes a few more bytes than an ENUM and has no special sort order; neither matters here.
4. **The known gap is covered.** Alembic autogenerate does not compare CHECK constraints, so a config change without a migration would go unnoticed. `enum_check_drift()` reads each constraint back from Postgres and compares it with the enum, and a test runs it against the real schema after `upgrade head`.

Stored values are lowercase snake case (`not_done`); display labels (`Not done`) stay in config and in `label`. Tests fail if the enums and `config/grading.yaml`, `config/checklist.yaml`, `config/people_scope.yaml` or `config/lanes.yaml` disagree.
