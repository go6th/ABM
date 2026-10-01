#!/usr/bin/env bash
# Run every gate this environment can run and print PASS, FAIL or SKIP for each.
# A SKIP is never a pass: report it as "not run" with its reason.
#
#   bash .claude/skills/account-intelligence/scripts/gates.sh
#
# Database gates need PG_BIN (directory with initdb and pg_ctl) or TEST_DATABASE_URL.
# Exit 1 if any gate FAILED.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
SKILL="$ROOT/.claude/skills/account-intelligence"
PLATFORM="$ROOT/.claude/skills/account-intelligence-platform"
PY="${PY:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="python3"
cd "$ROOT"

results=()
failed=0
record() { results+=("$(printf '%-6s %-34s %s' "$1" "$2" "${3:-}")"); [[ "$1" == FAIL ]] && failed=1; return 0; }
run() {  # run NAME CMD...
  local name="$1"; shift
  local log; log="$(mktemp)"
  if "$@" >"$log" 2>&1; then record PASS "$name" "$(tail -n 1 "$log")"
  else record FAIL "$name" "$(tail -n 1 "$log")"; sed 's/^/    | /' "$log" | tail -n 25; fi
  rm -f "$log"
}

# 1. Code quality and offline tests
run "lint, format, types, tests" make -s app-check

# 2. Skill self-tests and config alignment
run "platform skill selftest" bash "$PLATFORM/scripts/selftest.sh"
run "platform assets vs config" "$PY" "$SKILL/scripts/check_alignment.py"

# 3. Database gates
DB_URL=""
CLUSTER=""
cleanup() { [[ -n "$CLUSTER" ]] && "$PG_BIN/pg_ctl" -D "$CLUSTER/data" -m fast stop >/dev/null 2>&1; [[ -n "$CLUSTER" ]] && rm -rf "$CLUSTER"; }
trap cleanup EXIT
if [[ -n "${TEST_DATABASE_URL:-}" ]]; then
  DB_URL="$TEST_DATABASE_URL"
elif [[ -n "${PG_BIN:-}" && -x "${PG_BIN}/initdb" ]]; then
  CLUSTER="$(mktemp -d)"
  PORT="$("$PY" -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])')"
  if "$PG_BIN/initdb" -D "$CLUSTER/data" -U postgres -A trust --no-sync >/dev/null 2>&1 \
     && "$PG_BIN/pg_ctl" -D "$CLUSTER/data" -l "$CLUSTER/log" -w start \
        -o "-p $PORT -k $CLUSTER -c listen_addresses=127.0.0.1 -c fsync=off" >/dev/null 2>&1; then
    DB_URL="postgresql://postgres@127.0.0.1:$PORT/postgres"
  else
    record FAIL "start throwaway Postgres" "see $CLUSTER/log"
  fi
fi

if [[ -n "$DB_URL" ]]; then
  run "database tests (tests/app/db)" env TEST_DATABASE_URL="$DB_URL" "$PY" -m pytest tests/app/db -q
  if [[ -n "$CLUSTER" ]]; then
    ALEMBIC=("$PY" -m alembic -c app/db/alembic.ini)
    run "migrate: upgrade head" env DATABASE_URL="$DB_URL" "${ALEMBIC[@]}" upgrade head
    run "migrate: models match (check)" env DATABASE_URL="$DB_URL" "${ALEMBIC[@]}" check
    run "tenancy report (R-01)" "$PY" "$SKILL/scripts/check_tenancy.py" --url "$DB_URL"
    run "migrate: downgrade base" env DATABASE_URL="$DB_URL" "${ALEMBIC[@]}" downgrade base
  else
    record SKIP "migration round trip" "TEST_DATABASE_URL is shared; round trip needs PG_BIN"
  fi
else
  record SKIP "database gates" "set PG_BIN or TEST_DATABASE_URL"
fi

# 4. Infrastructure
TF="${TERRAFORM:-terraform}"
if command -v "$TF" >/dev/null 2>&1; then
  run "terraform fmt and validate" make -s tf-validate TERRAFORM="$TF"
  find infra -name .terraform -type d -prune -exec rm -rf {} + 2>/dev/null
else
  record SKIP "terraform validate" "terraform not installed (set TERRAFORM=...)"
fi

echo
echo "Gate results"
printf '%s\n' "${results[@]}"
if [[ $failed -eq 0 ]]; then echo "gates: no failures (SKIPs above were not run)"; else echo "gates: FAILED"; exit 1; fi
