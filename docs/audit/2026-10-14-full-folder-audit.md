# Full folder audit, 2026-10-14

Read-only audit of both working copies against the project documents. Nothing was changed, no database was written, no container was started or stopped.

- **MAIN:** `/home/sakib/ABM Generator`, branch `feat/final-product` (4 commits ahead of `main`, 13 uncommitted files, never pushed).
- **REPLICA:** `/home/sakib/abm-replica`, branch `feat/replica` (24 commits ahead of MAIN, clean, never pushed). Only adds the Claude Design replica (UI, Ask, workspace, tables, credits, Refresh, Schedules, migrations 0053-0056).
- **Documents checked against:** SaaS Architecture v2 (PDF), SaaS and Enhanced Version (docx), Use Case PRD (docx), Production Roadmap (PDF), Technical Documentation (PDF), the `account-intelligence-platform` skill references, and `docs/build/REPLICA_PROMPT.md` (the owner's latest product decision, which overrides the documents where they conflict).

## 1. The short answer

The project has strong foundations (data model, tenant isolation, grading, file pipeline, tests) but **it cannot do its main job yet**: no research run has ever completed end to end, because there is no model key, the data providers are off, the research engine in the replica is a stub, and the ABM skill package is missing. Around that core, many pieces are built but not connected (alerts, refresh deltas, private search, retention, tracing), and about half the replica screens are not built yet. Nothing is deployed, CI on `main` is red, and the deploy pipeline has no cloud settings.

## 2. Not working right now (runtime, ranked)

| # | Problem | Evidence |
| --- | --- | --- |
| 1 | **No AI model connected.** Answers, briefs and research cannot be generated. | `/readyz` reports `llm: absent`; `ANTHROPIC_API_KEY` empty in `.env` and the container; `LLM_FAKE` not set (`app/container.py` ~360-432 uses `UnconfiguredLlm`) |
| 2 | **All five data providers are off** (Parallel, Firecrawl, PredictLeads, BitScale, Sumble). | `/readyz` providers `off`; `CONNECTORS=off`; all `P_*_API_KEY` empty |
| 3 | **Production research engine is a stub** in the replica: every Refresh, scheduled refresh and new research fails outside dev. | `abm-replica/src/.../replica/jobs.py:109-125` (`SkillEngine` raises `EngineNotConfigured`; only the fake `HermeticEngine` works) |
| 4 | **Main web app is down.** | Container `abmgenerator-web-1` exited (143) 13 hours ago; nothing on :3000 (maybe stopped on purpose by the other session) |
| 5 | **Replica is not running** at all (no API on :8090, no web, no `.env`). | `ss -ltnp`, `docker ps` |
| 6 | **Replica fails its own quality gates:** 7 Python test failures, 5 lint errors, missing file headers, migration lint. | pytest 2676 pass / 7 fail; ESLint `accounts/page.tsx:54`, `(client)/page.tsx:31`, `Ask.tsx:64`; failures listed in section 7 |
| 7 | **Migration security lint fails in both copies:** SECURITY DEFINER functions without a reviewed comment. | `migrations/0048_password_reset.sql:6,18`; replica also `0053_replica.sql:180` |
| 8 | **Email cannot be sent** (verification, password reset), object store and identity broker fail closed. | `tenant_plane = unconfigured_plane(...)` in `container.py` |
| 9 | **Credit manager v2 parts fail closed** (wallet, upload store, scanner port, upload parser). | `container.py:71-126` defaults never overridden; `spend_v2.py:143` |
| 10 | Rate limits run per process, not in Redis; object-store keys unused locally. | `rate_limit_store: in_process` (container sets `REDIS_URL`, readiness reads `REDIS_HOST`); `payload_store: local` |

Healthy: MAIN API and worker are up and ready; MAIN's database-free Python suite passes fully (2,670 passed); MAIN web typecheck, lint and unit tests pass; the route catalog, guarantee tags and routing-governance checks pass in both copies.

## 3. Missing or broken user features (against REPLICA_PROMPT)

Replica status: steps 1-5 built (shell, Ask, Accounts, Account workspace, seven deep-dive tables with Excel, credits and Refresh, Schedules). Steps 6-11 not started.

| # | Feature | MAIN | REPLICA | Evidence |
| --- | --- | --- | --- | --- |
| 1 | Mail and drive page | Missing | **Nav link goes to a 404** | `WorkspaceShell.tsx:54` links `/mail-and-drive`; no page |
| 2 | Add account (three ways) | Missing | **Button opens "Account not found"** | `accounts/page.tsx:51,59` link `/accounts/add`; caught by `[domain]` |
| 3 | 6th Library (available list, add from library, admin upload, check, publish) | Missing | Tables only (0053:221-270), no code or routes | |
| 4 | Per-user account cap | Missing | Shown in sidebar, **never enforced** | `accounts.py:153` uses tenant plan limits |
| 5 | New research by company on credits | Partial | Partial: no estimate or confirm, old checkpoint wording | `research/new/page.tsx:40,169` |
| 6 | File cost in credits | USD | USD | `research/new/page.tsx` |
| 7 | File analysis screen (10) | Embedded | **Missing** (`FileAnalysis.tsx` not imported) | |
| 8 | Mail and drive OAuth, per-user, Outlook/OneDrive/SharePoint, sync job | Stub | Stub | `connectors_client/ports.py:129-167`; routes tenant-scoped |
| 9 | Admin A3 Client detail (limits, features, publish) | Missing | Missing | |
| 10 | Admin A4 Users list with Terminate / Re-enable | Stub | Stub | `operator/(console)/users/page.tsx` |
| 11 | Idle expiry and access state | Missing | Column only, enforced at password sign-in only | 0053:169-188 |
| 12 | Nightly isolation check (admin pill) | Missing | Table only, nothing writes it | 0053 `isolation_checks` |
| 13 | Admin A7 Platform defaults | Contradicts (checkpoint size, phase flags) | Same | `settings/page.tsx:11,60` |
| 14 | Admin A6 credits by purpose and runs table; A1 chart, categories, "Not using it"; A2 Add client and credit bars | Partial | Partial | `question_categories` written, never read |
| 15 | View as a client screen | API only | API only | `operator_view.py:27,52` |
| 16 | Credit cap per pull during a run | Old caps | Estimate only, not enforced live | `replica/credits.py` |
| 17 | Ask uses tracker, uploaded files, previous deliverables; streams text | Partial | Partial (answer arrives at once) | `replica/answers.py` |
| 18 | 33-row tracker visible to users | Hidden | Hidden | |
| 19 | Sheet Excel export goes through the safety gate | n/a | **Bypasses gate, tool-name scan and formula neutralising** | `replica/sheets.py::workbook` |

Leftovers that contradict REPLICA_PROMPT: MAIN still has the Normal | Deep switch; both copies keep checkpoint "Continue?" routes and UI (`RunSteps.tsx:144-162`), model-choice leftovers (`/v1/models`, `/v1/chat/provider-key`, phase model flags), many redirect-stub pages (`/alerts`, `/chat`, `/downloads`, `/runs`, `/usage`, `/settings/*`), and an admin comment "No caps of any kind anywhere" (`clients/page.tsx:3`). The replica changed some labels by owner decision (Search: off/on, "+", "Accounts", a fourth "topic" scope) that `REPLICA_PROMPT.md` does not yet record.

## 4. Missing or unwired architecture pieces (against Architecture v2)

| # | Entity | Status (both copies unless noted) | Evidence |
| --- | --- | --- | --- |
| 1 | Production research engine for credit-confirmed jobs | Replica: stub, not bridged to the working run graph | `replica/jobs.py:109-125` |
| 2 | Nightly spend invariant (balance = ledger) | **Broken:** Terraform calls a CLI that needs per-run arguments and exits 2 | `infra/stack/main.tf:352`; `worker/__main__.py:24-29` |
| 3 | Alert engine, outbox, digest, Slack/webhook delivery | Built, not wired; channels NotConfigured | `alerts/engine.py`; `alerts/channels/__init__.py:32,40` |
| 4 | Refresh deltas (`deltas`, `delta_items`) | Never written | `refresh/records.py` unreachable |
| 5 | Probe-then-escalate refresh, debounce | Built, not wired | `refresh/probe.py`, `escalate.py`, `debounce.py` |
| 6 | Cloud Scheduler for `/tasks/schedules-tick` | Missing: scheduled refreshes never fire when deployed | `infra/stack/main.tf:382-391` |
| 7 | Per-tenant KMS keys, crypto-shredding on offboarding | Missing (`tenants.kms_key_arn` unused) | `infra/modules/cloud_sql/main.tf:39` |
| 8 | Two billing ledgers (`wallet_ledger` and replica `credit_entries`) | Both hold credit truth, no reconciliation | `runs/lifecycle.py:430`; `replica/credits.py` |
| 9 | Private retrieval (hybrid vector and text search, rerank) | Built, not wired; only a hashing embedder | `rag/` 0 of 14 modules reachable; `documents/embed.py:62` |
| 10 | Reconciler (re-send stuck QUEUED runs) | Partial; sweeper only runs with `SERVICE_ROLE=worker`, which Terraform never sets | `runs/sweeper.py`; `api/main.py:96-98` |
| 11 | Audit log chain verification, 7-year locked archive | Not wired; bucket soft-delete is 7 days | `audit/chain`; `infra/modules/gcs/main.tf:47-51` |
| 12 | DSAR, erasure, suppression, retention jobs | Built, not wired; retention policy "needs counsel" | `src/privacy/*`; `config/retention.yaml` |
| 13 | Tracing | `obs.setup.configure()` never called, no exporter | grep |
| 14 | Research graph shape | No plan and gap loop (max 2), no separate verify node | `agents/graph.py:669-683` |
| 15 | MAIN uncommitted change | `deliver` now always marks runs `completed`, removing the partial fallback | MAIN `src/agents/graph.py:647-652` |
| 16 | Startup RLS role self-check | Script only | `scripts/verify_wiring.py:223` |
| 17 | BitScale people connector | Stub: every operation raises | `connectors/providers/p_people.py` |
| 18 | Roadmap quality gate v1 | Orphaned; 14 NotConfigured placeholders | `src/gate/pipeline.py`, `gate/ports.py` |
| 19 | Word renderer in cloud | Defaults to `node`, NotConfigured, so cloud runs end PARTIAL | `exports/word/renderer.py:68` |
| 20 | Model routing with fallback | Blocked by missing benchmark; one provider adapter | `config/model_routing.yaml` |

Present and wired (good): tenants, memberships, accounts, runs with idempotency, the paid-call ledger, sources, facts with evidence trigger, grading engine and product grades, Lead three-query rule, 33-row tracker, people, jobs, tech, documents and chunks schema, audit hash chain, LangGraph checkpoints with tenant prefix, FORCE RLS on every tenant table (including the replica's new ones), file quarantine, sandboxed parsing, redaction tiers, leak check, internal-only document, deletion propagation, support grants.

## 5. Roadmap status

| Phase | Exit met? | Main blockers |
| --- | --- | --- |
| 0 Foundations | No | Decision log unsigned; model-split and people-source benchmarks blocked; only a synthetic golden account; CI red; staging deploy only prints commands |
| 1 Core pipeline | No | No live run ever recorded; BitScale stub; gate v1 orphaned; no parity report |
| 2 Speed and quality | No | Parallel execution off; credit manager v2 not wired; routing blocked; live evals never run; tracing off |
| 3 Multi-tenant product | No | Per-tenant storage unconfigured; client connectors NotConfigured; no payments; no pilots; no pen test |
| 4 Automation and signals | No | Refresh engine and monitors unwired; Slack, webhook, email NotConfigured; CRM write-back NotConfigured; DR drill is a rehearsal |

## 6. Operations, deployment and security

- **CI is red on `main`** (lint and types, Playwright, worker image push); nightly eval fails at routing governance; last green run 2 October; **no branch protection** on `main`.
- **Deploy cannot run:** GitHub has 0 environments, variables or secrets; Terraform has no state bucket or tfvars; the `abmgenerator` GCP project has no Cloud Run, SQL or buckets. Infra targets GCP while skill reference 10 assumes AWS, with no decision record.
- **Backups** are configured in Terraform but a restore was never tested. Secret rotation config exists but nothing runs it.
- **Secrets:** no real keys in tracked files or git history; `.env*` is ignored. **`web/.env.local` holds a real-looking Google OAuth client id and secret** for operator sign-in: move it to a secret manager and rotate it if it was ever shared.
- **Git:** both branches are local only (never pushed), and they diverge in both directions (MAIN's uncommitted `graph.py` and `tracker_v1.yaml` changes are not in the replica). Stray tracked files at the root: `run_sql.py`, `screenshot_all.*`, `verification-report.txt`, `AUDIT.md`, `BACKEND_AUDIT.md`, `ABM_Skill_Readers_Guide_v2.docx`. A third worktree exists under `~/.local/share/kilo/`.

## 7. Replica test failures (to fix before its next step)

1. `tests/agents/test_conversions.py::test_conversion_operator_only_file_is_internal_only` (forbidden name in `docs/build/BUILD_PROMPT.md:174`, `UNDERSTANDING.md:76`)
2. `tests/obs/test_contract_and_pure.py::...[check_telemetry.py]` (`getLogger` used in `api/routes/ask.py:36`, `replica/jobs.py:32`)
3. `tests/system/test_single_write_paths.py::test_each_table_is_written_only_by_its_owner` (no owner for `client_settings`, `credit_entries`, `notifications`, `question_categories`, `research_jobs`)
4. `tests/system/test_static_guards.py::test_one_retry_owner_per_failure_class_w6` (generated loops doc stale)
5. `tests/system/test_static_guards.py::test_every_file_has_its_header_and_every_guarantee_tag_resolves` (4 files missing headers)
6. `tests/system/test_static_guards.py::test_interrogate_docstring_coverage_is_complete` (99.7% of 100%)
7. `tests/system/test_wiring_contract.py::test_env_example_lists_exactly_what_the_code_reads` (`DEMO_PASSWORD` in `.env.example` is read by no code)

## 8. Technical Documentation PDF is outdated

22 statements are wrong compared with the code, for example: wrong `APP_ENV` values; files that do not exist (`ClientShell.tsx`, `web/design/`); wrong design tokens and fonts; operator pages that do not exist; findings F-02, F-04, F-08, F-09 and F-12 are already resolved in code; the research engine, CI, infra and evals are not described at all. It should be rewritten after the replica lands.

## 9. What only the owner can supply

1. The ABM research skill package (`Three_step_Opus_Fable_account_research_with_veeva_and_Mulesoft.skill`).
2. `ANTHROPIC_API_KEY` and the five data-provider keys (or the decision to drop some providers).
3. Google and Microsoft OAuth apps for Mail and drive.
4. A GCP project, region, billing, domain, and GitHub environments and secrets for deployment.
5. Decisions: credit value, people source (BitScale or Sumble), retention and DSAR policy (legal review), embedding model, whether to keep one billing ledger.
