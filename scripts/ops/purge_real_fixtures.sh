#!/usr/bin/env bash
# Remove real customer data from ALL git history (fix prompt A, task 2; ADR-0107).
#
#   scripts/ops/purge_real_fixtures.sh                    # dry run (default): list, change nothing
#   scripts/ops/purge_real_fixtures.sh --dry-run          # same
#   scripts/ops/purge_real_fixtures.sh --execute --repo /path/to/ABM-purge.git --backup-dir DIR
#
# The dry run works in any clone and only reads: it lists every commit that touches a purge path,
# the branches (local and remote) that contain them, and the blob sizes.
#
# --execute rewrites history with git filter-repo. It refuses to run anywhere but a BARE MIRROR
# clone (git clone --mirror), so the shared working folder, its linked worktrees and the local
# unpushed commits are never rewritten in place. Before rewriting it saves a bundle of the mirror
# and a copy of every purged file into --backup-dir (outside the repository, mode 700). It never
# pushes: the force push and the re-clone are manual steps in docs/runbooks/history-rewrite.md.
set -euo pipefail

# Every path that ever held a real account's data. Paths, not globs: filter-repo --invert-paths
# removes exactly these (a directory path removes everything under it).
PURGE_PATHS=(
  "tests/fixtures/gsk/"
  "tests/documents/test_tables_real_gsk.py"
  "tests/evals/test_tabular_gsk_live.py"
  "evals/tabular_gsk/"
  "web/shots/structured-answer-1440.png"
  "web/shots/structured-answer-390.png"
  "web/shots/structured-preview.html"
  "unwanted-things/real-account-fixtures/"
  "unwanted-things/backup-2026-11-01-before-reset/"
)

mode="dry-run"
repo="."
backup_dir=""

usage() { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) mode="dry-run" ;;
    --execute) mode="execute" ;;
    --repo) repo="${2:?--repo needs a path}"; shift ;;
    --backup-dir) backup_dir="${2:?--backup-dir needs a path}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

g() { git -C "$repo" "$@"; }

g rev-parse --git-dir >/dev/null 2>&1 || { echo "not a git repository: $repo" >&2; exit 2; }

list_affected() {
  echo "== commits touching the purge paths (all refs)"
  local commits
  commits="$(g log --all --format='%H' -- "${PURGE_PATHS[@]}" | sort -u)"
  if [[ -z "$commits" ]]; then
    echo "none: history is already clean"
    return 1
  fi
  g log --all --date=short --format='%h %ad %s' -- "${PURGE_PATHS[@]}"
  echo
  echo "== refs that contain at least one of them"
  local refs=""
  while read -r c; do
    refs+="$(g for-each-ref --contains "$c" --format='%(refname:short)')"$'\n'
  done <<<"$commits"
  sort -u <<<"$refs" | sed '/^$/d'
  echo
  echo "== blobs per path (every version ever committed)"
  local p
  for p in "${PURGE_PATHS[@]}"; do
    local n
    n="$(g log --all --format='%H' -- "$p" | sort -u | wc -l)"
    [[ "$n" -gt 0 ]] && printf '%-60s %s commits\n' "$p" "$n"
  done
  return 0
}

if [[ "$mode" == "dry-run" ]]; then
  echo "DRY RUN in $(cd "$repo" && pwd): nothing will be changed"
  echo
  list_affected || true
  echo
  echo "To rewrite: follow docs/runbooks/history-rewrite.md (mirror clone, then --execute)."
  exit 0
fi

# ---- execute ---------------------------------------------------------------------------------
command -v git-filter-repo >/dev/null 2>&1 || git filter-repo --version >/dev/null 2>&1 || {
  echo "git filter-repo is not installed (pip install --user git-filter-repo)" >&2
  exit 3
}
[[ "$(g rev-parse --is-bare-repository)" == "true" ]] || {
  echo "refused: --execute runs only in a bare mirror clone (git clone --mirror <url> X.git)" >&2
  exit 4
}
[[ -n "$backup_dir" ]] || { echo "refused: --execute needs --backup-dir outside the repo" >&2; exit 4; }
repo_abs="$(cd "$repo" && pwd)"
mkdir -p "$backup_dir"
backup_abs="$(cd "$backup_dir" && pwd)"
case "$backup_abs/" in
  "$repo_abs"/*) echo "refused: --backup-dir must be outside the repository" >&2; exit 4 ;;
esac
chmod 700 "$backup_abs"

list_affected || { echo "nothing to purge"; exit 0; }

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
echo "== saving a bundle of every ref to $backup_abs/before-purge-$stamp.bundle"
g bundle create "$backup_abs/before-purge-$stamp.bundle" --all
echo "== saving the purged files as they are on each ref tip"
for ref in $(g for-each-ref --format='%(refname)' refs/heads refs/tags); do
  out="$backup_abs/files-$stamp/${ref//\//_}"
  for p in "${PURGE_PATHS[@]}"; do
    if g cat-file -e "$ref:${p%/}" 2>/dev/null; then
      mkdir -p "$out"
      g archive --format=tar "$ref" -- "${p%/}" | tar -x -C "$out"
    fi
  done
done

paths_file="$(mktemp)"
trap 'rm -f "$paths_file"' EXIT
printf '%s\n' "${PURGE_PATHS[@]}" >"$paths_file"

echo "== rewriting history"
(cd "$repo_abs" && git filter-repo --force --invert-paths --paths-from-file "$paths_file")

echo "== verifying"
if [[ -n "$(g log --all --format='%H' -- "${PURGE_PATHS[@]}")" ]]; then
  echo "FAILED: a purge path is still reachable" >&2
  exit 5
fi
echo "history is clean. Next: docs/runbooks/history-rewrite.md step 5 (force push)."
