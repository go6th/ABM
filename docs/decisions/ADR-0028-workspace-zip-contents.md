# ADR-0028: workspace zip contents (D-25)

- Status: Proposed (D-25 open; raw payloads depend on D-10)
- Date: 2026-10-04
- Deciders: TODO (product owner; counsel for D-10)

## Context

The PRD lists the workspace zip as: restore note, deliverables, build scripts, raw data with
neutral names, notes; client-shareable and passing the same scans. The SaaS document says to share
graded facts and links, not raw exports. Broker terms on resale and derived data (D-10) are open.
In a service there are no "build scripts" a client could run.

## Decision

1. Members, in a fixed order: `RESTORE.md`, `MANIFEST.json`, `NOTES.md`, the five client
   deliverables under `deliverables/`, and the render-model data each file was built from under
   `data/<kind>.json` (in place of build scripts; the builder code is not shipped).
2. **Raw broker payloads are excluded by default.** The neutral-name copy path
   (`data/raw/<sha256>.json`, no provider code in any name) is built and tested but off
   (`workspace.raw_payloads.neutral_name_copy_enabled: false`) until D-10 is answered. Payloads from
   the organisation provider are never included, enabled or not (adapter rule).
3. `MANIFEST.json` lists every other member with its SHA-256 and size, and the build manifest
   (snapshot hash, theme and rules hashes, rows per file, row ids, codes cited, numbers with their
   bases, the tracker tally, the change note).
4. Safety: relative names from a strict character set, no absolute path, `..`, backslash, colon or
   symlink, unique names, member, member-size and total-size caps; the Internal Only document has
   no kind in the builder and a name check refuses it. The zip is built only when all five files
   passed, and its every member (and every docx and xlsx inside it) is scanned again.
5. `RESTORE.md` wording is a neutral placeholder (`restore_text_source: TODO`).

## Consequences

- The render data repeats the files' content (it is scanned like them).
- Evidence: `tests/exports/test_workspace.py`, `test_mutation_kills.py`,
  `test_integration_checks.py::test_the_internal_only_file_is_never_built`.
