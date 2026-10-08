# ADR-0026: export builders: what was ported, the theme, one snapshot, reuse

- Status: Proposed (the look is a labelled proposal until the legacy files are supplied)
- Date: 2026-10-04
- Deciders: TODO (product owner)

## Context

Spec `docs/exports/files.md` records the output contract and legacy Node renderer decision; the two
openpyxl workbook scripts so they read database rows, and to keep "the look" exactly. A search of
the repository and the machine found none of the legacy files (the Node library, the people and
jobs workbook scripts, `rowcheck.py`, `doc_checks.py`, `third_party_scan.py`,
`strip_tool_names.py`) and no real delivered file set. The spec's stop rule applies: the port and
legacy parity are blocked; everything that does not depend on the look is built.

## Decision

1. **Nothing was ported, and nothing claims to be.** The Word path runs an interim python-docx
   renderer behind the D-26 contract and sandbox (ADR-0029); the workbooks are rendered with
   openpyxl from the theme. Characterization tests against the legacy output, and the legacy
   parity check, are listed as Blocked until the files arrive.
2. **The look is one file**, `config/export_theme.yaml`, every value a labelled proposal
   (`status: proposal`, `source: TODO`). Its SHA-256 is recorded on every deliverable. No renderer
   holds a style literal (static test). When the legacy files arrive, each value is replaced by
   the legacy value with its file and line as the source.
3. **Same look by construction.** Workbooks register every named style up front in a fixed order,
   and every package is normalised (fixed member order, entry time, permissions, document
   properties), so two accounts give identical style fingerprints and the same snapshot gives
   byte-identical files.
4. **One snapshot.** `tenant_txn(snapshot=True)` opens `REPEATABLE READ, READ ONLY` before any
   query; the reader, and the people and jobs repositories it reuses, all read through that one
   session. Facts and evidence are read as `public_reader`, so the database's two-output policy
   filters private rows as well as the query.
5. **One implementation per rule.** The builders reuse the grading statement policy and display
   labels, the tracker's client view and tally, the people export (selection, order, Summary
   sheet), the jobs export selection (`is_exported`, extracted from `export_rows`), the jobs and
   people counts, the jobs contact-detail scrub and the platform client-output scanner (its term
   patterns are now compiled once; behaviour unchanged).
6. **Absence claims** (Hypothesis with a search proof, no source) are listed under the hypotheses
   with their question, not in the lane tables, because a lane row must cite a source (B5).
7. **Production wiring** defaults to the Node renderer, which is `NotConfigured` until ported, so
   a production run ends PARTIAL `builders_not_configured` rather than delivering an unsourced
   look; the interim renderer is used in dev and test, and elsewhere only when an operator sets
   `EXPORT_RENDERER=python-interim`.

## Consequences

- The PDF row-presence check found the proposed 8 percent Ref column too narrow (keys wrapped at
  the hyphen); the Ref column is 10 percent. A legacy value will replace it.
- Integration gaps found by the Postgres tests (listed in `docs/open-items.md`): the jobs stage
  does not register posting links in the source registry and the people stage does not register
  post links, so a production build fails B4 until they do; distill does not write
  `report_sections` yet; grading does not write `facts.lane` yet.
- Evidence: `tests/exports/` (oracle, lying content per B-rule, properties, render and same-look,
  integration with LibreOffice, Postgres end to end), `tests/isolation/test_exports_isolation.py`.
