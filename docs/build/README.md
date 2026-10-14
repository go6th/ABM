# Build documents

How the product was specified, planned and built, in the order the owner asked for it. Where two
documents disagree, the later owner decision wins; each superseded prompt says so at its top.

## What to read first

| Document | What it is |
| --- | --- |
| `REPLICA_PROMPT.md` | The specification in force: the Claude Design prototype as a working product, screen by screen, with the owner's later decisions (sign-in pages, Ask without sample text, Phase 4B: no credits on user screens, model choice, files of any size, limits, private workspaces). |
| `FIX_LOG.md` | What was built and fixed, phase by phase, with commits and check results. |
| `../deploy/REPORT.md` | Phase 4C (production readiness on Google Cloud): audits, fixes, infrastructure, CI, results and the owner inputs still needed. |
| `../ARCHITECTURE.md` | The system as built: services, request flow, tenancy and private workspaces, where data lives. |

## Plans

| Document | Covers |
| --- | --- |
| `PLAN.md` | The final-product build plan and the owner's reversals. |
| `REPLICA_PLAN.md` | How the replica was built, in steps. |
| `PHASE_4B_PLAN.md` | Phase 4B, including why the 1 GB upload failed. |
| `../deploy/PLAN.md` | Phase 4C: what existed, what FIX_PROMPT Phases 5 to 7 still need, the agents, file ownership and assignments. |

## Earlier prompts and notes (superseded where they conflict)

| Document | Status |
| --- | --- |
| `BUILD_PROMPT.md` | Superseded where it conflicts by `REPLICA_PROMPT.md`. |
| `CHANGE_CLAUDE_ONLY_SEARCH.md` | Superseded where it conflicts by `REPLICA_PROMPT.md`. |
| `DESIGN_COPY_PROMPT.md` | Superseded where it conflicts by `REPLICA_PROMPT.md`. |
| `VISUAL_PROMPT.md`, `VISUAL_NOTES.md` | The pastel, depth and motion pass, which the owner rejected; kept as a record. |
| `UNDERSTANDING.md` | The state of the code before the final build (2026-10-13). |

FIX_PROMPT (the audit repair prompt) is kept outside this branch; Phases 5 to 7 of it are next,
when the owner says so. What they still need from the code is listed in `../deploy/PLAN.md`
section 2.
