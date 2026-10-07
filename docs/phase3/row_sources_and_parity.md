# Row sources and export parity (VW1 to VW3)

## One mapping

`exports.builder.map_snapshot` maps a run's stored content once. The same mapped objects feed:

* the Word and Excel builders (lane cells via `exports.word.compose.lane_cells`, tracker cells),
* the row sources in `views/rowsources/` (people, jobs, facts, tech, coverage), which build the
  client tables cell for cell from the same functions.

Exclusions (`tenancy.settings.ExclusionFilter`, pinned with the run's settings version) are applied
in that mapping (`exports.readers.exclusions.apply_exclusions`), so an excluded company or person
appears in no table and no file (TN5).

## Materialisation

`views.materialize` writes the rows once per run into the insert-only `view_rows` (cells as JSON,
a search document, the row's source code and URL, or the `no_source` state). Tables are read from
SQL with an allow-listed spec (`views.query`: columns, operators, sort, search, keyset cursor);
nothing from the request is interpolated.

## Parity

`views.parity` re-reads every built xlsx (openpyxl), docx and CSV file and compares it with the API
rows for the same run; for the PDF (LibreOffice conversion of the Account Summary) the Facts by
grade and Coverage tracker row keys must each appear once and in order in its text. A mismatch
blocks the package (`export_packages.status = blocked`, reason `parity_mismatch`); a missing
converter blocks it too (`pdf_not_configured`); a blocked package cannot be downloaded. Every delivered package passes the quality gate and the client output scan.

## Source links

Every row carries its source: a linked code and a URL checked by `views.render_safe` (http and
https only), or the explicit `no_source` state shown as "No source" (VW2).

Tests: `tests/phase3/test_ws4_views.py` (hypothesis-generated accounts, every export type equals the
API rows, a mutated export is caught), `test_ws4_render_safe.py` (XSS, URL scheme, CSV and Excel
formula injection corpora).
