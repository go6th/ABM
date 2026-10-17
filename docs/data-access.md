# Data access: files stay whole, tables are queried, prose is chunked

*2026-10-17. How the workspace's files reach the model. Owner-facing summary first, then the
mechanics.*

## Principles

1. **Files are the source of truth.** An upload's bytes are kept whole in a content-addressed
   raw store (migration 0078: one object per client and content hash, superseded versions kept);
   everything else — passages, table rows, index entries — is rebuilt from those bytes any time
   (`reindex`, below).
2. **Route by shape, not by file name.** Every member of every file is classed `tabular`,
   `transcript` or `prose` from its content, and the decision is logged once per member
   (`documents.shape`, identifiers only — never a name).
3. **Tools over retrieval.** The model calls typed tools over the store instead of reading
   retrieved fragments and guessing.
4. **A filter question returns a set, not a ranking.** `query_table` is a complete matching set
   or the structured `filter_too_wide` error — never a silent top-k cut.
5. **Determinism.** The answering model runs at temperature 0; splitting, typing and budgets are
   pure functions of the file's bytes, so a re-read parts the file the same way.
6. **Fail closed.** A chunk that lacks a predicate's column is dropped, not passed through; an
   ambiguous date stays text instead of being guessed; a table past `max_table_rows` fails its
   part instead of storing a half; a tool error comes back to the model as a structured result.
7. **Evidence attached.** Every row carries `{table, row, member}` and every passage its
   location, so answers cite where a claim was read. Names and document text never appear in
   logs — only identifiers, kinds, counts and hashes.

## Shapes (`documents/shapes.py`)

| Shape | Detected from | Chunked as |
|---|---|---|
| `tabular` | the parsed unit holds rows | one passage per row: the slim columns as `Column: value; …`, payload columns whole in later parts; the row also lands in the table store |
| `transcript` | ≥4 speaker turns covering ≥60% of lines, WebVTT/SRT cues normalised to turns first | windows of whole turns, 60–90 s, with `{speakers, start_s, end_s}` metadata |
| `prose` | everything else | sentence-boundary passages under the token budget, with `{member, kind, heading_path, page | slide | section, ingested_at}` metadata |

The token budget is calibrated once per file from its own first text
(`account_intelligence/rag/tokenizer.py`): with no real tokenizer installed, chars-per-token is
the file's own ratio (a Japanese transcript or a German contract is not budgeted by the English
estimate), clamped to [1, 6], and the same calibration drives both the token accounting and the
character split widths. The calibration is a pure function of the sample, so the same file
always parts the same way.

Cells type per column (`documents/chunking.py`): numbers, ISO dates, and unambiguous EU/US
dates (`31/12/2024` → `2024-12-31`; `05/06/2024` stays text — never invent a date), booleans,
text. A column with more than one kind is `text` and listed in the schema's `type_conflicts`;
multi-valued columns are flagged. `describe_table` reports all of it.

## The tools (`documents/tables.py`, `documents/sources.py`, `documents/run_python.py`)

All schemas are closed (`additionalProperties: false`); every call opens its own tenant
transaction, is parameter-bound against the table's column whitelist, and returns errors as
`{"error": {"code", "message", …}}` for the model to correct. `list_sources` is on every route.

| Tool | Returns |
|---|---|
| `list_sources` | the workspace's files and members, each with its shape — the model learns what to query and what to read |
| `describe_table` | columns (`header`, normalised `name`, `type`, `payload`, samples), `business_key`, `row_count`; must precede any query of that table (`describe_first` otherwise) |
| `query_table` | the complete matching row set (filter/project/sort/`distinct_on`, 5,000-row cap with `filter_too_wide` past it); `distinct_on` keeps the row seen last, and a keyless row merges onto the keyed row of the same title whose location shares a real place word (a re-scrape that lost its id), else falls back to title+location |
| `read_rows` | whole rows by row number, payload columns included when asked |
| `search_passages` | hybrid keyword + vector search over passages (top-k, typed metadata filters) |
| `read_passage` | one passage whole, by chunk id |
| `read_span` | the passages between two chunk ids — a section read end to end |
| `run_python` | sandboxed Python over the turn's results (`data`): no network, read-only, 60 s, result size-capped; only when the tenant's `run_python` flag is on; every call logged by the code's sha256, never the code |

Exposure follows the route (`account_intelligence/replica/tools.py`): `tabular` offers the table
tools, `prose` the passage tools, `mixed` both; `run_python` joins `tabular` and `mixed` only
when the tenant flag is on.

## Routing and the harness (`account_intelligence/replica/router.py`)

Each Ask question is routed `tabular | prose | mixed` by filter words, column names and prose
words, with one schema-validated fallback classification at temperature 0 when no rule fires
(`ASK_ROUTE_LLM_FALLBACK=off` → `mixed`). The lexicon rides on the table record itself
(2026-10-17): ingest stores, per column, its natural forms (the header as written, lowercased
and punctuation-stripped, the normalised name with spaces, a date column's "… date" form) and
the distinct values of a low-cardinality text column (50 short values at most). So "first seen
date" names `first_seen`, and the *values* "Open", "Salesforce", "Platform owner" route the
question to the table holding them; a trailing plural `s` counts ("platform owners"). A column
or value hit routes `tabular` (`mixed` when a prose word sits alongside); a filter word with no
hit still routes `mixed` — retrieval alone would answer from a sample; prose words alone stay
`prose`. The answering model is Kimi K3 through
`llm/adapters/moonshot_api.py` (OpenAI-style tools; the key is Ollama-shaped,
`MOONSHOT_API_KEY`), at temperature 0, in a bounded tool loop; the tabular system-prompt rules
join only when table tools are on offer. The rules themselves refuse a dead end: a tabular
answer may never ask the user to re-export the file or claim rows are missing — Ask logs
`ask.answer_refusal` when one slips through, and the live eval fails on the same phrases.
Every turn logs `ask.route` (route, reason, matched columns and value terms, tools
called, predicates as `column:op` only, row count, relaxed predicate, dedup removals, empty
results, searches, sandbox runs); metrics: `ask_routes_total{route}`,
`ask_table_queries_total{empty_result}`, `ask_run_python_total{sandbox_outcome}`. Routing details and the
business-key and payload tuning live in `docs/tabular-queries.md`.

### The live tabular eval (Gap 2/3)

`evals/tabular_gsk/cases.yaml` holds five owner questions over the **real** GSK workspace
fixture (`tests/fixtures/gsk/GSK_workspace.zip`); `tests/evals/test_tabular_gsk_live.py`
(marker `live`, never in the offline suite; the offline selectors exclude `live`) ingests it
through the production path and asks each question of the real model five times. The eval's
model comes from the one layer every surface reads (owner decision 2026-10-17): the
`surfaces.eval` entry of `config/models.yaml` — default `kimi-k3` at effort `high` —
overridable per run with `EVAL_MODEL` / `EVAL_EFFORT` to any choices model (Kimi K3, Claude
Fable 5.1, Claude Opus 5.5). Kimi K3 runs at temperature 0 with the seed pinned; a Claude
model's Messages call has no temperature or seed parameter, so there the per-run answer
assertions are the hard gate and convergence the tolerated signal. The five canonical
tool-call traces must converge, at least three of the five identical (a batched cloud model is
not bit-identical even at temperature 0 and a pinned seed) — a trace's canon judges only the
evidence calls (the filtered `query_table` calls and the `read_rows` calls on the case's named
target table; describe, other-table and where-less calls are exploration, not evidence; the
projection, the order-insensitive lists and an `icontains` value's case are folded as provably
result-preserving, and a run that never queries the target table fails outright) — and every
answer, a deviant trace's included, must pass the case's terms
(the right requisition ids named, the right counts, a deciding phrase quoted for the
field-sales false positives, and never a re-export refusal). It runs nightly and on demand in
`.github/workflows/eval.yml` with the `MOONSHOT_API_KEY` secret, and locally with the key in
`.env` (`ANTHROPIC_API_KEY` when `EVAL_MODEL` names a Claude model). The offline doubles:
`tests/documents/test_tables_real_gsk.py` pins the sheet's truth
(no model), `test_tables_golden.py` …`_with_stub_model` the byte-identical scripted answer.

## Runbook: rebuilding a workspace

The raw store makes a rebuild a re-read, not a re-upload. Two worker commands (exit 0 cleanly,
3 when the pass itself could not run):

```bash
# one client's documents that predate the table store (crm_row passages, no table record)
python -m account_intelligence.worker reingest-tables --workspace <tenant-uuid>

# every indexed document of one client, rebuilt end to end (after a pipeline change)
python -m account_intelligence.worker reindex --workspace <tenant-uuid>
```

Drop `--workspace` to run either across every client. Both list the documents in scope, clear
their passages, index rows and tables, return their parts to pending and queue them for the
worker, which reads, chunks, stores and indexes them exactly as a new upload. Both are
idempotent (`reset_for_reparse` skips anything not `indexed`), capped per client per run, and
safe to rerun after a failure. The file list shows "parsing" then "indexed" while they run.
`reingest-tables` reports per client (the `workspaces` of its result, one
`documents.reingest_tables.workspace` log line each): the documents queued, and the tables and
rows the client now holds — run it once more after the worker has drained and the report is the
rebuild's receipt.
