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
