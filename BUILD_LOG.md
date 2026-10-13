# Build log: Phase 3, the multi-tenant product

Branch `feat/phase3-tenant-product` (from `feat/observability`), spec `docs/phase3/architecture.md`.
Not pushed. Phase 4 not started.

| Workstream | Result | Main commits |
|---|---|---|
| WS0 foundations: route catalog deny-by-default, tenant storage, cache keys, generated isolation matrix | Done | early WS0 commits |
| WS1 tenancy: users and memberships, settings versions, exclusions, lifecycle and two-person deletion (0020) | Done | WS1 commits |
| WS2 identity: OIDC and SAML broker, sessions, CSRF, step-up, membership cache, roles (0021) | Done (real IdPs Blocked) | WS2 commits |
| WS3 audit: read and action audit, hash chain, tenant export by category (0022) | Done (archive Blocked) | WS3 commits |
| WS4 views and exports: one mapping, materialised rows, allow-listed queries, saved views, parity, packages, branding, safe rendering (0023) | Done (logo in Word header: follow-up) | WS4 commits |
| WS5 client app: Next.js 16 pages, run start and progress (SSE) | Done | WS5 commits |
| WS6 connectors: OAuth, exact scopes, Drive ancestry, quarantine, access log, bounded fetch (0024) | Partial: logic and fakes; real provider clients and CRM fetch not built | `812d666`, `4d5e1ec` |
| WS7 metering: usage events, atomic limits, summary, reconcile (0025) | Done (values proposals) | `b320909`, `5c32e44`, `69dfdd6`, `f6b7713` |
| WS8 operator plane: support grants and view-as, quotas, flags, key references, tenant management (0026) | Done (staff IdP Blocked; console web pages follow-up) | `622ee52`, `5ec2858`, `81a992a`, `6369631` |
| WS9 pilot: feedback, usefulness events and metric, seed script, onboarding docs (0027) | Done (live pilots Blocked) | `8952149`, `df847e5`, `471ed6d`, `377e358`, `4083fbe` |
| WS10 security pack, docs, runbooks, alerts | Done | `27ad5af`, `dad4cd4`, `b7fadd3` |
| Harness: Playwright, scale smoke, coverage, mutation | Done or partial as reported | `1156696`, `fa53c86`, `b0a5bbf`, `46dae9b`, `dbebfb9` |

## Incidents

* Commits `5c32e44` and `4d5e1ec` also contain another session's uncommitted RAG work
  (`src/account_intelligence/rag/`, `tests/rag/`), staged by a directory-wide `git add`. A history
  rewrite was refused by the permission system; the owner decides (OI-P3-7).
* Coverage under-reported async code until greenlet tracing was enabled (`b0a5bbf`).
* A lax filter type let JSON `true` act as the count 1; values are strict now (`46dae9b`).

# Build log: Phase 4, automation and signals

Branch `feat/phase4-automation` (from `feat/phase3-tenant-product`), spec
`docs/phase4/EXIT_CRITERIA.md`. Not pushed. Phase 5 not started.

| Workstream | Result |
|---|---|
| WS0 foundations: WorldScript fake world, fake grader, config with provenance | Done |
| WS1 shelf-life policy and scheduling (migration 0028) | Done |
| WS2 refresh engine: plan, delta, escalation, debounce, history, codes, Summary diff, probe orchestrator | Done; refreshed Summary through the export builders Partial (OI-P4-2) |
| WS3 monitors, intake, webhooks (0029) | Done hermetically; real adapters NotConfigured; webhook payload hand-off to the extractor Partial |
| WS4 fingerprints, clustering, scoring | Done |
| WS5 alerting: rules, subscribe-up, caps, templates, sensitive wording, outbox, channels, digest, feedback (0030) | Done; Slack and email adapters NotConfigured |
| WS6 monitoring budget, throttle, cost reports (0031) | Done |
| WS7 CRM write-back, create-only (0032) | Done hermetically; Salesforce and HubSpot NotConfigured |
| WS8 Open Door schemas, API, docs | Done |
| WS9 hardening: metrics, alerts, runbooks, restore drill, kill recovery, load profile, status page, DR plan, on-call template | Done as rehearsal; real DR Blocked |
| WS10 simulation, gates, exit criteria | Done |

Findings fixed during the build: a probe that crashed after grading would have lost its alert on
retry (the snapshot now uses facts already reported); a lost CRM reply was recorded as failed
although the record could exist (now reconciled by marker); fact expiry moved into the grading
repo (one writer of facts); debounce rows written through their owner; CRM previews audited.

# Build log: Cross-cutting workstreams (Phase 11)

Branch `feat/crosscutting`.

| Workstream | Result |
|---|---|
| X1 Data model (11.1) | Done |
| X2 Connector contracts (11.2) | Done |
| X3 Quality system (11.3) | Done |
| X4 People data & retention (11.4) | Done |
| X5 Security & assurance (11.4) | Done |
| X6 Unit costs & anomaly alerts (11.5) | Done |
| X7 Success metrics (14) | Done |
| X8 Registers & release (12, 15, 16, 17, 19) | Done |

All gates pass. `scripts/check_conformance.py` confirms 42/42 requirements.

# Build log: ABM agent layer (skill v1.8 as agents)

Branch `feat/abm-agents` (from `feat/crosscutting` at `07766f5`). Not pushed. **Guide-only build:**
the skill archive was absent; the owner instructed building from the reader's guide. Archive-only
content (checklist rows, lessons, runbook prompt text, sense-check questions, consent text, signal
tiers) is `status: TODO`, never reconstructed. Docs: `docs/abm/`.

| Work item | Result |
|---|---|
| 1 Skill content as config (`config/skill/`, versions, hashed, sourced) | Done (archive values TODO) |
| 2 Run modes: graph slices, manifest, `no_pulled_data`, lineage, migration 0041, API `mode`, start-run form | Done (ABM graph not yet the runner's graph: OI-ABM-1) |
| 3 Agent registry, contracts, schemas, allow-lists, budgets | Done |
| 4 Setup assistant and per-client settings | Done |
| 5 Pull layer, Pass 1, Pass 2, People | Done (prompts trace to guide ids; runbook text TODO) |
| 6 Sense check node and approval path | Done |
| 7 Distill and hand-off to build, gate, delivery | Done through ports; production bindings OI-ABM-4 |
| 8 Lessons as code | Done as mechanism; INCOMPLETE (no lessons without the archive) |
| 9 Consent and internal-history switches | Done (consent text TODO, so people rows Blocked) |
| 10 Internal Only file, person-name scan | Done (`output_spec.yaml` renamed; digest scan) |
| 11 Docs, ADR-0091..0099, D-95..D-102, C30..C34 | Done |

Gates (2026-10-08, local docker Postgres):

* `pytest tests/agents` 187 passed; `src/agents` coverage 98% (guards, lessons, modes, manifest,
  contracts 100%); `tests/phase3/test_abm_modes_api.py` 5 passed.
* `ruff`, `mypy --strict src/agents`, `lint-imports` (19 contracts kept), `interrogate src/agents`
  100%: pass. `ruff check .`: 26 findings, all in files of earlier packages.
* `validate_assets.py` OK; `selftest.sh` OK; `scan_client_output.py` on the three oracle accounts'
  six files: 0 hits (a planted provider name is caught); `abm_name_scan.py` OK;
  `check_routing_governance.py` OK; `abm_lessons.py check`: INCOMPLETE (exit 3, archive absent).
* `lint_migrations.py`: 13 findings, identical with and without 0041 (earlier packages).
  `check_rls_catalog.sql` on a fresh migration: 8 rows, none on this package's tables; 0041
  downgrade and re-upgrade succeed.
* Full `pytest -m "not eval"`: 36 failed, 2974 passed, 243 errors. Against the parent commit the
  only new failure was `test_routing_changes_are_governed` (fixed: decision recorded). All other
  failures and the 243 isolation-matrix errors exist at `07766f5` (matrix fixture violates
  `ck_facts_hypothesis_has_question`; RAG, alerting, grading-isolation and static-guard findings of
  other packages). The isolation matrix therefore cannot probe the new tables; their RLS is proven
  by `tests/agents/test_pg_adapters.py`.
* Mutation (mutmut 3.8.0, scratch copy, pure tests): guards, modes, manifest, lessons: 643 mutants,
  634 killed, 9 survived (8 equivalent; 1 minor: `require_guard`'s prompt directory, used only by
  prompt-enforced lessons, none yet). Eval suite in replay mode: not run (no ABM golden set).

Incidents and findings: the output spec named a person in a comment (fixed, C33); candidates could
claim any row of their source group (fixed: row must match the evidence); a budget breach in the
careers-page choice crashed a node (fixed); tool names in the Internal Only distill were not
scanned (fixed: both audiences are tenant-facing).

## ABM agent layer, part 2: production bindings and production-grade tests (2026-10-08)

| Item | Result |
|---|---|
| Production adapters for every port (`src/agents/production/`, ADR-0100) | Done, except people recording from the agent layer, internal-history connector search, Internal Only file builder (OI-ABM-4) |
| Grading engine rule `unread_match` + `match_origin` (C36) | Done, engine tests added |
| Tracker reasons `consent_required`, `pull_not_approved` | Done |
| Runner: `execute_slice` graph factory; worker endpoint switch (pull, build, `abm_agents` flag) | Done; default stays Workflow v1 (C34) |
| Migration 0042 (`abm_distill_outputs`) | Done |
| K3 cap guard | The agent package no longer reaches the approval service; `propose_plan_and_gate` in the setup adapter |
| Stale fixtures from before migration 0033 (isolation matrix, grading) | Repaired; the isolation matrix probes every table again |

Defects found by the production tests and fixed: visited-page ids built from payload keys
(dots refused by the schema); attribute keys without an engine attribute class (C38); a cap
proposal path that bypassed K3; G2 conflating search snippets with keyword matches (C36).

Gates (local docker Postgres):

* `pytest tests/agents tests/system/test_abm_production.py tests/phase3/test_abm_modes_api.py
  tests/grading/test_unread_match.py`: 238 passed; `src/agents` coverage 96% (guards, lessons,
  modes, manifest, contracts 100%).
* Full `pytest -m "not eval"`: 30 failed, 3280 passed, 0 errors. Against the parent commit
  `07766f5`: 0 new failures, 248 fixed (the isolation matrix errors and three grading tests). The
  30 remaining failures exist at the parent commit (RAG, alerting, simulation, privacy, static
  guards and write-owner or isolation-coverage entries for tables of earlier packages).
* mutmut 3.8.0 on guards, modes, manifest, lessons: 643 mutants, 634 killed, 9 survived (8
  equivalent, 1 minor: `require_guard`'s prompt directory, unused until a prompt-enforced lesson).
* ruff, `mypy --strict` (130 files), lint-imports (18 contracts kept), interrogate 100%, name
  scan, routing governance, validate_assets, selftest: pass. `abm_lessons.py check`: INCOMPLETE
  (archive absent). Eval gate: blocked on analyst-reviewed golden accounts, as at the parent.

## Separate client and operator panels (2026-10-08)

The user requested a separate customer login/workspace and staff admin console, then selected
Google Workspace for staff sign-in. The client and operator route groups now have distinct shells.
Operator routes use a same-origin proxy, generated runtime-validated API operations and a separate
HttpOnly session cookie.

The Google authorization-code flow includes state, nonce, PKCE, a Workspace-domain hint, and
requested auth_time/amr claims. The API maps only verified Workspace emails from server-side role
allowlists, validates the signed token against the configured issuer, audience and JWKS, and still
requires fresh MFA. No name-based access bypass is present. The exact Workspace domain and the
designated operator email remain deployment inputs.

Connected operator screens: content-free tenant overview and lifecycle/plan/quota administration;
feature flag and write-only key reference management; tenant-approved support view; run timeline;
live spend; cost reconciliation; and tenant-scoped failures/stuck observations. Review queue,
operator audit browsing, monitors/SLOs and announcements remain hidden in production until
backend read contracts exist. Two-person deletion and pre-pulled upload actions are not exposed yet.

Validation so far:

* Web TypeScript check: pass.
* Web ESLint: pass.
* Web Vitest: 14 passed.
* Google staff-claim, MFA freshness and OpenAPI contract tests: 11 passed.
* Ruff on changed authentication and unit-test modules: pass.
* PostgreSQL-backed operator integration suite: not run successfully; fixture setup failed because
  the local PostgreSQL server rejected the configured connection with no password supplied.

Final frontend checks (2026-10-08):

* `npm run build` (standalone output): pass; all routes render dynamically so the per-request CSP
  nonce reaches Next.js scripts. `postbuild` copies static assets into the standalone bundle.
* Production standalone smoke: `/login` and `/operator/login` return 200; unsupported operator
  routes return 404; `/api/operator/login` returns the expected 503 while OAuth settings are absent.
* Production Playwright smoke: `/login`, `/operator/login`, and `/icon.svg` returned 200 with no
  browser console errors, failed requests, or error responses. Fresh customer and operator desktop
  light screenshots and a customer 390px dark screenshot were visually inspected from temporary
  storage; screenshot files are not retained in the repository.
* Contrast check: 102/102 declared pairs pass for light and dark themes; generated API client and
  `git diff --check`: pass.

## Sign-in and dashboard polish (2026-10-08)

* Customer and staff sign-in now use a restrained, centered flow with clear workspace/provider
  expectations and links between the two panels. Admin access continues through the approved Google
  Workspace flow; no password form or hardcoded credential was added.
* Customer home adds API-derived account summary cards, account search, add-account feedback, and
  distinct loading, empty and error states. Operator home adds API-derived tenant, active tenant, live
  run and feature-flag summaries. No demo records or metric values are embedded.
* `npm run build`, lint, 14 Vitest tests and 102 contrast pairs: pass. Playwright visual review loaded
  both login screens and both dashboard empty states. Axe reports no WCAG 2.1 A/AA violations on
  those four screens.

## Finalize and release audit (2026-10-09)

Branch `chore/finalize-clean-deploy`; not pushed. The original checkout was archived at
`../backup-before-finalize-20261008-164539.zip` and verified before deletions. The current branch
contains the source traceability matrix, static wiring inventory, deployment and operations
guidance, and a release report. These documents replace earlier broad readiness claims with
observed results.

Recorded checks: backend unit tests 89 passed; web unit tests 20 passed; web typecheck, lint,
production build, and standalone login/favicon browser smoke passed; static wiring audit mapped 69
frontend call sites with zero static breaks. The initial configured full run had 30 failures; the
intermediate audit rerun had 28 failures. Three captured post-fix `TMPDIR=/dev/shm make test` runs
passed. Their isolation suites passed 378/378 each. The first full run passed 3,340 with 15 skips,
2 eval tests deselected, and 94.06% coverage; two later runs passed 3,343 with 12 skips, 2 eval
tests deselected, and 94.17% coverage. The latest backend run took 1,454.45s, emitted four existing
asyncio-mark warnings, and exited 0. The 12 remaining skips are provider-source review blockers;
three service-offline skips in the first full run passed in a focused 9/9 run after `make up`.

Fixes made after the red run: converter temp storage is isolated; phase 4 synthetic fixtures satisfy
the Hypothesis confirming-question constraint; RAG recognizes its refusal sentinel and clamps
confidence before validation; the regression loader returns the declared skipped-eval policy;
status-only tracker updates retain existing call proof; and the table-writer audit includes the
new privacy, contracts, and quality owners. No tests were xfailed or assertions relaxed. The
formula-only backoff property now has no Hypothesis runtime deadline because it is not a performance
test.

Migration 0043 separates platform tombstones from tenant tombstones, forces tenant RLS on the
affected tables, and permits canonical profile URL clearing only on first erasure. Its downgrade
refuses to restore the old identity constraint if erased rows depend on the relaxed form. Empty
database upgrade twice, RLS catalog check, downgrade, re-upgrade twice, and the isolation suite all
passed on disposable scratch databases.

Release status: **not production-ready**. Compose declares an API container, but `make up` starts
only its dependency services and API startup from a clean checkout with DB bootstrap/migrations was
not proven. Worker, web, scheduler, fake identity provider, scripted provider/LLM, and notification
services needed for end-to-end proof are absent. The existing API image built and passed a
scratch-DB smoke as UID 10001, with `/readyz` 200 and
clean SIGTERM. Terraform validation passed. CI lint, separate service images, image scanning,
SBOM/signing, authenticated multi-role E2E, production load, real cloud/identity/provider checks,
external penetration testing, and pilot tenants were not performed. See `docs/RELEASE_REPORT.md` for
blockers and the next safe step.

## 2026-10-11: backend verification against the build prompt (preflight, full suite, live smoke)

Preflight: the backend exists and runs (compose stack: api 8080, worker 8081, db 5432 with
PostgreSQL 16.15 and pgvector 0.8.7, redis 6379, web 3000, fake-idp 9090; `/healthz` ok, `/readyz`
ready with database ok). Per the prompt's decision rule nothing was rebuilt; the suite was run and
real failures fixed.

Gates run: `make test-isolation` 408 passed; catalog RLS check on the dev database returned no rows;
ruff, mypy (573 files), lint-imports (18 contracts), interrogate 100%, comments, guarantees,
telemetry, bandit, vulture, route catalog and OpenAPI snapshot all clean after the fixes below.
First full run (`pytest -m "not eval" --cov`): 3418 passed, 13 failed, 12 skipped; coverage 91%.
Final full run after the fixes: 3441 passed, 0 failed, 12 skipped; line coverage 93%, below the
project gate of 94% (the build prompt's 80% is met). Grading maths modules are at 100%; grading and
billing I/O modules are at 87 to 98% (runs/lifecycle.py 93%, spend/pause.py 94%,
spend/prepulled.py 87%), so the prompt's "100% on grading and billing" is not met.

Fixes (each with a test that fails without it):
- No API exposed a run's credit-cap proposal, so a capped run could not leave AWAITING_APPROVAL
  through the API. Added `GET /v1/runs/{id}/cap` (version, hash, cap, state, calls by category; no
  provider or line amount, D-30), policy entry, matrix entry, contract model, docs.
- Settlement wrote the post-refund balance on the `commit` ledger row; it now carries the balance
  before the refund, so a row-by-row replay reproduces every `balance_after`.
- The chat endpoint streamed hard-coded, unsourced "intelligence" (including a table of "verified"
  roles at a named company) whenever no model key was set (`LocalIntelligenceChatProvider`, added
  in 4fc79bd). Removed; chat fails closed with 503 again, as it did before that commit.
- Model ids written into six agent prompts, `model_routing.py` and the chat route moved back to
  configuration (R1); `model_routing.py` reads the allowlist and phase policies from
  `config/models.yaml` and scans report text with the client-output denylist (it previously
  substring-matched five names, so the English word "parallel" tripped it).
- The chat route imported the model SDK directly; it now uses `platform.llm.anthropic_api_client`.
- Password enrollment, chat thread creation and chat turns now record their audit events.
- Chat tables declared in the single-writer registry; `POST /v1/auth/login` declares its 200 and
  409 response models; the typed-response check treats a 204 as bodiless.
- `tests/ops/test_backup_restore.py` backs up a database it migrates itself; it used to dump the
  shared scratch database, where the isolation matrix plants canary rows with foreign keys off, so
  `pg_restore` failed whenever the isolation suite ran first.
- The `account_runs` reference stub in `db/models/source.py` now marks `id` as its key (an
  order-dependent failure in `tests/db/test_conventions.py`).
- The API sends `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'` on every
  response (the per-request nonce CSP for HTML stays in the web BFF).
- The judge-distinct pin flipped: config now routes verification to a different model from
  extraction (same provider family; family independence is still open, D-48).
- compose: api and worker now share a `payloads` volume (each had its own `/tmp/payloads`, so every
  table and export read of worker-written payloads answered 409 `source_missing`); both images
  create `/tmp/payloads` owned by UID 10001. Needs `make up` (image rebuild) to take effect; the
  running shared stack was not restarted.

Live smoke (bearer tokens from the fake IdP, synthetic tenant `smoke-synthetic`): `/v1/me` resolves
the role from the database membership (a token claiming another role is refused 401); `POST
/v1/runs` 202 and the same Idempotency-Key replays the same run; the cap read and admin approval
resumed the run (seller approval 403); the run ended PARTIAL with all 33 coverage rows carrying a
status and reason; wallet reserve 6, commit 0, refund 6, balance back to 500. Not COMPLETED: with
no model key the distill stage writes no narrative and the Summary build fails closed
(`missing_narrative`); no test reaches COMPLETED end to end either. Operator login not verifiable
locally (staff IdP is Google with a placeholder client id).

## 2026-10-11 (second pass): follow-up fixes and SaaS gap closure

### Preflight (measured)

* Branch `chore/finalize-clean-deploy`; 49 files uncommitted at start (994 insertions, 437
  deletions). The previous pass was committed first in five groups (`edcafed` chat, `f1607a4`
  wallet and backup test, `a4508c7` cap route, `336e79e` compose volume, `1b1d559` log). Left
  uncommitted as unrelated to the backend pass: the web theme and operator sign-in page edits
  (`web/app/globals.css`, `web/app/operator/(auth)/*`, `web/components/ClientShell.tsx`,
  `web/components/ThemeToggle.tsx` deletion, `web/design/*`, `web/public/theme-init.js`,
  `web/app/v1/[[...path]]/route.ts`, untracked `web/lib/same-origin.ts` and its test),
  `compose.live.yml` (`PUBLIC_BASE_URL`), `TECHNICAL_DOCUMENTATION.md`, `screenshot_all.*`,
  `web/screenshot_test.spec.ts`, `test-results/`.
* `make up` rebuilt the images; api and worker now both mount `abmgenerator_payloads` at
  `/tmp/payloads`; `/healthz` ok, `/readyz` ready.
* `TMPDIR=/dev/shm make test`: isolation 409 passed; full suite 3441 passed, 0 failed, 12 skipped,
  2 deselected (1672 s); coverage 92.90% (29801 statements, 1715 missed, 6592 branches, 596
  partial) against `fail_under = 94`: the gate fails (exit 2). Matches the previous pass.
* Critical modules before: grading/adapters 95%, grading/pipeline 97%, grading/repo 96%,
  grading/integration 97%, grading/lineage 98%, grading/docs 94%, runs/lifecycle 93%,
  spend/prepulled 87%, spend/pause 94%, spend/upload 95%, spend/config 95%, spend/service 98%,
  spend/repo 97%, spend/limits 98%, spend/plan 96%, spend/models 99%; every other grading and
  spend module 100%.

### B0 audit (before any Part B change)

| Requirement | State | Where | Covering test |
|---|---|---|---|
| B1 single client role | MISSING: four roles (`config/roles.yaml`), route catalog role sets (`api/policy.py` `_SELLERS`, `{"admin"}`), spend approver roles (`spend.yaml` `approver_roles`), admin-only MFA at sign-in (`api/routes/auth.py` local login, `identity/broker.py` SSO) | as listed | role-denial tests in `tests/phase3` (they pin the old tiers) |
| B1 other tenant == not found | PARTIAL: RLS hides other tenants' rows and routes answer 404 | `api/routes/*`, isolation matrix | `tests/isolation/test_matrix.py` probes status only, not body or header shape |
| B1 no tenant names or ids except own | PARTIAL: `/v1/me` returns the caller's own `tenant_name`; local login 409 lists the caller's own memberships | `api/routes/me.py`, `api/routes/auth.py` | none for payload scanning |
| B1 login and reset do not reveal existence | PRESENT: one 401 message, reset always 202, dummy hash on miss | `api/routes/auth.py` | `tests/phase3` local auth tests (no identical-response test) |
| B1 ids not sequential | PRESENT for tenant resources (`gen_random_uuid()`); bigserial only on ledgers and audit (never client ids) | migrations | none |
| B2 per-user account cap | MISSING (a tenant-wide `accounts` plan limit exists: `metering/limits.py`, atomic) | `api/routes/accounts.py` | `test_limits_are_atomic_under_concurrency` (tenant limit only) |
| B3 inactivity expiry | MISSING | none | none |
| B4 6th Library | MISSING (no library tables, importer or publish path); client-output scanner exists (`scripts/scan_client_output.py`, `exports` `assert_client_safe`) without the dash, internal-call or dead-link rules | `exports/`, `config/client_output_denylist.txt` | scanner tests in `tests/exports` |
| B5 grounded answers with fact citations | MISSING: `/v1/chat` streams a general model answer (no facts, no citations); the RAG loop (`rag/graph.py`) is unwired and was untested | `api/routes/chat.py`, `rag/` | none for grounding |
| B5 "what this answer used" record | MISSING | none | none |
| B5 refresh on confirmation with estimate and scopes | PARTIAL: refresh planner and slots exist (`refresh/planner.py`, `refresh/slots.py`); no API estimate or scope confirmation | `refresh/` | `tests/phase4` refresh tests |
| B5 per-account schedule (3 days, weekly, 2 weeks, monthly, off) | MISSING (shelf-life cadences weekly, monthly, quarterly only; no account setting) | `refresh/schedule.py` | `test_slot_periods_jitter_and_catch_up_are_bounded` |
| B6 monthly credit pool, usage by purpose, per-pull cap | PARTIAL: per-run credit cap and reservation exist (`spend/`, `runs/lifecycle.py`); no monthly pool, no purpose | `spend/`, `metering/events.py` | `tests/spend` |
| B7 operator overview, clients, lifecycle, quotas, flags, keys, runs, failures | PRESENT | `api/routes/operator_admin.py`, `ops.py` | `tests/phase3/test_ws8_admin.py` |
| B7 usage (who uses, who does not), client caps, expiry, library assignment, library upload, platform defaults, stored isolation check | MISSING | none | none |
| B7 question text never operator-readable | MISSING (no question store yet) | none | none |
| B8 mailbox and drive connectors | PARTIAL: Gmail and Drive clients, OAuth, exact scopes, quarantine and access log exist (`connectors_client/`), but grants are tenant-admin folder grants, not per-user scopes limited to the user's accounts | `connectors_client/` | `tests/phase3/test_ws6_connectors.py` |

## 2026-10-13: research cost audit, cache, routing and failure handling

Branch `chore/finalize-clean-deploy`, uncommitted (the owner commits). Audit:
`docs/audit/2026-10-13-research-cost-audit.md`.

### Measured before the work

* 27 runs on 2026-10-11 and 2026-10-12, 0 COMPLETED, 16 FAILED; 1902 paid provider calls of
  which 612 distinct (two in three were repeats of a call an earlier run had paid for);
  221.64 USD charged, 185.25 of it page scrapes; model spend unrecorded (the legacy model path
  wrote no usage anywhere).
* Failure causes from the checkpoints: a provider credit-balance 400 and a missing model key
  ending runs FAILED after 50 USD of pulls; the judge and setup task kinds answering no JSON
  (max_tokens 1000 and 400 spent on thinking); a 1.13 million token prompt; every narrative
  sentence dropped by the grade guard (missing_narrative build failures); an unconfirmed statement
  failing the build closed as FAILED; an in-flight call after a worker restart.

### Built

* Research cache: `config/research_cache.yaml`, `connectors/cache.py`, `Ledger.claim` adoption
  of a fresh finished call of an earlier run of the same tenant (row at cost 0, `cost_source
  cache`, same payload reference; attempt 1), `is_replayable` so an adoptable call takes no
  provider slot, `CallContext.reuse_cached` (`run_input {"fresh": true}` pays anew), Redis index
  through `REDIS_URL` (compose sets `redis://redis:6379/0`), Postgres rows as the record.
* Model reply log and reuse: migration 0049 `model_reply_log` (RLS, insert only), `llm/
  reply_cache.py`, `RunDeps._legacy_model` logs every call (served from, tokens, priced cost)
  and reuses a byte-identical request's reply with the run id rewritten.
* Routing: `config/models.yaml` arm `cost_saving` (extraction and classification on Sonnet 5.5,
  planning and verification on Opus 5.5, synthesis and narrative on Fable 5.1), `task_settings`
  effort per task and the system prompt as one cached block (`platform/llm.py request_kwargs`).
  `config/model_routing.yaml` no task kind under 2000 max_tokens (DECISIONS.md row added).
* Failure handling (`agents/graph.py`): `BuildIncomplete` ends the run partial with the
  builder's reason; `ProviderCreditsExhausted` (new, mapped from the credit-balance 400),
  `AuthFailed`, `ModelUnavailable`, `AdapterNotConfigured`, platform `NotConfigured` and
  `ContentRefused` in a model node end the run partial with a code and keep the pulls; a
  `ContextTooLong` extraction group is dropped and counted; `restates_grade` matches grade labels
  only; distill re-asks once when the narrative was dropped whole (`DistillInput.notes`); a
  `started` attempt of the same worker older than `in_flight_stale_s` (900 s) is re-issued.
* `scripts/token_savings.py`: savings and model spend per tenant from the ledger and the log.
* Env registry: `REDIS_URL`, `RESEARCH_CACHE`, and the previously unregistered dev and web
  variables; `docs/ENVIRONMENT.md` regenerated.

### Tests and gates

* `python .claude/.../lint_migrations.py migrations`: 0049 clean; two findings in 0048 (not this
  work: SECURITY DEFINER without `-- reviewed:`).
* `scripts/check_routing_governance.py`: OK. `lint-imports`: 18 kept, 0 broken.
* `pytest tests/isolation`: passed (hard gate). `tests/connectors tests/spend tests/db
  tests/tracker tests/system/test_single_write_paths.py tests/grading/test_adapters_ledger_edges.py`:
  passed except `test_vendor_names_live_only_in_allowed_places`, which flags the committed,
  untouched `tests/system/test_wiring_contract.py` (pre-existing).
* `tests/llm tests/workflow tests/agents tests/unit/test_platform_llm_knobs.py
  tests/unit/test_model_routing.py tests/unit/test_config_files.py tests/unit/test_env_registry.py
  tests/system/test_port_contracts.py`: 347 passed.
* mypy on the changed modules: clean. Full suite: see the line appended below.
* Live stack: migration 0049 applied to `ai`; api and worker recreated with the live overlay;
  `/readyz` ready; cache config, Redis round trip and routes verified inside the worker. No live
  run was started (the model provider account has no credits; a run would also cost money).
* Full suite `pytest -m "not eval"` (TMPDIR=/dev/shm): 3580 passed, 9 failed, 12 skipped,
  2 deselected (706 s). Of the 9: `test_env_example_lists_exactly_what_the_code_reads` was this
  work (REDIS_URL and RESEARCH_CACHE missing from `.env.example`; added, passes). The other 8
  fail identically on a clean HEAD worktree before this work: `test_vendor_names_live_only_in_
  allowed_places` (the committed `tests/system/test_wiring_contract.py` names a vendor), the
  three fake-model judge tests (`test_fake_model.py`, `test_fake_model_scenarios.py`), the W6
  static guard (a wrapped `# LOOP` comment in `runner.py` the generator calls malformed; the
  comment is repaired here and `docs/architecture/loops.md` regenerated), and four
  `test_wiring_contract.py` tests importing names (`REQUEST_ID`, `components`) that do not exist
  in `obs.logging` and `api.routes.health`.
* Measured on the live ledger after the change: a re-run of the last failed run's account would
  reuse 300 of its 301 provider calls (54.41 of 54.66 USD) from the cache.

## 2026-10-13 (second pass): durable runs, no paid data lost on any stop

Branch `feat/durable-runs` (from `chore/finalize-clean-deploy`; carries the uncommitted research
cache work of the first pass too). Uncommitted. Audit before any change: `AUDIT.md` (14 findings).

### Built (backend)

* **Write-ahead, provider calls** (`connectors/service.py`): already a `started` intent with
  tenant, run, provider, args hash and worker id before the call, and the raw body stored before
  parsing. Fixed A2 and A3: a defect inside parsing (not a connector error) used to close the
  attempt `failed` at zero cost although the body was paid and stored, so a resume paid again. The
  stored body is now finalised `done` with the error class and its cost; the replay is free and
  raises the same error.
* **Write-ahead, model calls** (`workflow/deps.py`, `llm/reply_cache.py`, migration 0050): a
  `started` row in `model_reply_log` before every model request; the raw reply (also an invalid
  one) is stored and logged with tokens and cost before any parsing. A `started` row is never a
  reuse hit.
* **One terminal-state handler** (`workflow/runner.py`, `runs/stop.py`, `runs/lifecycle.py`):
  an unexpected error ends PARTIAL (reason: the error class; `account_runs.stop_detail`: class and
  redacted message, keys and tokens removed, at most 600 characters) when the run holds a
  finished paid call, a fact or a deliverable; FAILED with a full refund only when it holds
  nothing. `lifecycle.end_from_active` walks the run to VERIFYING so PARTIAL is a legal end.
  A cancellation, SIGTERM or SIGINT (also when LangGraph wraps it as `NodeCancelledError`) only
  notes itself on the run; the lease is released and the run resumes from its checkpoint.
  Failed builds already ended PARTIAL (both graphs, first pass).
* **Orphan recovery** (`runs/sweeper.py`, migration 0050 `runs_orphaned`, a reviewed narrow
  definer returning identifiers only): at worker startup and every `SWEEP_INTERVAL_S` (300), a
  run in RUNNING, DISTILLING or VERIFYING whose lease expired more than `ORPHAN_AFTER_S` (600)
  ago is ended `worker_lost` (PARTIAL with data, FAILED without), each under its own tenant
  transaction. Waiting runs are never swept. Switch: `RUN_SWEEPER`.
* **Resume** (`POST /v1/runs` `resume_from`, validated to be a run of this tenant and account):
  every finished call and model reply of that run is reused at cost 0 whatever its age, also
  with the research cache off; `started` rows are never hits; a stale `started` attempt is
  re-issued only after 15 minutes (`in_flight_stale_s`, first pass).
* **Partial output** (`GET /v1/runs/{id}` adds `domain`, `stop_detail`, `stored` counts and a
  `missing` list; new `GET /v1/runs/{id}/collected`: pulls by tool category, never a provider
  name; public current facts; the client distill drafts; missing rows with reasons;
  leak-scanned). Route catalog, isolation matrix (route probed for cross-tenant reads, function
  and job registered), OpenAPI snapshot and generated web client updated.
* Secrets: `runs.stop.redact` on every stored stop detail; payloads hold provider bodies only.

### Built (frontend)

* `web/components/ws/RunStopReport.tsx` on the run page for every finished run: why it stopped in
  plain words (`web/lib/run-stop.ts`; the technical detail for admins only), what it holds
  (pulls, reused pulls, findings, files), the areas still missing with reasons, the collected
  findings on demand, and **Resume research** (confirm dialog, one idempotent POST with
  `resume_from`, then the new run's page). Model text renders as plain text.

### Tests and gates

* New: `tests/system/test_durable_runs.py` (15: unexpected error PARTIAL vs FAILED, model
  credits exhausted, prompt too long, invalid reply, network drop, provider credits exhausted,
  parse failure after a paid response, SIGINT and SIGTERM-style cancel then resume, OOM-style
  kill then sweeper, sweeper leaves live and waiting runs, failed build, resume at cost 0 past
  every TTL, a started row is never finished); `tests/runs/test_stop.py` (redaction, bounds);
  `tests/system/test_run_collected_api.py` (API shape, no provider names, other tenant 404);
  reply cache write-ahead tests; `web/tests/unit/run-stop.test.tsx` (7). Every system test
  asserts the ledger (each paid key once, ledger sum equals spend) and another tenant's view.
* Isolation suite: 413 passed. Targeted backend batch: 591 passed. Web: `tsc` clean, eslint 0
  errors, vitest 90 passed. Migration lint: 0049 and 0050 clean (0048 findings pre-existing).
  Route catalog, import contracts, loop inventory, OpenAPI snapshot: clean.
* Dev stack: migration 0050 applied to `ai`; api, worker and web rebuilt or restarted; reading
  run efe58555 (2026-10-12, 54.66 USD) returns 301 kept pulls and 33 missing areas.
* Full suite `pytest -m "not eval"` (TMPDIR=/dev/shm): 3604 passed, 7 failed, 12 skipped,
  2 deselected (728 s). All 7 fail identically on a clean HEAD (`aaf7330`): the vendor-name scan
  (flags the committed `tests/system/test_wiring_contract.py`), two fake-model judge tests, and
  four `test_wiring_contract.py` tests that import `REQUEST_ID` and `components`, names HEAD's
  source does not define. Not one failure comes from this work.

## 2026-10-13 (third pass): Research files (upload any file, audit it, analyse it)

Branch `feat/research-files` (from `feat/durable-runs`; carries the earlier uncommitted passes).
Uncommitted. Plan before any change: `PLAN.md`.

### Built (backend: `src/research_files/`, migration 0051)

* **Storage**: a streaming `BlobStore` port (`blobstore.py`) with a local-volume adapter (the dev
  `payloads` volume) and a Google Cloud Storage adapter (compose of parts; VERIFY V-FILES-1: not
  run against a bucket). Parts are written to temporary files and renamed; nothing holds a whole
  file in memory. Postgres keeps metadata only (`research_files`: tenant, name, size, mime,
  detected type, SHA-256, status, uploaded_by, timestamps), unique per tenant by SHA-256.
* **Upload** (`upload.py`): init, PUT part (request body streamed), upload state (received and
  missing parts), complete (compose while hashing; size checked; same SHA-256 in the tenant
  returns the existing file: stored and processed once). The raw file is stored before parsing.
* **Detection and parsers** (`detect.py`, `parsers/`): magic bytes first, then the name. Tabular:
  CSV and TSV (resumable by byte offset, encoding detection, unterminated-quote detection), XLSX
  (read-only streaming, every sheet), Parquet (row groups, pyarrow), JSON (top-level arrays
  streamed item by item; other documents up to 64 MiB), JSON Lines. Documents: PDF (pypdf, per
  page, scanned pages flagged), DOCX and PPTX (lxml over the zip, streamed), text, Markdown and HTML
  (sections by heading). Images: Pillow metadata, GPS flagged. Audio and video: container
  metadata, WAV duration and truncation. Archives: zip, tar, gzip, member by member with
  path-traversal, link, member-count, depth, ratio and total-size guards (high-water byte
  counting stops a lying header). Unknown types are stored, PARTIAL `unsupported`.
* **Audit** (`checks.py`, `profile.py`, `analytics.py`): integrity (hash re-verified before
  parsing), corrupt and truncated content, encoding, header problems, ragged rows, missing
  values, duplicates (first 100,000 rows per table), type mismatches, outliers (z above 6 after
  1,000 values), inconsistent dates and units, personal data and secrets (flagged with kind and
  location, value never shown, columns masked in analytics), unsourced quantitative claims,
  contradicting numbers, empty sections. Severity, exact location and a suggested fix on every row;
  exact counts per check with at most 50 listed. Data is never changed.
* **Analytics**: per-column profiles (Welford moments, exact top values and distinct counts to a
  cap, quantiles from a deterministic bottom-k sample whose row numbers are kept, pairwise
  Pearson), document sections with extractive summaries, topics and entities; charts built from
  stored state, each naming its table and rows (or the sampled rows) or its pages and sections.
* **Processing** (`pipeline.py`): one worker per file (lease), each unit stored with its findings
  and the compressed accumulator checkpoint in one transaction before the next is read; resume from
  the cursor; an unexpected error ends PARTIAL with a redacted detail; an interrupt leaves the file
  resumable; a dead worker's file is ended `worker_lost` by the sweeper (`research_files_orphaned`,
  a reviewed definer returning identifiers only) and resumed on request.
* **Models and cost** (`modelsvc.py`, `qa.py`): section summaries (tier `synthesis`) and answers
  (tier `extractor`, the cheapest configured model) through the existing router; estimate before
  any call, approval above `approval_limit_usd` (0.50); cache by task, model and content hash
  across the tenant's files ($0 for unchanged chunks); `started` row before each request and the
  reply with tokens and cost before parsing; prompts capped at 48,000 characters and split;
  credits, refused keys, refusals and overload stop PARTIAL with the reason. Answers cite only
  retrieved units (an invented citation is removed); without a model the answer is extractive.
* **API** (`api/routes/research_files.py`): upload init, part, state and complete; list, status,
  audit, analytics, ask, approve, resume, delete; worker task `POST /tasks/file-process`;
  schedulers (Cloud Tasks, local HTTP, in-process) enqueue files. Route catalog, contracts,
  OpenAPI snapshot, isolation matrix (8 tables, 13 routes, function, job, event), single-writer
  registry and job catalog updated.
* Dependencies added: `pypdf` 6.20.1, `pyarrow` 26.0.0 (`uv add`; images rebuilt).

### Built (frontend)

* `web/app/(client)/research-files` (list and upload) and `[file_id]` (status refreshed while
  working, why it stopped, approve with the estimate, resume, audit report filtered by severity,
  column profiles and correlations, charts with their sources, sections with summaries, cited
  questions with the approval path, delete). `components/ws/FileUploader.tsx`: browser slicing,
  part retries, pause, resume of the same file across reloads (the upload id is kept locally).
  Sidebar entry "Research files".

### Tests and gates

* `tests/research_files/test_parsers.py` (47): every type parsed, a corrupt copy of each reported,
  resume from every unit gives the same units, zip bomb, gzip bomb and path traversal blocked,
  sensitive data and research checks, detection, the blob store.
* `tests/research_files/test_pipeline_db.py` (30): every type end to end, audit locations and
  masking, corrupt and unknown files PARTIAL with the upload kept, resumable upload and dedupe,
  kill mid-parse in a subprocess then sweeper and resume (same chunks, analytics and findings as an
  uninterrupted parse), second run of the same chunks at $0, approval gate, credits out then resume
  paying only the rest, any stop PARTIAL with a redacted reason, cited answers, tenant isolation,
  delete, sweeper leaves live files, atomic unit write.
* `tests/research_files/test_api.py`: the whole flow over HTTP with a cookie session; tenant B
  gets 404 on every route.
* `tests/research_files/test_large_file.py` (`RF_LARGE_FILE=1`, run once, 531 s): peak resident
  memory 133.3 MB for a 20 MB CSV and 133.3 MB for a 1.10 GB CSV (1,828,386 rows, 551 units);
  upload 14.2 s, processing 469 s, re-upload 20.5 s deduplicated; model spend $0.
* Web: `web/tests/unit/research-files.test.tsx` (6); all web checks: tsc clean, eslint 0 errors,
  vitest 96 passed.
* Isolation suite with the new matrix entries, migration lint, route catalog, import contracts,
  loop inventory, docstring coverage, guarantee tags, OpenAPI snapshot: all pass.
* Browser check on the dev stack: signed in, uploaded a CSV on the Research files page, the worker
  processed it over HTTP, the report and charts rendered, a question was answered with citations,
  no email address appeared on the page.

## Two research input modes (2026-10-13)

Request: one New research screen with two inputs. "Company name" runs the existing public
pipeline unchanged; "Upload file" researches only the tenant's own documents, with optional
public enrichment. Branch `feat/research-files` (shared working tree; not switched). Nothing
committed; no live paid run; no outbound message.

### Built (backend)

* **Migration 0052** (`migrations/0052_research_input_modes.sql` and down): `documents`,
  `document_passages`, `document_redactions`, `document_policies`, `run_documents`,
  `document_deletions`, `fact_reevaluations`, `run_gate_results`, `leak_checks`,
  `internal_deliverables` (forced RLS, `tenant_id` on every row, `account_id` on every row once
  the company is confirmed), `account_runs.input_mode`, `chunks` location columns,
  `fact_evidence` kind `document` with `document_id` and `document_location`, function
  `documents_orphaned` (identifiers only, reviewed SECURITY DEFINER).
* **`src/documents/`**: config (`config/research_input_modes.yaml`), scanner (signature scanner
  for local profiles, clamd adapter, fail closed), parser sandbox (subprocess, empty environment,
  isolated mode, resource limits, audit hook refusing network, processes, native loads and
  writes), redaction tiers (Strict, Standard default, Minimal; map stored, original kept),
  chunking (400 to 800 tokens with heading path, 60 to 90 second transcript windows, one CRM row
  per passage), local hashing embedder `local-hash-v1`, company resolution (never guesses),
  repository (single writer), service (upload, signed or token-checked PUT into quarantine,
  complete, scan, promote, parse, index, confirm company, delete with re-evaluation marking,
  sweeper), extraction (rows by code; prose by the model inside the untrusted boundary; verbatim
  quote check per fact), leak check (shared 8-word runs and embedding similarity), pre-delivery
  gate for both modes, Internal Only Word file, file-mode run graph, cost estimate.
* **Runs**: `POST /v1/runs` accepts exactly one input mode (`input_mode_mixed`,
  `input_mode_empty`, `domain_required` problems); company mode keeps the legacy body and gains
  an optional spend limit; file mode checks documents, limits and the confirmed company (409
  `company_confirmation_required` with the detected candidates), links the documents, and with
  `enrich_public` creates a linked company-mode run budgeted at the credit cap. The run view and
  list carry `input_mode`, `source_label` and the linked run; the run view carries the latest gate
  verdict and defects. `/tasks/run-slice` runs the file graph for file-mode runs.
* **Routes** (catalogued, audited, isolation-probed): `/v1/documents/uploads`, `{id}/content`,
  `{id}/complete`, `{id}/status`, list, `confirm-company`, `estimate`, delete, `policy` (PUT
  admin), `{id}/authoritative` (admin), `/v1/runs/{id}/internal` and `/internal/file` (never
  viewers), worker `/tasks/document-process`.
* **Grading and tracker**: `record_document_fact` (private, Internal, Confirmed only from an
  authoritative document) and `mark_document_facts` in the grading write path; zero-cost
  `record_document_read` ledger record as row proof; reason codes `not_in_uploaded_data` and
  `not_searched_file_only`; the pinned completion policy accepts those two reasons on High rows.
* **Gate wired** (audit F1): `Container.gate` is the pre-delivery gate in every profile.
* **Triage of the seven failing tests**: request id in logs and in the worker hop
  (`obs.logging.REQUEST_ID`), readiness `components` (status words only), vendor names read from
  config in tests, the fake judge unit test aligned with its scenario, and the happy path test
  aligned with the independent judge of the active routing arm. All seven pass.

### Built (frontend)

* `web/app/(client)/research/new`: the two-option switch; Company name (name, optional domain,
  optional spend limit); Upload file (drag and drop, per-file stage, detected company confirm
  step, enrichment switch off by default with its limit, estimate before start). Entry points:
  sidebar "New research", the runs page, "Add research files" on an account.
* Run page: source label, linked run, Internal Only section (findings with grade, citation and
  quote; documents; download), quality check problems, Resume hidden for file runs. Runs list:
  Source column.

### Tests and gates

* New: `tests/documents/` (37 Python tests): units (config coverage, redaction tiers, three chunk
  shapes, embedder, sandbox refuses network, processes and writes, scanner verdicts, unreachable
  scanner fails closed, verbatim quote checks, injection boundary, canary leak, completion rule);
  Postgres (PDF, DOCX and XLSX through scan, parse and index; infected, macro, wrong-type and
  disguised files rejected before parsing; oversized, wrong-type and audio refused before upload;
  unavailable scanner; company detection and confirmation; delete removes chunks and marks facts;
  page limit; sweeper; tenant isolation); gate (clean pass; unset row, dash, broker name and credit
  count, private canary each fail with stored defects; file mode needs the labelled internal file);
  end to end over HTTP (file run COMPLETED with Internal facts citing document and place, all 33
  rows reasoned, gate pass; labelled internal Word file and its export audit; zero paid calls;
  ambiguous company blocks until confirmed; estimate; enrichment budget equals the cap and stays
  within it; viewer refused, access audited; prompt-injection file; input mode validation and
  company mode unchanged). Web: `tests/unit/documents.test.tsx` and run-stop additions.
* `pytest tests/isolation`: 467 passed (hard gate).
* Full suite `pytest -m "not eval"` (TMPDIR=/dev/shm): 3778 passed, 1 failed, 13 skipped. The
  failure was a property test generating "p_a" (a provider-code shape the builder rightly
  refuses); its generator now excludes leak-pattern text and it passes (rerun alone). An earlier
  full run aborted once in `tests/research_files/test_pipeline_db.py` (a native abort inside the
  in-process research-file parser); the test passed three times on rerun. Recorded as OI-IM-13.
* Web: tsc clean, eslint 0 problems, vitest 101 passed.
* Static gates: ruff, ruff format, mypy (623 files), import contracts (18 kept), docstrings 100%,
  comments, guarantee tags, loop inventory, route catalog, OpenAPI snapshot, web client, env docs,
  routing governance, bandit (0 issues): all pass. Migration 0052 applied to the dev database;
  catalog RLS check returns no rows.
* Dev stack: api, worker and web rebuilt and restarted; `/readyz` ready with components. The
  dev worker has no model key, so a file run there reads spreadsheet rows only and ends PARTIAL
  `model_not_configured` (prose needs a model).
