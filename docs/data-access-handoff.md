# Data access rebuild: hand-off (2026-10-17, second update)

Status of the brief "Rebuild ingestion, indexing and retrieval in `account_intelligence`".
Everything below is **uncommitted** in `/home/sakib/ABM Generator` on `feat/final-product`; do not
discard it, build on it. Do not commit until the owner says "commit".

## Done (uncommitted)

- A/B1 raw file store: content-addressed `raw_files` registry (migration 0078, one object per
  (tenant, sha256), superseded versions kept, first-registered-by link with a column-list
  composite FK delete action); documents point at `tenants/<id>/raw/<sha>` object keys.
- A/B2 shape detection: `documents/shapes.py` (`tabular | transcript | prose` by content; VTT/SRT
  cue normalisation; one logged `documents.shape` decision per member, identifiers and kind
  only); the chunker routes by shape, not file name.
- A/B3 table typing: per-column tallies with typed values (numbers, ISO and unambiguous EU/US
  dates, booleans, text), `type_conflicts`, multi-valued columns, `max_table_rows` fail-closed,
  payload columns kept out of the embedded first part; `TableIngestTruncated` instead of silent
  truncation.
- A/B4/B5 passage metadata: `{member, kind, heading_path, page/slide/section}` plus
  `ingested_at` stamped at write time; transcripts carry `{speakers, start_s, end_s}`; the token
  budget is calibrated once per file from its own first text
  (`account_intelligence/rag/tokenizer.py`, shared with retrieval's regex tokenizer) and drives
  both the token accounting and the character budgets, deterministically.
- A/B6: table store + passages metadata indexed (0077/0078 carry the indexes).
- `rag/retrieval.py`: `Predicate` with the 11 typed ops, `metadata_filter`, `filtered_set` (whole
  set or `FilterTooWide`), `dedupe` on a business key, `lexical_overlap` query coverage;
  predicates skip `drop_below`/`select_top_k` in `rag/graph.py`.
- Part C tools 1–7: `documents/sources.py` (`list_sources`, `search_passages`, `read_passage`,
  `read_span`, `read_rows`), `documents/tables.py` (`describe_table`, `query_table`), and
  `documents/run_python.py` + `run_child.py` (sandboxed: no network, 60 s, per-tenant flag, code
  hash logged). Tool schemas carry `additionalProperties: false`.
- Part E: `replica/router.py` (tabular | prose | mixed), `replica/tools.py` (tool loop at
  temperature 0, `max_tool_iterations`), wired into `api/routes/ask.py`; the answering model is
  Kimi K3 through `llm/adapters/moonshot_api.py` with OpenAI-style tool calls.
- Commands: `python -m account_intelligence.worker reingest-tables [--workspace T]` and
  `reindex [--workspace T]` (`documents/reindex.py`).
- Tests: shapes, passage metadata, cell typing, tokenizer calibration, table store DB suite,
  reingest/reindex passes, single-write-path ownership (raw_files registered).
- Acceptance test 1 (golden Ask): `tests/documents/test_tables_golden.py` — a scripted tabular
  model answers the golden GSK question byte-identically across 5 runs.
- Acceptance test 13 (redaction never leaks):
  `test_a_seeded_pii_cell_never_reaches_the_store_index_or_tools` in
  `tests/documents/test_tables_db.py` — the strict-tier seed appears only as its placeholder in
  the table store, passages+metadata, the private index, the embedder's input and the table
  tools' results; the raw bytes stay the only truth.
- Part G docs: `docs/data-access.md` (principles, shapes, the eight tools with schemas, routing
  and exposure, the reindex runbook); `docs/tabular-queries.md` no longer claims Moonshot has no
  tool support and shows `--workspace`.
- Optional draft `documents/access_config.py` + `config/data_access.yaml`: **not adopted** —
  everything it would carry already has a home (`max_table_rows: 500000` in
  `config/research_input_modes.yaml`, the router word lists in `replica/router.py`, the harness
  knobs `MAX_TOOL_ROUNDS = 12` and temperature 0 in `chat/service.py` / the call sites).

## Missing against the brief

- The PR description mapping root causes to commits (nothing is committed yet; the mapping lands
  with the commits when the owner says "commit").

## Drafts from the desktop session (not in the repo)

`/home/sakib/abm-handoff-drafts/`: `tokenizer.py` and `shapes.py` adopted; `access_config.py` +
`config/data_access.yaml` deliberately not adopted (redundant with the existing configuration,
see above).

## House rules that bit today

Stage explicit paths only (never a directory); never `ruff format .` repo-wide; the local test
database is the docker Postgres on 55440; one pytest session per test DB; run static checks with
`make static` (every module needs a docstring, every `while` a `# LOOP(...)` comment, every
`GUARANTEE(test_x)` tag a real test; the bandit findings — 12 Low + 2 Medium — are all in
committed files and predate this work; the full offline suite needs `-m "not requires_postgres
and not eval"`: the eval-gate golden-set test is blocked by design until analyst-reviewed golden
accounts exist).
