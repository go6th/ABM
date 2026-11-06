# Runbook: remove real customer data from git history

Owner-only. Rewrites every commit id on every branch of `go6th/ABM`. Written 2026-11-03 (fix
prompt A, task 2; ADR-0107). Nothing in this runbook has been run yet.

## Why

`GSK_workspace.zip` (a real client's workspace: deliverables, people lists, raw provider results)
and the tests and eval cases built on it were committed on 2026-10-17 and are reachable from
`origin/main`, `origin/feat/final-product` and `origin/claude/busy-thompson-0cc98e`.
**The repository is PUBLIC** (`gh repo view go6th/ABM --json visibility` on 2026-11-03), so the
files can be downloaded by anyone today. The unpushed commit `87f4c12e` also adds
`unwanted-things/backup-2026-11-01-before-reset/untracked.tar`, which holds workspace inventories
of eleven real client accounts; it must never be pushed.

The paths purged are listed once, in `scripts/ops/purge_real_fixtures.sh` (`PURGE_PATHS`).

## Steps

1. **Make the repository private now** (minutes, reversible, does not wait for the rewrite):
   `gh repo edit go6th/ABM --visibility private --accept-visibility-change-consequences`.
   Check forks: `gh api repos/go6th/ABM/forks --jq '.[].full_name'`. A public fork keeps the
   data after the rewrite; ask its owner to delete it, or ask GitHub Support.

2. **Keep the unpushed work, without the backup files.** In the working folder the three commits
   `6ee2ebe8`, `d3831549`, `87f4c12e` are not on GitHub. Save them as patches without the backup
   folder (the patch files stay outside the repository):

   ```
   mkdir -p ~/abm-private && chmod 700 ~/abm-private
   cp -a "unwanted-things/backup-2026-11-01-before-reset" ~/abm-private/
   git format-patch origin/feat/final-product..feat/final-product -o ~/abm-private/unpushed \
     -- . ':(exclude)unwanted-things/backup-2026-11-01-before-reset'
   ```

   Also commit or stash any uncommitted work first and note it.

3. **Dry run** (reads only): `scripts/ops/purge_real_fixtures.sh`. It lists the commits, every
   ref that holds them and the blob counts. Local refs it lists (`backup/*`,
   `worktree-agent-*`, `refs/agents/*` checkpoints) are rewritten only in the mirror; delete the
   local copies after step 7 with the re-clone.

4. **Rewrite in a fresh mirror clone**, never in the working folder (it has linked worktrees and
   other sessions use it):

   ```
   uv tool install git-filter-repo          # or: pip install --user git-filter-repo
   git clone --mirror https://github.com/go6th/ABM ~/abm-purge/ABM.git
   scripts/ops/purge_real_fixtures.sh --execute --repo ~/abm-purge/ABM.git \
     --backup-dir ~/abm-private/purge-backup
   ```

   The script refuses anything that is not a bare mirror, saves a bundle and the purged files
   into the backup folder (mode 700), runs `git filter-repo --invert-paths`, and verifies that
   no purge path is reachable. `filter-repo` removes the `origin` remote; that is expected.

5. **Force push** (the outward step; check the dry run in the mirror shows "none" first):

   ```
   cd ~/abm-purge/ABM.git
   git remote add origin https://github.com/go6th/ABM
   git push --force --mirror origin
   ```

   `--mirror` also deletes remote branches that no longer exist in the mirror. Branch protection
   on `main` must allow the force push for the duration (re-enable afterwards).

6. **GitHub caches.** Old commits stay reachable by SHA, and through `refs/pull/*` of any pull
   request, until GitHub garbage-collects them. Open a GitHub Support request ("remove cached
   views / sensitive data", name the repository and the old commit ids from step 3) so they are
   purged. Until then treat the data as exposed.

7. **Every collaborator re-clones.** An old clone pushes the purged history back. Each person:
   push nothing, `git clone https://github.com/go6th/ABM` into a new folder, re-apply local work
   with `git am ~/abm-private/unpushed/*.patch` (for the owner) or by copying files, then delete
   the old folder. In `/home/sakib/ABM Generator` remove the linked worktrees under
   `.claude/worktrees/` first (`git worktree list`). Cloud Build triggers and CI caches built
   from old SHAs need nothing: they rebuild from the new tip.

8. **Delete the parked copies** once nobody needs them: `rm -rf unwanted-things/real-account-fixtures`
   and the backup folder (they are ignored now, `.gitignore` lines after "real customer data").

## Rotation list

Every key or token that was ever inside `unwanted-things/` (current tree and every historical
blob of the purge paths, scanned 2026-11-03 for provider keys, private keys, GitHub and Slack
tokens and connection strings with passwords; locations only, never values):

| Where | What | Rotate? |
|---|---|---|
| `unwanted-things/cleanup.sh:19`, `:20`, `:23` | `PGPASSWORD` of the local docker Postgres (compose.yml dev password) | no: local dev only, not used by any cloud database; it is already public in `compose.yml` |
| `unwanted-things/backup-2026-11-01-before-reset/tracked.patch:9599` | a string in a web unit test (`apiMock` PUT path), not a credential | no |
| GSK zip, `untracked.tar`, every past version of the purge paths | none found | no |

No provider API key, cloud credential, session secret or private key was found. If the owner
knows of a key that was ever pasted into a parked file that the scan cannot see (for example in a
binary), rotate it in Secret Manager regardless.
