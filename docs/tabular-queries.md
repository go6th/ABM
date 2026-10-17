# Tabular questions: tables are queried, not retrieved

*2026-10-17. Owner-facing summary first, then the mechanics.*

## What changed, in plain terms

When you ask a question that is really a filter, count or lookup over one of your uploaded
spreadsheets ("list all open Salesforce-related jobs first seen on or after March 1, 2026"),
the model no longer reads ten retrieved passages and guesses. It is given two tools over the
spreadsheet itself: `describe_table` (what columns the sheet has, their types, a few sample
values, how many rows) and `query_table` (filter, project, sort, deduplicate; always the
complete set of matching rows, never a sample). The answer is computed, cited by row number,
and the same every time: the tool calls are deterministic and the model runs at temperature 0.

Files uploaded **before** this change have their rows indexed as passages only. Run
`reingest-tables` once (below) so their tables appear in the store; until then such a question
still answers the old way.

## Routing rules (`account_intelligence/replica/router.py`)

Before anything is retrieved, each Ask question gets a route and a reason (logged as
`ask.route`):

| Route | When | What the model gets |
|---|---|---|
| `prose` | the workspace has no ingested table; or the question has no filter word and no column name and the fallback says prose | retrieval and the whole file, as before |
| `tabular` | a filter word (list, how many, count, which, all, every, where, with, after, before, since, between, on or after, open, closed, first seen, last seen, unique, duplicate, sorted...) **or** a column name of one of the tables appears in the question, and no prose word does | `describe_table` + `query_table` only; no passages; temperature 0 |
| `mixed` | filter words or column names **and** prose words (why, explain, summarise, describe, tell me about, what does, overview, recommend, strategy...); or no rule fires and the fallback cannot decide | tools and retrieval both |

The fallback classification (no rule fired, tables exist) is one short, non-streamed call to the
conversation's Claude model at temperature 0 whose reply must be exactly `{"route": ...}`
(validated against a JSON schema; anything else counts as no answer). `ASK_ROUTE_LLM_FALLBACK=off`
turns it off, in which case the turn is `mixed`.

## The tools

`describe_table({"table": "Jobs"})` returns the table's columns (`header` as written, `name` as
the predicates use it: case folded, spaces as underscores), the inferred `type` (`date`,
`number` or `text`: a column is a date or a number only when every non-empty cell is one),
whether the column is `payload`, three `samples` per column (80 characters at most), the
`business_key` and the `row_count`. The model must call it before querying a table it has not
seen in the turn (a query on an undescribed table is refused with `describe_first`).

`query_table` input schema:

```json
{"name": "query_table",
 "description": "Filter/project rows of an ingested spreadsheet or CRM export. Use for any question that is a filter, count, or lookup over tabular data. Returns complete result sets, never a top-k sample.",
 "input_schema": {"type":"object","required":["table"],"properties":{
   "table":   {"type":"string"},
   "where":   {"type":"array","items":{"type":"object","required":["column","op","value"],
               "properties":{"column":{"type":"string"},
                 "op":{"enum":["eq","neq","gt","gte","lt","lte","contains","icontains","in","is_null","not_null"]},
                 "value":{}}}},
   "columns": {"type":"array","items":{"type":"string"}},
   "order_by":{"type":"array","items":{"type":"object","properties":{"column":{"type":"string"},"desc":{"type":"boolean"}}}},
   "distinct_on":{"type":"string"},
   "limit":   {"type":"integer","minimum":1,"maximum":5000,"default":1000}}}}
```

Result: `{"table", "rows": [{"table", "row", "member", "cells": {...}}], "row_count",
"truncated", "applied_where", "deduped_on", "dedup_removed"}`. `row_count` is the number of
matching rows (after `distinct_on`); `truncated` is true only when `limit` was hit; more than
5,000 matches is the structured error `filter_too_wide` (narrow the filter), never a silent
cut. Every row carries `{table, row, member}`, so `documents.chunking.cite` cites it as it cites
the passage ("GSK_Job_Postings.xlsx, row 17 of Jobs"). Payload columns (the long description)
are returned only when named in `columns`.

Semantics: `eq`/`neq`/`in` on text are case-insensitive; dates compare as dates (ISO
`YYYY-MM-DD`), numbers as numbers; `contains`/`icontains` match a whole item of a multi-valued
cell ("Veeva, Salesforce") or a substring; `is_null` is an empty cell too. `distinct_on` keeps,
per key, the row with the latest `last_seen` (else the lowest row number); a row without the
key collapses on `title` + `location` when the table has them, else stays apart.

Safety: the SQL is built from the column whitelist of the schema and bound parameters only;
the caller's words never reach the SQL text. Unknown tables, columns, operators and badly typed
values come back as `{"error": {"code", "message", ...}}` for the model to correct. Cells were
redacted by the tenant's tier before they were stored (`Redactor.cell`), so the tools return
nothing the passages do not hold. Row level security plus the explicit account scope limit the
tables to the caller's own.

## The store (`documents/tables.py`, migration 0077)

While a document is read, every table row is written whole and typed into
`document_table_rows` (one JSON object of cells per row, keyed by document, member and table)
and the table's schema into `document_tables`. The store is Postgres, not a DuckDB or SQLite
file: on Cloud Run a per-instance file would not survive the instance, and Postgres already
carries the tenant and owner policies. The `crm_row` passages stay for citation and keyword
search; `document_passages.metadata` holds the same typed cells for metadata filters.

Chunking of a row (`documents/chunking.py`): the row's first part is the projection of its
short, filterable columns (`Column: value; ...`), which is what is embedded and keyword
indexed; a payload column goes whole into the parts after it, unsplit. Filterable columns are
never split across parts. A table with more rows than the per-part passage cap raises
`TableIngestTruncated` and fails that part (reason `parse:table_truncated`); it is never stored
in part.

### Tuning `table_payload_columns`

`config/research_input_modes.yaml`, `chunking`:

```yaml
table_payload_columns: [full_description, description, raw_text]  # normalised names
table_payload_min_chars: 1000   # any column whose median cell (first unit) is longer
```

A column is payload when its normalised name is in the list, or when the median length of its
non-empty cells over the first unit of rows (5,000 rows at most) is above the threshold. Add a
column name (case folded, spaces as underscores) to keep a long, free-text column out of the
row's embedding; lower the threshold to catch shorter prose columns. Payload columns are still
stored, still queryable, and still returned by `query_table` when named in `columns`.

### Adding a business key per table

```yaml
table_business_keys: [requisition, requisition_id, id]   # the first present wins
table_business_key:
  "*:Jobs": requisition          # a glob on the table name (member:sheet for a zip member)
  "People": linkedin_url
```

The key is recorded in the table's schema (`business_key`) and is what `describe_table`
reports; the model passes it as `distinct_on`. The rag-side `dedupe(chunks, key)` uses the
same key for chunk results.

## Migration note: `reingest-tables`

Existing workspaces need their spreadsheets read again for the tables to appear in the store.
From the worker image:

```bash
python -m account_intelligence.worker reingest-tables            # every client
python -m account_intelligence.worker reingest-tables --workspace <tenant-uuid>   # one client
```

It finds, in every client, the indexed documents that hold table rows but no table record,
clears their passages and index rows, resets their parts to pending and queues them for the
worker exactly like a new upload. Idempotent: a document already in the store, or already
being read, is skipped; a second run queues nothing more; a failure can simply be rerun. The
documents show "parsing" then "indexed" again in the file list while it runs.

## Observability

Every Ask turn logs `ask.route` with `route`, `reason`, `outcome` (model or local),
`tools_called`, `predicates` (column and operator only, never values), `row_count` (the last
query's), `relaxed_predicate` (the one column the model dropped between two queries of the same
table), `dedup_removed` and `empty_results`. Metrics (`config/telemetry/metrics.yaml`):
`ask_routes_total{route}` (the tabular share is the `tabular` series over the sum) and
`ask_table_queries_total{empty_result}` (the empty-result rate).

## Retrieval primitives (`account_intelligence/rag/retrieval.py`)

For the chunk path (the agentic RAG graph): the tokenizer strips punctuation and keeps dates
and versions whole; `metadata_filter` takes typed `Predicate`s (the old mapping still means
`eq`); `filtered_set` returns every match and raises `FilterTooWide` past 5,000; the lexical
re-ranker is query coverage, not Jaccard; `dedupe` collapses a business key; and the config
refuses a `min_score` above the smaller arm weight at startup.
