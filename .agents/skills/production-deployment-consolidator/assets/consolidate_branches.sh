#!/usr/bin/env bash
# Safe branch consolidation for a repository.
#
# Classifies every local branch against a target branch and deletes only the ones whose
# full patch content is verifiably present on the target:
#   - merged        : tip is an ancestor of target (fast-forward / merge commit)
#   - squash-merged : a synthetic single commit carrying the branch's tree (parented at the
#                     merge-base) is patch-id-equivalent to work already on the target.
#                     `git cherry` prints '+' for patches NOT on the target; a branch is
#                     contained only when NO line starts with '+'. (The naive inverse of
#                     this check is what silently deletes unmerged work.)
#   - open          : anything else. Never deleted; reported with its unique patch count.
#
# Usage:
#   bash consolidate_branches.sh [target] [--apply]
# Default target: main. Default mode: dry run (prints the plan, deletes nothing).
set -euo pipefail

TARGET="main"
APPLY=0
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --dry-run) APPLY=0 ;;
    -*) echo "unknown flag: $arg" >&2; exit 2 ;;
    *) TARGET="$arg" ;;
  esac
done

PROTECTED_BRANCHES=("main" "master" "develop" "staging" "production")

git fetch --prune origin 2>/dev/null || echo "warn: git fetch failed; continuing with local state" >&2

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$CURRENT_BRANCH" != "$TARGET" ]; then
  git checkout -q "$TARGET" || { echo "error: cannot check out $TARGET" >&2; exit 1; }
fi
git pull -q --ff-only origin "$TARGET" 2>/dev/null || echo "warn: could not fast-forward $TARGET from origin; continuing" >&2

# Branches checked out in any worktree (including this one) cannot be deleted.
WORKTREE_BRANCHES="$(git worktree list --porcelain | awk '/^branch /{sub("refs/heads/","",$2); print $2}')"

is_protected() {
  local b="$1"
  [ "$b" = "$TARGET" ] && return 0
  printf '%s\n' "${PROTECTED_BRANCHES[@]}" | grep -qx "$b"
}

is_in_worktree() {
  printf '%s\n' "$WORKTREE_BRANCHES" | grep -qx "$1"
}

MERGED_BRANCHES="$(git branch --merged "$TARGET" --format='%(refname:short)' || true)"
DELETE_MERGED=()
DELETE_SQUASHED=()
OPEN_BRANCHES=()

while IFS= read -r branch; do
  [ -z "$branch" ] && continue
  is_protected "$branch" && continue
  is_in_worktree "$branch" && { echo "keep (checked out in a worktree): $branch"; continue; }

  if printf '%s\n' "$MERGED_BRANCHES" | grep -qx "$branch"; then
    DELETE_MERGED+=("$branch")
    continue
  fi

  MERGE_BASE="$(git merge-base "$TARGET" "$branch" 2>/dev/null || true)"
  if [ -z "$MERGE_BASE" ]; then
    echo "keep (unrelated history, needs manual review): $branch"
    OPEN_BRANCHES+=("$branch")
    continue
  fi

  SYNTHETIC="$(git commit-tree "$(git rev-parse "$branch^{tree}")" -p "$MERGE_BASE" -m "squash probe for $branch")"
  if [ -z "$(git cherry "$TARGET" "$SYNTHETIC" | grep '^+' || true)" ]; then
    DELETE_SQUASHED+=("$branch")
  else
    UNIQUE="$(git cherry "$TARGET" "$branch" | grep -c '^+' || true)"
    echo "keep (open, $UNIQUE unique patch(es)): $branch"
    OPEN_BRANCHES+=("$branch")
  fi
done < <(git for-each-ref refs/heads/ --format='%(refname:short)')

echo
echo "plan: delete ${#DELETE_MERGED[@]} merged, ${#DELETE_SQUASHED[@]} squash-merged; keep ${#OPEN_BRANCHES[@]} open"
for b in "${DELETE_MERGED[@]:-}"; do [ -n "$b" ] && echo "  merged        -> $b"; done
for b in "${DELETE_SQUASHED[@]:-}"; do [ -n "$b" ] && echo "  squash-merged -> $b"; done

if [ "$APPLY" -eq 0 ]; then
  echo "dry run: rerun with --apply to delete the listed branches"
  exit 0
fi

for branch in "${DELETE_MERGED[@]:-}"; do
  [ -z "$branch" ] && continue
  git branch -d "$branch"
done

for branch in "${DELETE_SQUASHED[@]:-}"; do
  [ -z "$branch" ] && continue
  # Re-verify immediately before the forced delete; content may have changed since the scan.
  MERGE_BASE="$(git merge-base "$TARGET" "$branch")"
  SYNTHETIC="$(git commit-tree "$(git rev-parse "$branch^{tree}")" -p "$MERGE_BASE" -m "squash probe for $branch")"
  if [ -z "$(git cherry "$TARGET" "$SYNTHETIC" | grep '^+' || true)" ]; then
    git branch -D "$branch"
  else
    echo "refused to delete (content changed during run): $branch" >&2
  fi
done
