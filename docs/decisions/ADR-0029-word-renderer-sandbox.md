# ADR-0029: Word renderer behind a JSON contract in a sandboxed subprocess (D-26)

- Status: Proposed (D-26 open)
- Date: 2026-10-04
- Deciders: TODO (product owner)

## Context

The legacy Word output came from a Node docx library. Re-implementing it in Python would change
the look; running Node inside the service brings a second runtime and a process boundary. The
library was not supplied (ADR-0026).

## Decision

1. Keep the Node renderer (proposal), run as a **subprocess behind a JSON contract**:
   `exports.word.contract.DocumentSpec` (theme, header, footer, ordered blocks) on stdin, docx
   bytes on stdout. The JSON Schema `src/exports/word/schema/document.schema.json` is generated
   from the model (a test fails when stale) so both sides validate the same contract.
2. The sandbox (`exports.word.renderer.SubprocessWordRenderer`): fixed argv, no shell; a cleared
   environment (a fixed locale only: no database URL, credentials or cloud settings, tested); its
   own process group with a timeout that kills the group; stdout and stderr read in bounded chunks
   and the process killed the moment a cap is passed; at most one re-run; output must be a zip
   holding `word/document.xml`. The caller normalises the package (ADR-0026).
3. Until the Node library is ported, an interim python-docx renderer
   (`python -I -m exports.word.render_cli`) implements the same contract in the same sandbox, so
   the whole path is exercised for real. Swapping it for `node render.js` changes one command.
4. Reversible: the builder depends on `WordRenderPort` only.

## Consequences

- Blocked until supplied: the Node library, its lockfile and audit, its own lint and tests, and the
  Node-side schema validation (`node_renderer` raises `NotConfigured` until then).
- Measured, not targets: one subprocess start per document (about 0.3 s locally); a 10,000-posting
  synthetic account builds in about 58 s with tracemalloc on (`tests/exports/test_scale.py`).
- Evidence: `tests/exports/test_render.py` (timeout, malformed, oversized, stderr flood, non-zero
  exit, contract violation, cleared environment, re-run).
