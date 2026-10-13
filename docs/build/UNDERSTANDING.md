# Understanding: Account Intelligence before the final build (2026-10-13)

Read-only findings that the build in `BUILD_PROMPT.md` starts from. Line numbers were checked against the tree on branch `feat/research-files` at `aaf7330` plus its uncommitted work.

## 1. How a run works today

**Company run.**
1. `api/routes/runs.py:111` `create_run`. `_input_mode` (:186) picks the mode. `_company_domain` (:223) returns 422 `domain_required` unless the name matches exactly one account. Plan limits are checked (:146, :163).
2. `runs/lifecycle.py:125` writes `account_runs` (:158) and legacy `runs` (:189), debits the wallet (:203), and seeds the tracker (:218).
3. Back in `runs.py`, a 402 at :178, then slice 0 is enqueued (:182). `HttpTaskScheduler` (`platform/hermetic.py:148`) POSTs to the worker with no retry.
4. `api/routes/tasks.py:65` picks one of three graphs: `FileGraphFactory` (`documents/graph.py:392`), `AbmGraphFactory` (`agents/production/wiring.py:167`, when `uses_abm` :147), or Workflow v1 (`workflow/engine.py`, 13 stages).
5. All three run under `workflow/runner.py:166` `execute_slice`: lease, checkpointer thread `tenant:run` (:270), the slice ceiling (:150), and `_end` (:417). `_end` moves through AWAITING_APPROVAL (24h), DISTILLING, VERIFYING and `lifecycle.finish`/`settle_once` (:430).
6. Paid calls go through `connectors/service.py:133` → `ledger.claim` (`ledger.py:255`). That does args_hash replay (:66), cross-run cache adoption (`cache.py`), **the cap gate (:478-542)** and **`budget.reserve` ($5/60 calls)**.
7. Model calls go through `workflow/deps.py`. `reply_cache` is wired only when connectors are on (`container.py:425`), so `model_reply_log` holds 0 rows.
8. Build: `exports/builder.py:502` → `build_all` (:418) → Word summary mapper, which fails `missing_narrative` (`exports/readers/summary.py:243`).
9. Gate: `documents/gate.py:70`, wired at `container.py:432/576`. Deliverables go to `exports/store.py:53`.

**File run.**
1. Upload through `api/routes/documents.py`: uploads :105 → content :130 → complete :162.
2. `/tasks/document-process` runs `DocumentService.process` (`service.py:258`): scan (local signatures), then parse in a subprocess (`sandbox.py:77`), then redact, chunk, and embed with `HashingEmbedder` after confirm-company.
3. `POST /v1/runs` → `file_runs.py:116`, with the same wallet debit. `FileGraphFactory` then runs setup → extract → tracker → internal_file → gate → deliver.

**Where each stops on the hermetic stack.**
- The running worker has `UnconfiguredLlm`, not `FakeModel`: `LLM_FAKE` is unset.
- On the dev tenant `42a5d415` the `abm_agents` flag is **on**, so a company run fails at once with `NotConfigured` (`wiring.py:121`).
- Other tenants run Workflow v1. Each pauses for cap approval, then ends PARTIAL `build_failed` (`missing_narrative`) before the gate runs (observed on run c4fbaaa8).
- A file run with prose ends PARTIAL `model_not_configured`.
- Chat returns 503 (`api/main.py:75-77`).
- DB evidence: 28 runs, 0 COMPLETED, 1,902 paid calls (612 distinct, $221.64), 0 deliverables, 0 gate results, 0 uploaded documents.

## 2. Broken, beyond what BUILD_PROMPT section A lists

- **OI-TRK-2:** `lead_verification` can never be Done on a run with no Leads. It will block COMPLETED once PARTIAL is gone.
- **OI-ABM-8:** grading covers 4 attribute classes, so most row claims are refused as `unknown_attribute_class`.
- **OI-EXP-2/3/4/6:** job, people and tech links are not registered as sources, which fails the citation check.
- **OI-SPEND-1:** provider prices are $0.25 placeholders. The new rate table needs real prices.
- **OI-IM-8:** no zero-retention check runs before private text goes to the model.
- **OI-PPL-1/3/4:** `p_people` (BitScale) is unsupported until its grid contract is known.
- **AUDIT A4:** the router path leaves spend unrecorded after two schema failures.
- `/v1/runs/{id}/collected` (stored and missing data on a stopped run) is not in the section 6 route list. Keep it.
- **Stale facts in section A:**
  - `AbmGraphFactory` is at `wiring.py:167`.
  - The `Ask.tsx` failure path is at :106-114.
  - `build_all` is at `builder.py:418`.
  - `src/gate` is imported by `tests/unit/test_gate.py`.
  - The `abm_agents` flag is on in the local DB.
  - OI-IM-13 (in-process parse crash) applies to `research_files`; `documents` already parses in a subprocess.

## 3. The ABM skill

**The package is not on this machine.** Everything below comes from `ABM_Skill_Readers_Guide_v2.docx`, which covers v1.8 in 1,230 words, plus the PRD, which describes v1.5 with 12 steps.

**Files.** The package is 16 files under `abm-account-research/`:
- `SKILL.md`
- `references/{checklist, lessons, phase1-pull, phase2-build, tools, signals, output-spec}.md`
- assets: a Word helper, a people workbook builder, a jobs workbook builder, a tool-name stripper, and a deck example
- scripts: `third_party_scan.py`, a row-drop check, and a link and banned-dash check
- The PRD names the scripts `rowcheck.py`, `doc_checks.py`, `third_party_scan.py` and `strip_tool_names.py`.

**Steps.**
- **Eight steps:** set up, summary homework, detailed homework, people, distill, build files, pre-delivery gate, delivery.
- **Split mode:** Phase 1 pulls into files on the cheaper model. Phase 2 opens with a sense check (entity boundaries, adjacent organizations, hidden titles, contradictions), then builds.
- **Which files a step opens:** the guide does not map steps to reference files or scripts. It says that `SKILL.md` lines 10-21 are "Read these first, every run" (lessons, tools, signals, output-spec). This may limit how far progressive loading can go. Check it against the real `SKILL.md`.
- **The Word build uses Node (docx)** per PRD 4.1, so the script sandbox needs Node and a Word-to-PDF converter for the row check. No tech workbook builder is listed, so keep `exports/excel/tech.py` until the skill supplies one.

**Single-operator parts** (guide S13-S21 and PRD), each with its runtime answer:

| Part | Runtime answer |
| --- | --- |
| Pursuing client | Tenant profile |
| Credit cap at setup | First checkpoint |
| Ask before unapproved spend | Checkpoints |
| Sense-check pulls "once approved" | Checkpoints |
| BitScale consent | Tenant acknowledgement (`config/skill/consent.yaml` holds the approved text) |
| Pass 1 mail, calls, Drive | Off; in file mode the uploads are the internal history |
| "Rajesh Only" file | Never built (`name_digests.yaml` proves it) |
| In-chat repackaging | Registry |
| Pre-pulled broker files | Not offered |
| Broker balance check | Not offered |
| Deck only on request | Kept |

**Checklist.** `config/checklist.yaml` has 33 rows (5 in pass 1, 28 in pass 2), taken from PRD Appendix A, not the skill. The skill's `checklist.md` also holds standing requirements (at least 14), the pre-delivery gate, scope defaults (MuleSoft counted under Salesforce) and a change log v1.0 to v1.8. The hand-built YAML has none of these (RECONCILIATION C31). Once the package arrives, the rows come from the skill version.

## 4. Baseline tests

- **Isolation:** 467 passed.
- **Web, all 15 files:** 6 files fail, 16 of 68 tests. The causes are:
  - stale exports: `sourceLabel`, `allStrings`, and undefined components
  - grade-class expectations
  - the missing `:root[data-theme="dark"]` block
  - copy-rule hits
- **Full Python suite:** see section 8.

## 5. Delete

The verified inventory is the deletion report kept with this build:
- `spend/` (4,457 lines), `metering/` (except reporting), `governance/`, `connectors_client/`, `crm_writeback/`, `research_files/` (5,199 lines)
- `admin/quotas.py`, `agents/production/credit.py`, `workflow/stages/spend.py`, and the limits in `connectors/budget.py`
- Wallet code in `lifecycle.py`, `file_runs.py` and `documents.py`
- The seats limit (`users.py:79,131`, `identity/broker.py:378`) and `/v1/chat/provider-key`
- Routes: cap, spend, spend/live, usage, connectors, crm, the research_files `/v1/files`, monitoring/budget, quotas, plan
- Config: billing, plans, spend, pricing (orphan), budgets, connectors, and chat turn limits
- 53 legacy web files and every redirect stub
- Workflow v1, the ABM graph and legacy `runs`, removed **after** acceptance tests 1 to 3 pass

**Not deleted:**
- The provider-pacing governor tables (`provider_leases` holds 2,333 rows). They pace providers; they do not cap users.
- `llm/budget.py`.

## 6. Add

- **Skill registry:** `skill_versions` table plus a loader from `skills/abm-account-research/`. The fixture skill lives in `tests/fixtures/skills/`.
- **Skill executor:** a worker agent loop with `read_skill_file`. Data tools go through the ledger and cache. A quarantined extractor reads raw web text (Arch ADR-005). Skill scripts are run by the executor in a no-network sandbox and are never offered to the agent as tools.
- **State machine on `account_runs`:** QUEUED, RUNNING, PAUSED_AWAITING_APPROVAL, GATE, COMPLETED, FAILED, STOPPED_BY_USER.
- **Meter:** `run_checkpoints` and a rate table. `reply_cache` is wired whether connectors are on or off.
- **Routes:**
  - `POST /v1/runs/{id}/checkpoint`
  - `POST /v1/chat` with normal and deep modes
  - `/v1/files/*` backed by `documents`
  - account tables with keyset paging
  - deliverables zip download
  - `PUT /v1/accounts/{id}/schedule`, wired to `src/refresh/`
- **Operator console:** users and runs lists, settings, skill versions.
- **Web:** five client screens and five admin pages, global error pages, CSP nonce, overlay mobile menu, Playwright with axe.

## 7. Risks and open questions

1. **Skill package absent.** The executor is proven only on a fixture skill. Acceptance tests 1 and 4 are blocked. Script interfaces are guesses until the package arrives, so the executor maps steps to scripts through a runtime table.
2. **Unit size.** Decided by the owner on 2026-10-13: 1 unit = 1,000 model tokens. Tool spend converts through the rate table at the same scale, and the default checkpoint stays at 1,000 units. Arch 16.1 puts a standard run at about 615k tokens, so a deep run pauses about once or twice. The unit and the checkpoint size are both settings.
3. **Tables with data to drop:**
   - wallets 3, wallet_ledger 87, legacy runs 28
   - run_cap_versions 12, run_cap_events 24, balance_snapshots 60, spend_reports 28
   - plan_limits 12, plan_assignments 2, usage_counters 5, quota_versions 6, monitoring_budget_versions 1
   - research_files tables: 2 to 275 rows each
   - Arch 7.3 says never delete the wallet ledger.
   - Decided by the owner on 2026-10-13: export each table to CSV in a gitignored folder, then drop it in a new forward migration.
4. **`provision_tenant()` (0026) inserts a wallet.** It must be replaced in the same migration that drops `wallets`.
5. **`documents` imports `research_files`** (parsers, blobstore, redaction helpers). That code must move before `research_files` is deleted.
6. **One client role vs SaaS/Arch** (admin, seller, viewer). Who sets redaction policy and marks a source authoritative? Proposed: any member, audited.
7. **The 4-hour run ceiling (Arch 5.4) vs pausing indefinitely.** Paused time must not count toward the ceiling.
8. **Uploading a skill from the admin console skips Arch 12.2's CI eval gate.** Activation should require the fixture-run check to pass on the new version.
9. **Decisions to record:**
   - remove the tenant model key (no source mentions it)
   - remove CRM (Phase B; tables empty)
   - idle-access expiry (prototype only)
   - audio (not in scope without a vendor)
   - embeddings (Arch suggests a 1536-dimension model, a second sub-processor needing zero retention)

## 8. Full Python suite

`pytest -m "not eval"` against a scratch `ai_test` database, Postgres tests included: **3,779 passed, 13 skipped, 0 failed**, 2 deselected, in 13 minutes 19 seconds. That includes `tests/system/test_fake_model_scenarios.py`, the one audit test (F6) not verified until now. Skips:

- 12 connector tests marked `[VERIFY] provider fact not sourced` (see `docs/providers/CLAIMS.md`)
- 1 research_files large-file test, which needs `RF_LARGE_FILE=1`

The suite is green while no run has ever completed. The tests check the parts, not a whole run end to end on the stack.
