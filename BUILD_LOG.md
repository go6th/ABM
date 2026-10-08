# Build log: Phase 3, the multi-tenant product

Branch `feat/phase3-tenant-product` (from `feat/observability`), spec `docs/prompts/build_phase3.md`.
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
`docs/prompts/build_phase4.md`. Not pushed. Phase 5 not started.

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
