# Project status

Audit date: 2026-10-09. Branch: `chore/finalize-clean-deploy`. This project is **not cleared for
production or a live pilot**. The configured backend suite passes, but the full Compose stack,
authenticated browser journeys, CI supply-chain checks, and owner-specific deployment inputs are
incomplete. See [RELEASE_REPORT.md](RELEASE_REPORT.md) for commands, results, and blockers.

## Traceability snapshot

| Status | Count |
|---|---:|
| Done | 1 |
| Partial | 161 |
| Missing | 13 |
| Blocked-by-external | 6 |

Source-linked requirement rows and their mapped code/tests are in [TRACEABILITY.md](TRACEABILITY.md).
Statuses are conservative: a code path or unit test alone does not establish that the requirement
works through the full stack.

## What has been exercised

- Backend unit suite: 89 passed.
- Web: 20 Vitest tests, TypeScript, ESLint, and production build passed.
- Production standalone browser smoke: customer/staff login paths and favicon returned 200; no
  console or network errors were observed. No authenticated user journey was run.
- Static wiring inventory: 69 frontend call sites, zero static route breaks. It does not run the
  stack or prove authorization, browser proxying, or schema parity at runtime.
- Three captured post-fix full runs: `TMPDIR=/dev/shm make test` passed each time, with isolation
  378/378 on every run. The latest passed 3,343 tests, skipped 12 provider-source verification
  cases, deselected 2 eval tests, and reported 94.17% coverage. An earlier run skipped three local
  service checks while emulators were offline; after `make up`, focused Redis/GCS/Cloud Tasks
  integration tests passed 9/9. Browser E2E, API fuzz, full accessibility, production load, and
  backup/restore remain unproven.
- Post-fix regression groups: RAG, export integration, regression policy, and writer ownership
  passed 191 tests; phase 4 simulation/alerting and parallel cap/pause passed 16 PostgreSQL tests.
- API Docker image built and passed a scratch-database smoke as UID 10001 with `/readyz` 200 and a
  clean SIGTERM exit.
- Backup archive verified at `../backup-before-finalize-20261008-164539.zip`.

## Product areas and limits

The repository includes tenant isolation and lifecycle code, connectors and model ports, the
research workflows, tracker and grading, billing ledger, exports, privacy workflows, tenant UI,
and an operator console. Coverage and production readiness vary by area; the traceability matrix
and open-items register record gaps.

The local Compose stack starts PostgreSQL, Cloud Storage and Cloud Tasks emulators, and Redis. It
does not include the API, worker, scheduler, Next.js app, fake OIDC users, scripted provider/LLM
servers, or email/Slack sinks. Production sign-in, provider calls, cloud deployment, production
load, DR, external penetration testing, and pilot-tenant operation remain unproven.

## Current release blockers

1. Complete the synthetic local stack and browser E2E across user roles and separate tenants.
2. Consolidate environment reads into a typed registry and generated `.env.example`; add full
   startup validation and an environment-safe doctor command.
3. Build and validate separate API, worker, migrator, and web images, plus scans, SBOM, and signing.
4. Complete API fuzzing, authenticated E2E, accessibility and repeat-run checks; add CI config lint
   and image supply-chain checks.
5. Resolve the product, legal, identity, cloud, provider, and on-call inputs listed in
   [open-items.md](open-items.md) and [conflicts.md](decisions/conflicts.md).

## Next action

Complete the local stack, E2E, CI, and image supply-chain blockers. Then the owner can provide
non-secret staging Terraform inputs and review a staging plan as described in [DEPLOY.md](DEPLOY.md).
No cloud action has been performed.
