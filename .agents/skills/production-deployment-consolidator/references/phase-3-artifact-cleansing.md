# Phase 3 reference: artifact cleansing and comment standards

## Deletion classes (default: documentation-preserving)

| Class | Examples | Action |
| --- | --- | --- |
| Chat/AI artifacts | chat logs, pasted prompt transcripts, BUILD_LOG/AGENT_LOG/STATUS files maintained as narration | delete (`git rm` if tracked) |
| Scratch scaffolding | `_review/`, `_scratch/`, agent tool state (`.kilo/`, `.freebuff/`, `.hypothesis/`, `mutants/`), nested repo or worktree copies (`*.worktrees/`) | delete; add to `.gitignore` so they cannot return |
| Caches | `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/`, `.coverage`, `__pycache__/` | delete locally; ensure gitignored |
| Dead scaffolding | placeholder branches ("Your descriptive commit message here"), unused boilerplate modules, commented-out code blocks, debug prints | delete |
| **Preserved** | README, SECURITY, CONTRIBUTING, `docs/` (ADRs, runbooks, architecture), licenses, CI workflows, operational config | **keep** |

Rule of thumb: delete what narrates the *process* (logs of agent sessions, review scratch), keep what documents the *system* (decisions, operations, contracts). When a file mixes both (a STATUS file with open questions), extract the open questions into the tracker/docs file that outlives the log, then delete the log.

Use `git rm` for tracked files so the purge lands as one reviewable commit. Untracked directories can be removed directly. After deleting, sweep for now-dangling references (CI scripts, Makefiles, docs links) and fix them in the same commit.

## Inline comment standard

Behavior-preserving only — this phase never changes logic. Sweep every source file for:

1. **Conversational traces** — "here is the code", "I asked the model to…", "updated as requested". Delete outright.
2. **Redundant narration** — comments that restate the next line (`# increment i`). Delete; the code says it.
3. **Vague stubs** — `# function to check user`. Replace with a real docstring or delete if the signature is self-documenting.
4. **Strategic intent** — workarounds, invariants, non-obvious tradeoffs ("retry owner is fixed per run so a crashed worker cannot double-charge"). Keep, and tighten to explain **why**.

Documentation gates to satisfy (Python here; JSDoc for TS): every public module, class, function and method has a docstring covering purpose, args, returns and raised errors — PEP 257 shape, enforceable with `interrogate --fail-under 100` plus type checks via mypy strict.

## Exit check for the phase

- No file outside `docs/` and the README/SECURITY/CONTRIBUTING set mentions the build conversation.
- `git status` clean of untracked scaffolding; `.gitignore` covers every regenerated artifact class.
- Lint, docstring gate, typecheck and tests all green after the purge — cleansing must be invisible to behavior.
