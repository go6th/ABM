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
| `render-docx-scratch/` | two throwaway comparison scripts written while bringing `render/docx/` to parity with the reference Word files (2026-10-27) | one-off diagnostics; the parity checker `scripts/parity/parity_check.py` is the maintained check | no |

`next.config.ts` still lists `dev.tsx` as a development-only page extension; with the gallery
parked no such page exists, and the setting is harmless.

## Not parked (checked 2026-10-27)

* Every module under `src/` is imported somewhere (the three `agents/pass1`, `agents/pass2` and
  `agents/people` packages hold the prompts the extraction step loads by path).
* Every script under `scripts/` is referenced by the Makefile, CI, docs or tests.
* Every web component and library file is imported by a screen or a test.
* `.aws/` and `.codex/` in the repository root are empty folders the sandbox creates; they hold
  nothing and are left alone.
- purge-work-in-progress/ (2026-10-30): unfinished purge of a deleted account (migration 0096, ops command, tests), stopped by the owner mid-way. Moved here so the tree matches the deployed build rel-9390fb5. changed-files/ holds the agent's edited copies of health.py (EXPECTED_REVISION 0096), tenant_plane.py, tenancy/lifecycle.py, storage.py, config/deletion.yaml.
