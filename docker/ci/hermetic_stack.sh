#!/usr/bin/env bash
# The just-built images as a hermetic stack of plain `docker run` containers (no compose, no
# cloud, no paid call): Postgres with pgvector, migrate job, fake identity provider, worker, dev
# object store, api and web, all on one private network. LLM_FAKE=hermetic and CONNECTORS=hermetic
# run the whole application on the deterministic fake model and synthetic providers. Staging
# refuses both switches by design, so this is the only place the end-to-end run happens.
#
# Usage:  hermetic_stack.sh up | check | down
#   up     start the stack and seed the synthetic demo workspace
#   check  hermetic research run, then the authenticated cross-tenant probe
#   down   remove every container and the network this script created
# Environment (names only; no value is a secret, the seed password is generated per run):
#   API_IMAGE WORKER_IMAGE WEB_IMAGE   image references (default abm-agent-ci-{api,worker,web}:check)
#   STACK                              container-name prefix (default abm-ci-hs)
#   API_PORT WEB_PORT DB_PORT          published on 127.0.0.1 (defaults 18090 13010 15433)
set -Eeuo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
stack="${STACK:-abm-ci-hs}"
api_image="${API_IMAGE:-abm-agent-ci-api:check}"
worker_image="${WORKER_IMAGE:-abm-agent-ci-worker:check}"
web_image="${WEB_IMAGE:-abm-agent-ci-web:check}"
api_port="${API_PORT:-18090}"
web_port="${WEB_PORT:-13010}"
db_port="${DB_PORT:-15433}"
idp_port="${IDP_PORT:-19090}"
net="${stack}-net"
state="${HS_STATE_DIR:-${RUNNER_TEMP:-/tmp}/${stack}-state}"

# Diagnostic only (default off): some modules (replica/credits.py, replica/jobs.py,
# connectors/hermetic.py) find config/ (and replica/jobs.py also skills/) relative to the package file, which only works
# from a source tree. In an installed image those paths are wrong and POST /v1/runs answers 500.
# PACKAGE_PATH_SHIM=1 mounts config/ at the path those modules look in, to let the rest
# of the stack be checked. CI runs without it, so the defect fails the build until src/ is fixed.
shim=()
if [[ "${PACKAGE_PATH_SHIM:-0}" == "1" ]]; then
  shim=(-v "$root/config:/app/.venv/lib/python3.12/config:ro")
fi

app_env=(
  -e APP_ENV=dev -e LOG_LEVEL=INFO
  -e DATABASE_URL=postgresql+asyncpg://app_rw:local-dev-only@db:5432/ai
  -e CHECKPOINT_DATABASE_URL=postgresql://app_rw:local-dev-only@db:5432/ai
  -e JWKS_URL=http://fake-idp:9090/jwks -e JWT_ISSUER=http://fake-idp:9090/
  -e JWT_AUDIENCE=account-intelligence-local -e PAYLOAD_DIR=/tmp/payloads
  -e "PUBLIC_BASE_URL=http://localhost:${web_port}"
  -e LLM_FAKE=hermetic -e CONNECTORS=hermetic -e RESEARCH_ENGINE=graph
  -e DEVSTORE_PUBLIC_URL=http://localhost:8092
)

wait_for() { # description, tries, command...
  local what="$1" tries="$2"; shift 2
  for _ in $(seq 1 "$tries"); do "$@" >/dev/null 2>&1 && return 0; sleep 2; done
  printf 'timeout waiting for %s\n' "$what" >&2
  return 1
}

up() {
  mkdir -p "$state"
  docker network create "$net" >/dev/null
  docker volume create "${stack}-payloads" >/dev/null
  docker run -d --name "${stack}-db" --network "$net" --network-alias db \
    -p "127.0.0.1:${db_port}:5432" -e POSTGRES_PASSWORD=dev -e POSTGRES_DB=ai \
    pgvector/pgvector:pg16 >/dev/null
  wait_for "database" 60 docker exec "${stack}-db" pg_isready -U postgres -d ai
  # pg_isready answers during the first start-up pass; a query proves the final server is up
  wait_for "database queries" 30 docker exec "${stack}-db" psql -U postgres -d ai -c 'select 1'

  docker run --rm --name "${stack}-migrate" --network "$net" \
    -e SERVICE_ROLE=migrate -e MIGRATOR_DATABASE_URL=postgresql://postgres:dev@db:5432/ai \
    -e APP_RW_PASSWORD=local-dev-only "$api_image" alembic upgrade head

  # the payload volume is created root-owned; the images run as uid 10001
  docker run --rm --user 0 --entrypoint chown -v "${stack}-payloads:/tmp/payloads" \
    "$worker_image" -R 10001:10001 /tmp/payloads

  docker run -d --name "${stack}-fake-idp" --network "$net" --network-alias fake-idp \
    -p "127.0.0.1:${idp_port}:9090" \
    -e APP_ENV=dev -e FAKE_IDP_ISSUER=http://fake-idp:9090/ \
    -e FAKE_IDP_AUDIENCE=account-intelligence-local \
    -v "$root/ops/dev:/dev-tools:ro" --entrypoint python "$worker_image" /dev-tools/fake_idp.py >/dev/null
  docker run -d --name "${stack}-worker" --network "$net" --network-alias worker \
    "${app_env[@]}" "${shim[@]}" -e SERVICE_ROLE=worker -e ALLOW_UNAUTHENTICATED_TASKS=1 \
    -e WORKER_URL=http://worker:8081 -v "${stack}-payloads:/tmp/payloads" \
    --entrypoint uvicorn "$worker_image" account_intelligence.api.main:app \
    --host 0.0.0.0 --port 8081 >/dev/null
  docker run -d --name "${stack}-objectstore" --network "$net" --network-alias objectstore \
    -e APP_ENV=dev -e DEVSTORE_ROOT=/tmp/payloads/files -e DEVSTORE_PUBLIC_URL=http://localhost:8092 \
    -e "DEVSTORE_ALLOW_ORIGIN=http://localhost:${web_port}" -v "${stack}-payloads:/tmp/payloads" \
    --entrypoint uvicorn "$worker_image" devstore.server:app --host 0.0.0.0 --port 8092 >/dev/null
  docker run -d --name "${stack}-api" --network "$net" --network-alias api \
    -p "127.0.0.1:${api_port}:8080" "${app_env[@]}" "${shim[@]}" -e SERVICE_ROLE=api \
    -e WORKER_URL=http://worker:8081 -v "${stack}-payloads:/tmp/payloads" "$api_image" >/dev/null
  # API_ORIGIN is fixed at web build time (http://api:8080), hence the network alias above.
  docker run -d --name "${stack}-web" --network "$net" -p "127.0.0.1:${web_port}:3000" \
    -e API_ORIGIN=http://api:8080 "$web_image" >/dev/null

  wait_for "api /readyz" 60 curl -fsS "http://127.0.0.1:${api_port}/readyz"
  wait_for "web /healthz" 60 curl -fsS "http://127.0.0.1:${web_port}/healthz"
  wait_for "fake identity provider" 30 curl -fsS "http://127.0.0.1:${idp_port}/jwks"

  # Seed the synthetic demo workspace (two tenants). The password is generated here, held in a
  # mode-0600 file for the Playwright step, and never printed.
  if [[ ! -s "$state/demo_password" ]]; then
    (umask 077; openssl rand -hex 16 > "$state/demo_password")
  fi
  APP_ENV=dev DEMO_PASSWORD="$(cat "$state/demo_password")" \
    MIGRATOR_DATABASE_URL="postgresql://postgres:dev@127.0.0.1:${db_port}/ai" \
    uv run --frozen --project "$root" python "$root/scripts/seed_replica_demo.py"
  printf 'stack up: api :%s  web :%s  fake-idp :%s\n' "$api_port" "$web_port" "$idp_port"
}

psql_value() { docker exec "${stack}-db" psql -U postgres -d ai -tA -c "$1"; }

mint() { # subject tenant -> token (role analyst: the seeded members' stored role) (synthetic; printed to stdout only)
  curl -fsS -X POST "http://127.0.0.1:${idp_port}/token" -H 'Content-Type: application/json' \
    -d "{\"sub\":\"$1\",\"tenant_id\":\"$2\",\"role\":\"analyst\",\"mfa\":true}" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'
}

check() {
  local meridian northline run_b
  meridian="$(psql_value "select id from tenants where name = 'Meridian Health Partners'")"
  northline="$(psql_value "select id from tenants where name = 'Northline Group'")"
  [[ -n "$meridian" && -n "$northline" ]] || { echo "seed tenants missing" >&2; return 1; }

  echo "== hermetic research run (fake model, hermetic providers)"
  python3 "$root/docker/ci/hermetic_run.py" --api "http://127.0.0.1:${api_port}" \
    --idp "http://127.0.0.1:${idp_port}" --tenant "$meridian" --sub priya-nair

  echo "== cross-tenant isolation probe (read-only)"
  run_b="$(psql_value "select id from account_runs where tenant_id = '$northline' limit 1")"
  [[ -n "$run_b" ]] || { echo "no run seeded for the second tenant" >&2; return 1; }
  TOKEN_A="$(mint priya-nair "$meridian")" TOKEN_B="$(mint lm-northline "$northline")" \
    python3 "$root/docker/ci/probe_isolation.py" --api "http://127.0.0.1:${api_port}" \
    --token-a TOKEN_A --token-b TOKEN_B --resource-b "/v1/runs/${run_b}"
}

down() {
  local c
  for c in web api objectstore worker fake-idp db migrate; do
    docker rm -f "${stack}-${c}" >/dev/null 2>&1 || true
  done
  docker network rm "$net" >/dev/null 2>&1 || true
  docker volume rm "${stack}-payloads" >/dev/null 2>&1 || true
}

on_up_exit() { # show the api log when start-up failed
  local status=$?
  if [[ $status -ne 0 ]]; then docker logs --tail 40 "${stack}-api" >&2 || true; fi
}

case "${1:-}" in
  up) trap on_up_exit EXIT; up ;;
  check) check ;;
  down) down ;;
  *) printf 'usage: %s up | check | down\n' "$0" >&2; exit 2 ;;
esac
