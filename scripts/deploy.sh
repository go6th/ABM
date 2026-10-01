#!/usr/bin/env bash
# One-command deploy: make deploy-staging (or scripts/deploy.sh <dev|staging|production>).
#
# Steps: preflight -> quality gate -> image build -> terraform plan -> apply -> worker rollout.
# DRY_RUN=1 stops after the plan. Secrets come from environment variables only; nothing is
# read from files in the repository.
set -euo pipefail

ENVIRONMENT="${1:-}"
DRY_RUN="${DRY_RUN:-0}"
SKIP_CHECKS="${SKIP_CHECKS:-0}"
TERRAFORM="${TERRAFORM:-terraform}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="$ROOT/infra/terraform/envs/$ENVIRONMENT"

fail() { echo "deploy: $*" >&2; exit 2; }
step() { echo "==> $*"; }

case "$ENVIRONMENT" in
  dev|staging|production) ;;
  *) fail "usage: scripts/deploy.sh <dev|staging|production>" ;;
esac

# ---- preflight ---------------------------------------------------------------------------
step "preflight ($ENVIRONMENT, DRY_RUN=$DRY_RUN)"
command -v "$TERRAFORM" >/dev/null 2>&1 || fail "terraform not found (set TERRAFORM=/path/to/terraform)"
[[ -d "$TF_DIR" ]] || fail "no terraform root at $TF_DIR"

GIT_SHA="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
if [[ "$DRY_RUN" != "1" ]]; then
  # A real deploy needs a clean tree so the image tag names exactly what ships.
  [[ -z "$(git -C "$ROOT" status --porcelain)" ]] || fail "working tree is dirty; commit first"
  # Required for a real deploy. Values are injected by the CI secret store or the operator shell.
  missing=()
  for var in IMAGE_REGISTRY DEPLOY_CREDENTIALS_REF; do
    [[ -n "${!var:-}" ]] || missing+=("$var")
  done
  [[ ${#missing[@]} -eq 0 ]] || fail "missing environment variables: ${missing[*]}"
fi
IMAGE="${IMAGE_REGISTRY:-local}/account-intelligence:${GIT_SHA}"

# ---- quality gate ------------------------------------------------------------------------
if [[ "$SKIP_CHECKS" != "1" ]]; then
  step "quality gate (make app-check)"
  make -C "$ROOT" app-check
fi

# ---- image -------------------------------------------------------------------------------
step "image $IMAGE"
if docker info >/dev/null 2>&1; then
  docker build -f "$ROOT/docker/app.Dockerfile" -t "$IMAGE" "$ROOT"
  if [[ "$DRY_RUN" != "1" ]]; then docker push "$IMAGE"; fi
elif [[ "$DRY_RUN" == "1" ]]; then
  echo "    docker daemon not reachable; skipping image build in dry run"
else
  fail "docker daemon not reachable; cannot build $IMAGE"
fi

# ---- infrastructure ----------------------------------------------------------------------
step "terraform plan"
"$TERRAFORM" -chdir="$TF_DIR" init -input=false -backend=false >/dev/null
PLAN="$TF_DIR/$ENVIRONMENT.tfplan"
"$TERRAFORM" -chdir="$TF_DIR" plan -input=false -out="$PLAN"

if [[ "$DRY_RUN" == "1" ]]; then
  step "dry run complete; nothing applied"
  exit 0
fi

CLOUD="$("$TERRAFORM" -chdir="$TF_DIR" console <<<'var.cloud_provider' | tr -d '"')"
[[ "$CLOUD" != "unset" ]] || fail "cloud_provider is unset in $TF_DIR/terraform.tfvars (open decision, roadmap s16)"

step "terraform apply"
"$TERRAFORM" -chdir="$TF_DIR" apply -input=false "$PLAN"

# ---- rollout -----------------------------------------------------------------------------
step "worker rollout"
# The rollout command depends on the chosen container platform (roadmap s12). It is added with
# the cloud module; until then a real deploy stops here rather than pretending to succeed.
fail "worker rollout for cloud '$CLOUD' is not implemented yet; see infra/README.md"
