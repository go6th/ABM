#!/usr/bin/env bash
# Irreversible clean-up steps for the owner to run by hand (unwanted-things/README.md).
# Nothing here has been run. Read each step before running it; each one is independent.
set -euo pipefail
cd "$(dirname "$0")/.."

# 1. The old Claude worktree (775 MB, branch claude/angry-banach-78a658). Check that its branch
#    holds nothing you still want first: git log --oneline feat/final-product..claude/angry-banach-78a658
# git worktree remove ".claude/worktrees/angry-banach-78a658"
# git branch -D claude/angry-banach-78a658

# 2. Old Next.js build output parked under unwanted-things/web (generated; rebuilt on demand).
# rm -rf unwanted-things/web/.next-srclabel unwanted-things/web/.next-live unwanted-things/web/.next-livebuild

# 3. The local dev build of the 2026-10-27 tabs check stack.
# rm -rf unwanted-things/local-junk/web/.next-tabs

# 4. Scratch databases of the 2026-10-27 audit (the tabs check stack and its test run).
# docker exec -e PGPASSWORD=dev abmgenerator-db-1 psql -U postgres -c "DROP DATABASE IF EXISTS ai_tabs_ui"
# docker exec -e PGPASSWORD=dev abmgenerator-db-1 psql -U postgres -c "DROP DATABASE IF EXISTS ai_test_tabs1027"

# 5. Older scratch test databases in the local cluster (ai_test_*). List them first:
#    docker exec -e PGPASSWORD=dev abmgenerator-db-1 psql -U postgres -Atc "SELECT datname FROM pg_database WHERE datname LIKE 'ai_test_%'"
#    then drop the ones no session is using, one by one.

# 6. The real account's workspace zip and its tests are still in git HISTORY (they were committed
#    before 2026-10-30; the files themselves are parked in unwanted-things/real-account-fixtures/).
#    To remove them from history (rewrites every commit id, needs a force push and every clone
#    re-cloned; take a backup branch first), after `pip install git-filter-repo`:
# git branch backup/before-history-purge
# git filter-repo --invert-paths --path tests/fixtures/gsk --path tests/documents/test_tables_real_gsk.py --path tests/evals/test_tabular_gsk_live.py --path evals/tabular_gsk --path web/shots
#    and, once nobody needs the parked copies any more:
# rm -rf unwanted-things/real-account-fixtures

# 7. (2026-11-02) Generated output parked under unwanted-things/local-junk/ (all rebuilt on demand):
#    web/.next-caps, web/.next-e2e, web/.next-tabs (1.4 GB), web/test-results, docs-audit-e2e/ (old e2e Word files and zips).
# (DONE 2026-11-02: deleted.)

# 8. (2026-11-02) The other three Claude worktrees (about 3.1 GB). Check each branch first, as in step 1:
#    git log --oneline feat/final-product..claude/<name>
# (DONE 2026-11-02: removed busy-thompson-0cc98e)
# (DONE 2026-11-02: removed dreamy-chaplygin-5ec24d)
# (DONE 2026-11-02: removed gcp-research-status-35156c)
# (DONE 2026-11-02: three branches deleted)

# 9. (2026-11-03) Four agent worktrees left by earlier attempts at the 2026-11-03 fix prompts. Their
#    work is superseded by the uncommitted fix-prompt work in the main folder (A-E). Each branch holds
#    commits not on feat/final-product; check them first, as in step 1:
#      git log --oneline feat/final-product..worktree-agent-a1fbc00695a37e445   # prompt C attempt (4)
#      git log --oneline feat/final-product..worktree-agent-a5b8c7cad45404b9e   # prompt D task 1 attempt (1)
#      git log --oneline feat/final-product..worktree-agent-a82f99494b4567cdb   # prompt A attempt (3)
#      git log --oneline feat/final-product..worktree-agent-aced12de48f548427   # prompt B attempt (6)
#    They also hold the real-account files the history purge removes (scripts/ops/purge_real_fixtures.sh
#    lists them): remove them BEFORE the history rewrite (docs/runbooks/history-rewrite.md).
# for w in a1fbc00695a37e445 a5b8c7cad45404b9e a82f99494b4567cdb aced12de48f548427; do
#   git worktree remove ".claude/worktrees/agent-$w" && git branch -D "worktree-agent-$w"
# done
