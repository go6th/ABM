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
# rm -rf web/.next-tabs

# 4. Scratch databases of the 2026-10-27 audit (the tabs check stack and its test run).
# docker exec -e PGPASSWORD=dev abmgenerator-db-1 psql -U postgres -c "DROP DATABASE IF EXISTS ai_tabs_ui"
# docker exec -e PGPASSWORD=dev abmgenerator-db-1 psql -U postgres -c "DROP DATABASE IF EXISTS ai_test_tabs1027"

# 5. Older scratch test databases in the local cluster (ai_test_*). List them first:
#    docker exec -e PGPASSWORD=dev abmgenerator-db-1 psql -U postgres -Atc "SELECT datname FROM pg_database WHERE datname LIKE 'ai_test_%'"
#    then drop the ones no session is using, one by one.
