# ADR-0107: real customer data is removed from git history, not only parked

- Status: Accepted (script and runbook ready; the rewrite itself is the owner's step)
- Date: 2026-11-03
- Deciders: owner (fix prompt A)

## Context

On 2026-10-30 the real GSK workspace zip, its tests and eval cases were moved into
`unwanted-things/real-account-fixtures/` ("move, never delete", 2026-10-16). Moving keeps every
old blob in history, and `go6th/ABM` is a PUBLIC repository: the zip is downloadable from
`origin/main`. The unpushed commit `87f4c12e` adds a backup tar holding workspace inventories of
eleven real client accounts.

## Decision

Real customer data is purged from all history with `git filter-repo --invert-paths`, run only in
a bare mirror clone by `scripts/ops/purge_real_fixtures.sh` (dry run by default; `--execute`
refuses a non-bare repository and a backup folder inside the repository, saves a bundle and the
purged files first, verifies afterwards, never pushes). The steps around it (private visibility
first, saving the unpushed work without the backup, force push, GitHub Support for cached SHAs,
re-clone, rotation list) are in `docs/runbooks/history-rewrite.md`. `.gitignore` refuses the
fixture paths, `unwanted-things/backup-*/` and `*.tar` so the data cannot be re-added by
accident. "Move, never delete" stays the rule for code; it never applies to customer data.

## Consequences

Every commit id changes; every clone and worktree must be re-made, and the 87f4c12e work is
carried over as patches. No test or script depends on the purged files (grep, 2026-11-03); the
synthetic workspace fixture (`tests/documents/workspace_zip.py`) already replaced them. No
credential was found in the purged material, so nothing needs rotating.
