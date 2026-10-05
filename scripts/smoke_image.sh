#!/usr/bin/env bash
# Smoke test of the built image against the local database: runs as a non-root user, /readyz
# answers 200 (fails closed otherwise), and SIGTERM ends the process cleanly (exit 0).
set -euo pipefail
name=ai-smoke-$$
docker run -d --name "$name" --network host -e APP_ENV=test \
  -e DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://app_rw:local-dev-only@localhost:5432/ai}" \
  -e CHECKPOINT_DATABASE_URL="${CHECKPOINT_DATABASE_URL:-postgresql://app_rw:local-dev-only@localhost:5432/ai}" \
  -e JWKS_URL=https://issuer.invalid/jwks -e JWT_AUDIENCE=aud -e JWT_ISSUER=iss \
  account-intelligence:local >/dev/null
trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT
for _ in $(seq 1 30); do curl -fsS localhost:8080/healthz >/dev/null 2>&1 && break; sleep 1; done
test "$(docker exec "$name" id -u)" != "0"
test "$(curl -s -o /dev/null -w '%{http_code}' localhost:8080/readyz)" = "200"
docker stop -t 30 "$name" >/dev/null
test "$(docker inspect "$name" --format '{{.State.ExitCode}}')" = "0"
echo "smoke OK: non-root, ready, graceful SIGTERM"
