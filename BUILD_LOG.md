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
