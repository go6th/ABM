# unwanted-things

Files that are not part of the product, parked here instead of deleted (owner rule, 2026-10-16:
move, never delete). Nothing in `src/`, `tests/`, `config/`, `migrations/` or `web/` imports
anything here. Steps that cannot be undone (removing a worktree, dropping databases, deleting
build folders) are written into `cleanup.sh` for the owner to run; nothing in it has been run.

## Inventory

| Path | What it is | Why it was parked | Tracked |
|---|---|---|---|
| `tests/connectors/test_verify.py` | skipped placeholder tests for provider facts not yet verified ([VERIFY] in `docs/providers`) | parked in an earlier session; every test in it was a skip | no |
| `web/components/ProviderKey.tsx` | an old provider key form | parked in an earlier session; no screen imports it | no |
| `web/.next-srclabel/`, `web/.next-live/`, `web/.next-livebuild/` | old Next.js dev and build output from side stacks | generated output; 0 references | no (ignored) |
| `web/app-design/gallery.tsx`, `web/app-design/page.dev.tsx` | the `/__design` gallery route, already emptied to stubs (`export {}`, a page returning null) | nothing links to it and the route rendered nothing (audit 2026-10-27) | yes (moved with `git mv`) |
| `local-junk/abm-account-research-v1.11-session-installed.zip` | a copy of the ABM skill package (v1.11) that sat in the repository root | no file reads it; the skill the research follows lives in `config/skill/` | no (ignored) |
| `local-junk/projects/plugins/` | 43 Claude Code plugin checkouts (16 MB) | editor tooling, not product code; nothing references `projects/` | no (ignored) |
| `real-account-fixtures/tests/fixtures/gsk/` (a real account's workspace zip, 22 files with raw tool results), `real-account-fixtures/tests/documents/test_tables_real_gsk.py`, `real-account-fixtures/tests/evals/test_tabular_gsk_live.py`, `real-account-fixtures/evals/tabular_gsk/cases.yaml` | the tracked fixture, tests and live-eval cases built on a real client account's workspace | owner decision 2026-10-30: no real account's data in the repository. Replaced by a synthetic workspace (`tests/documents/workspace_zip.py`, `tests/documents/test_tables_workspace_zip.py`, `tests/evals/test_tabular_live.py`, `evals/tabular_synthetic/cases.yaml`). Move them back to their original paths together to run the old tests. Git history still holds the old zip (no rewrite; see `cleanup.sh` step 6) | yes (moved with `git mv`) |
| `real-account-fixtures/web/shots/` (`structured-answer-1440.png`, `structured-answer-390.png`, `structured-preview.html`) | generated preview shots of the structured answer, rendered from that real account's answer | same decision; `web/scripts/preview-structured.tsx` now renders a fictional account and rewrites `web/shots/` when run | yes (moved with `git mv`) |
| `retired-stubs/src/{security,metrics,registers,unitcost}/`, `retired-stubs/tests/crosscut/test_x5_security.py` .. `test_x8_registers.py` | four packages whose every function returned a constant (`beta_gate() -> True`, `soc2.coverage() -> 1.0`, `rotation.overdue() -> []`, `secrets_scan.scan() -> []`, the rest `pass`) and the `assert True` tests that "covered" them | they made the section 11 report and `make scan` print Done/pass for checks nothing performed (Prompt D task 1, ADR-0122). Nothing imports them; `tests/crosscut/test_no_stub_evidence.py` fails if they come back to `src/` | no (moved with `mv`; `git status` shows them deleted from `src/`) |
| `dead-code-2026-11/src/{privacy,quality,contracts}/`, `dead-code-2026-11/tests/crosscut/test_x2_contracts.py`, `test_x3_quality.py`, `test_x4_privacy.py`, `dead-code-2026-11/config/{drift,dsar,regression}.yaml` | the privacy toolkit (DSAR, minimisation, retention, suppression, tombstones), the quality release and regression checks, the connector contract-drift monitor, with their tests and configs | no importer in `src/`, `scripts/` or `evals/` (grep evidence in `docs/audits/purge-2026-11.md`; Prompt F, ADR-0136) | yes (moved with `git mv`) |
| `dead-code-2026-11/src/account_intelligence/rag/` (graph, prompt, reflection, cache, access, guardrails, graphrag, metrics, obs, synthetic, config), `dead-code-2026-11/tests/rag/`, `dead-code-2026-11/config/rag.yaml` | the agentic RAG question loop and its tests | `rag.graph` had no importer and the rest were reached only from it; `documents/` keeps `rag.retrieval`, `rag.tokenizer`, `rag.models` (ADR-0136). `tests/rag/test_retrieval_config_guard.py` is the `RetrievalConfig` test cut out of `tests/rag/test_retrieval.py` | yes (moved with `git mv`; the guard test is new) |
| `dead-code-2026-11/src/verify/narrative.py`, `dead-code-2026-11/src/verify/prompts/{narrative_reread,source_reread}.v1.md`, `dead-code-2026-11/tests/verify/test_verify_narrative.py` | the narrative re-read and the superseded v1 re-read prompt | only a test imported `verify.narrative`; `verify/stage.py` loads `source_reread.v2` (ADR-0136) | yes (moved with `git mv`; the test was cut out of `tests/verify/test_verify.py`) |
| `orphan-routes-2026-11/` (`chat_provider_key_and_stream.py`, `chat_grounding.py`, `runs_pull_option.py`, `parity_edits.py`, `tests/test_chat_grounding.py`) | the handlers of `GET/PUT/DELETE /v1/chat/provider-key`, `POST /v1/chat/threads/{thread_id}/turns/stream`, `GET /v1/runs/pull-option`, `POST /v1/accounts/{domain}/parity/edits`, and the helpers only they called | no web caller (`docs/audits/orphan-routes.md`, re-checked 2026-11-03); the `.py` files are cut-outs, not importable as they stand: restore the pieces to the files each header names (ADR-0136) | `chat_grounding.py` and its test moved with `git mv`; the other three are new files cut from tracked ones |
| `docs-frontend-2026-11/` | `docs/frontend/` (UI capability map, operator route map) | they describe screens removed in prompt B | yes (moved with `git mv`) |
| `docs-guide-2026-10-29/` (pages 01 to 06) | the 29 October project guide | superseded by `docs/handover/` (ADR-0136). `docs/guide/README.md` and `00-structure-map.md` were left in place: they are uncommitted work of another session | yes (moved with `git mv`) |
| `render-docx-scratch/` | two throwaway comparison scripts written while bringing `render/docx/` to parity with the reference Word files (2026-10-27) | one-off diagnostics; the parity checker `scripts/parity/parity_check.py` is the maintained check | no |

`next.config.ts` still lists `dev.tsx` as a development-only page extension; with the gallery
parked no such page exists, and the setting is harmless.

## Not parked (checked 2026-10-27, re-checked 2026-11-02; superseded for the modules above by the 2026-11-03 purge)

* Every module under `src/` is imported somewhere (the three `agents/pass1`, `agents/pass2` and
  `agents/people` packages hold the prompts the extraction step loads by path).
* `vulture` at 80% confidence reports nothing in `src/` (2026-11-02).
* Every script under `scripts/` is referenced by the Makefile, CI, docs or tests.
* Every web component and library file is imported by a screen or a test.
* `.aws/` and `.codex/` in the repository root are empty folders the sandbox creates; they hold
  nothing and are left alone.
- purge-work-in-progress/ (2026-10-30; MOVED INTO THE LIVE TREE 2026-10-31): the account purge (migration 0096, src/account_intelligence/ops/purge_account.py, tests/db/test_purge_deleted_account.py) is now part of the product.
