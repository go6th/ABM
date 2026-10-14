#!/usr/bin/env bash
# Inspect a locally built image: non-root, and none of the files an image must not carry (an env
# file, tests, source maps, dev tools). The api and worker images also pass the packaging import
# check. Read-only; starts only throwaway `docker run --rm` containers. Prints no secret.
# Usage: docker/ci/image_checks.sh <api|worker|web> <image-ref>
set -Eeuo pipefail

kind="${1:?usage: image_checks.sh <api|worker|web> <image>}"
image="${2:?usage: image_checks.sh <api|worker|web> <image>}"
fail=0
ok() { printf 'ok    %s\n' "$1"; }
bad() { printf 'FAIL  %s\n' "$1" >&2; fail=1; }

user="$(docker inspect --format '{{.Config.User}}' "$image")"
case "$user" in
  "" | 0 | root | 0:* | root:*) bad "image runs as root (User='$user')" ;;
  *) ok "runs as non-root user ${user}" ;;
esac
runtime_uid="$(docker run --rm --entrypoint id "$image" -u)"
if [[ "$runtime_uid" != "0" ]]; then ok "container uid is ${runtime_uid}"; else bad "container uid is 0"; fi

# One listing of the image file system, searched below (no shell needed in the image's own PATH
# beyond `find`, which the Debian base images ship).
# (listed as root so that no directory is hidden by its permissions)
listing="$(docker run --rm --user 0 --entrypoint find "$image" / -xdev \
  \( -path /proc -o -path /sys -o -path /dev \) -prune -o -print 2>/dev/null || true)"
[[ -n "$listing" ]] || { echo "could not list the image file system" >&2; exit 2; }

absent() { # description, extended regex over full paths
  local hits
  hits="$(grep -E "$2" <<<"$listing" || true)"
  if [[ -z "$hits" ]]; then ok "no $1"; else bad "$1 present:"; head -5 <<<"$hits" >&2; fi
}
absent "env files" '/\.env($|\.)'
absent "credential key files" '/(gha-creds-[^/]*\.json|[^/]*\.pem|id_rsa|id_ed25519)$'
absent "git metadata" '/\.git($|/)'
absent "source maps (*.map)" '\.map$'

case "$kind" in
  api | worker)
    # Application tests and fixtures; third-party packages keep their own `tests` directories
    # inside site-packages, so only the application's own locations are checked.
    absent "tests directory at /app" '^/app/(tests?|docs|evals|ops|scripts)(/|$)'
    absent "pytest/ruff/mypy/bandit/vulture in the virtualenv" \
      '^/app/\.venv/lib/python[0-9.]+/site-packages/(pytest|_pytest|ruff|mypy|mypyc|bandit|vulture|coverage|hypothesis|interrogate)(/|-)'
    absent "dev executables in the virtualenv" '^/app/\.venv/bin/(pytest|ruff|mypy|bandit|vulture|coverage|lint-imports)$'
    absent "build tools (uv, uvx) in the runtime stage" '^/(bin|usr/bin|usr/local/bin)/(uv|uvx)$'
    absent "application source tree (the wheel is installed instead)" '^/app/src(/|$)'
    # Packaging import check (every package under src/ is in the wheel list in pyproject.toml).
    if docker run --rm --entrypoint python "$image" -I -c \
      "import documents.sandbox_child, research_files.blobstore, devstore.server" 2>/dev/null; then
      ok "packaging import check (documents.sandbox_child, research_files.blobstore, devstore.server)"
    else
      bad "packaging import check failed"
    fi
    ;;
  web)
    absent "node_modules dev dependencies (typescript, eslint, vitest, playwright)" \
      '/node_modules/(typescript|eslint|vitest|@playwright|playwright|playwright-core|@axe-core)(/|$)'
    absent "web sources and tests" '^/app/(e2e|tests|app|components|lib|ui|scripts)(/|$)'
    absent "npm, npx, corepack" '^/usr/local/(bin/(npm|npx|corepack)|lib/node_modules/(npm|corepack))(/|$)'
    ;;
  *) printf 'unknown image kind %s\n' "$kind" >&2; exit 2 ;;
esac

exit "$fail"
