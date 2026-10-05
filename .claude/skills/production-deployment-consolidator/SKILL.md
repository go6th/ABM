---
name: production-deployment-consolidator
description: Consolidate a repository to deployment-ready state. Triggers when the user asks for a full project validation, 100% production-grade evaluation, mutation testing, branch merging/cleanup, or the removal of AI/conversational artifacts. Merges every branch safely (including squash merges), enforces mutation thresholds, purges scaffolding while preserving documentation, and standardizes comments.
---

# Production-grade consolidation and validation

Treat the repository as a compiler target, not a conversation. Produce merges, diffs and test evidence; never prose filler. Work the three phases in order and stop to ask the user only when a phase would destroy content the checks cannot verify.

## Non-negotiable rules

1. **Never delete a branch whose patch content is not verifiably present on the target.** Ancestry (`git branch --merged`) is necessary but not sufficient: squash merges hide content from ancestry checks. Verify with the synthetic-commit probe in `assets/consolidate_branches.sh`. If the probe errors or histories are unrelated, skip the branch and report it.
2. **A branch checked out in a worktree cannot be deleted.** Prune or remove worktrees first (`git worktree list --porcelain`), and treat uncommitted changes in a worktree as content to inspect, not to discard.
3. **Documentation is content, not artifact.** ADRs, runbooks, module maps, README, SECURITY, CONTRIBUTING are preserved by default. Purge only: chat logs, build logs, AI session output, scratch review folders, tool state, caches, nested repo/worktree copies, and inline conversational comments. If the user demands literal "delete all .md", archive them in a `archive/docs` branch first and say so.
4. **Coverage is not evidence.** Only a mutation score counts. Set the break threshold to 80% minimum (the project's own config may be stricter); report the score per module, and list surviving mutants by file:line.
5. **Output discipline.** Report phase results as: what changed (exact commands/diffs), what is proven (test/mutation evidence), what remains open (WIP branches, equivalent mutants, skipped checks). No acknowledgements, no summaries of the obvious.

## Phase 1: repository consolidation

Run `bash assets/consolidate_branches.sh <target> --dry-run` first and show the plan. Then rerun with `--apply`. The script:

- fetches with prune, checks out the target, fast-forwards it;
- classifies every local branch as **merged** (ancestry), **squash-merged** (synthetic `commit-tree` probe compared with `git cherry`; a branch is contained when cherry prints **no `+` lines** — `+` means a patch is *not* on the target), or **open** (kept, reported with its unique patches);
- protects `main`, `master`, `develop`, `staging`, `production` and the current branch;
- deletes only verified-contained branches, with `git branch -d` for ancestry-merged and a re-verified `git branch -D` for squash-merged.

For an integration branch that already contains other branches via real merge commits, prefer one `git merge --no-ff <integration> ` into the target over N individual merges; then the per-branch probes collapse to ancestry checks. Branches that are open (unique patches) get merged individually — resolve conflicts by content, never by force flags — or are reported as WIP with their unique patch count.

## Phase 2: mutation validation

Read `references/phase-2-mutation-validation.md` for framework configs (mutmut 3 for Python, Stryker for JS/TS) and the CI break gate. Workflow:

1. Run the existing suite first; a red suite invalidates any mutation run.
2. Run the mutation framework scoped to critical paths (pure rule modules, boundaries, money, counts).
3. For each survivor: state the hypothesis (why the suite missed it), write a targeted test that kills it, rerun. Iterate until the score is ≥80% or survivors are proven equivalent — equivalence claims must cite why no input distinguishes the mutant.
4. Report: score per module, survivors killed, survivors left with reasons.

## Phase 3: artifact cleansing and comment standard

Read `references/phase-3-artifact-cleansing.md` for the deletion classes and comment rules. Rules of engagement:

- AST-aware, behavior-preserving only: delete debug prints, commented-out code, conversational comments ("as requested", "here is the code"), scratch files, and dead scaffolding. Never rewrite logic in this phase.
- Every public function, class and complex block gets a docstring/comment explaining **why** (intent, invariants, workarounds), not **what**. Follow the language standard: PEP 257 + the project's docstring gate for Python, JSDoc for TS.
- Prefer `git rm` over bare `rm` for tracked files so the purge is one reviewable commit.
- After cleansing, rerun lint + tests; the purge must leave them green.

## Exit criteria

- `git branch` contains only the target and intentionally-open branches, each with a stated reason.
- Working tree has no untracked scaffolding; `.gitignore` covers the artifact classes so they cannot return.
- Test suite green; mutation score ≥80% on the scoped paths (or documented equivalent-mutant exceptions).
- One merge commit (or one per branch), one purge commit, one report — no intermediate noise.
