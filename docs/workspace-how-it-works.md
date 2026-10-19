# How the workspace works, end to end

*2026-10-17. Every subsystem in full detail: the pipeline a file travels, the store it lands in,
the tool loop that answers questions, and the one model layer every surface reads. Companion
pieces: `docs/data-access.md` (the store contract), `docs/tabular-queries.md` (tabular
mechanics), `docs/architecture/loops.md` (the generated loop inventory).*

## **1. The big picture in one table**

| Stage | What happens | Where it lives |
|---|---|---|
| **Upload** | A member drops files into an account's workspace; bytes are stored verbatim, processing is queued | `documents` package, worker service |
| **Shape** | Each file (or zip member) is classified `tabular` or prose; sheets of a workbook become separate members | `documents` ingest, `documents.shape` log event |
| **Store** | Raw bytes stay the truth; every table is stored **row by row, typed**; prose and payload become searchable passages | migrations **0077** (`document_tables`, `document_table_rows`, `document_passages.metadata`), **0078** (`raw_files`) |
| **Route** | Each Ask question is classified `tabular`, `prose` or `mixed` **before** anything is retrieved | `replica/router.py`, `ask.route` log event |
| **Answer** | The model drives a bounded loop of workspace tools over Postgres, then streams an answer whose every citation is checked before it is sent | `replica/model_answer.py`, `replica/answers.py` |
| **Model** | One config layer decides which model (Kimi K3 / Fable 5.1 / Opus 5.5) and how hard it thinks, for every surface | `config/models.yaml`, `src/llm/choice.py`, `platform/llm.py` |

Everything is **tenant-private and owner-private**: the store's row-level security lets a row be
read only inside its tenant transaction and by its owner (or the system scope), so a question can
never see another workspace's data.

## **2. Ingest: from upload to the store**

An uploaded file is processed by the **worker service** (the API never parses documents inline):

1. **The raw bytes are the truth.** The file's bytes are kept unchanged (migration 0078's
   `raw_files`); everything else — passages, table rows — is derived and can be rebuilt from the
   bytes at any time. That is exactly what the `reingest-tables` admin command does: it queues a
   workspace's pre-table-store documents for re-read, and the worker then processes them exactly
   like new uploads.
2. **Shape detection decides the treatment.** A spreadsheet or CRM export with a header row is
   `tabular`; running text is prose; a zip is opened and each **member** is shaped on its own.
   The worker logs each decision as `documents.shape`, pages as `documents.indexed_page`, and a
   finished document as `documents.indexed`.
3. **Tabular members are stored as rows, not flattened to text.** For each sheet the store keeps
   (migration 0077):
   - **`document_tables`** — one row per ingested table: the columns as written plus their
     normalised names, the inferred type of each (`date` | `number` | `text`), which columns are
     **payload** (long free text such as a job description, kept out of query results and read
     only through the row's passage), the **business key** duplicate rows collapse on, and the
     row count.
   - **`document_table_rows`** — every row as **one jsonb object of typed cells**, keyed by
     `(table, row_no)` and carrying the passage sequence number it cites back to. A part that is
     read again removes its own rows first, so re-ingest is idempotent.
   - **`document_passages.metadata`** — each CRM row's cells, typed, under the normalised column
     names; the row's passage stays for citation and keyword search.
4. **Prose members are chunked into passages** (`document_passages`) with embeddings for semantic
   search; whole-file reads are available for the questions that need a document end to end.

**Why rows:** a question like *"how many open Salesforce jobs were first seen after March 1?"* is
a **filter and a count**, and a top-k sample of passages can never answer a count. Storing every
row makes the answer computed, complete and reproducible instead of retrieved and guessed.

## **3. Routing: every question gets a route before retrieval**

`replica/router.py` classifies each Ask question and logs the decision as `ask.route` (with the
matched columns and values, never the user's words beyond that):

| Route | Fires when | Model offered |
|---|---|---|
| **`tabular`** | The question names a table's columns or its low-cardinality values ("open", "first seen …") | `list_sources`, `describe_table`, `query_table`, `read_rows` (+ `run_python` when enabled) |
| **`prose`** | No tabular rule fires | `list_sources`, `search_passages`, `read_passage`, `read_span` |
| **`mixed`** | Both signals present, or the fallback classifier says so | Everything above |

The rules are deliberate and natural-phrased (the header as written, a date column's "… date"
form, plural/possessive value spellings such as "platform owners"). When no rule fires **and**
`ASK_ROUTE_LLM_FALLBACK=on`, a small model classification over the table hints breaks the tie
(prior to 2026-10-17 this classifier forwarded a `temperature` kwarg the installed Anthropic SDK
does not accept and silently fell back to `mixed` on every call — fixed with the one-model-layer
work).

## **4. The tool loop: how a question is actually answered**

The model is offered **up to eight tools**, exposed by route (table above). It may call them for
at most **12 rounds**, then must answer:

| Tool | Store it reads | What it returns |
|---|---|---|
| `list_sources` | workspace membership | The workspace's members with their shape |
| `describe_table` | `document_tables` | Columns, types, distinct low-cardinality **values**, sample, row count |
| `query_table` | `document_table_rows` | **The complete set of matching rows** — filter (`where`, `icontains`, `in` …), project, sort, deduplicate; never a sample |
| `read_rows` | rows + passages | Specific rows by number, chosen columns — the payload evidence |
| `search_passages` | `document_passages` | Keyword/semantic matches with row citations |
| `read_passage` / `read_span` | `document_passages` | A passage or a character span of it, in full |
| `run_python` | the turn's results | Sandboxed code over the turn's results so far (tenant-flagged, tabular/mixed only) |

Rules the loop enforces **in code**, not by asking nicely:

- **`describe_table` before any `query_table`/`read_rows` on that table** — the model must look
  at the real columns and values before it filters them.
- **A value-word in the question filters that column over every stored value the wording
  covers** (`describe_table` lists them) — never membership judged from titles or descriptions.
- **Platform/product names match with `icontains`** (exports spell names in mixed case).
- **False positives are named by business key with the deciding phrase quoted verbatim**, only
  when the deciding column is payload; otherwise it is already in the query result and judged
  there.
- **Every streamed citation is checked before it is sent** — an uncheckable citation discards
  the sentence rather than show a made-up source (`replica/model_answer.py`,
  `test_a_streamed_answer_only_sends_checked_citations`).

Each turn's statistics — tools called, predicates applied, row counts, dedup removed, any relaxed
predicate — are logged by kind and count only.

## **5. The one model layer (owner decision 2026-10-17)**

**Every surface reads the same layer**: the research graph, Ask, follow-up chat and the live
evals. The choosable models, from `config/models.yaml` (`choices`):

| Model | Provider | Key | Role it can fill | Notable features |
|---|---|---|---|---|
| **Claude Opus 5.5** | anthropic | `ANTHROPIC_API_KEY` | research (default), analysis | strict JSON, web search, citations, adaptive thinking, prompt caching |
| **Claude Fable 5.1** | anthropic | `ANTHROPIC_API_KEY` | analysis (default), research | as above |
| **Kimi K3** | moonshot | `MOONSHOT_API_KEY` | research or analysis, user's choice | strict JSON |

How each surface reads the layer:

| Surface | Model comes from | Effort comes from |
|---|---|---|
| **Research graph** (planner / extractor / classifier / synthesis / narrative / judge tiers) | The run's recorded choice (`account_runs.models`: the user's research and analysis models), each tier served by `ChoiceLlm.complete_as` | The tier's task in `task_settings` (e.g. synthesis/narrative `high`, extraction `low`) — passed to Anthropic (`output_config`) and Moonshot (`reasoning_effort`) alike |
| **Ask answers** | The conversation's analysis model (thread's kept model → user's preference → catalogue default); a saved workspace Anthropic key serves Claude selections, while Kimi uses Moonshot | `surfaces.chat.effort` (`high`) |
| **Follow-up chat** | The same conversation model as Ask (Kimi included; a tenant's own Anthropic key — BYOK — can serve Claude only) | `surfaces.chat.effort` |
| **Live tabular eval** | `surfaces.eval.model` (default `kimi-k3`), overridable per run with **`EVAL_MODEL`** | `surfaces.eval.effort` (`high`), overridable with **`EVAL_EFFORT`** |

Hard invariants that hold regardless of the choice:

- **The judge never equals the generator** (G12): when both roles name the same model, the judge
  is served by the other Claude model (`independent_judge`).
- **A model that cannot meet a step's schema falls back to that role's Claude default** after two
  failed validations, and the fallback is recorded on the run with its reason.
- **Switching models is config, never code.** There are no hardcoded model ids in `src/`; the two
  YAML catalogues (`models.yaml`, `chat_models.yaml`) and the `surfaces` block are the whole
  switchboard.
- **Anthropic determinism caveat (OI-LLM-1):** the installed SDK's Messages call has **no
  `temperature` or `seed` parameter** — nothing on the Anthropic path forwards one (doing so
  TypeErrors). Research-graph determinism comes from schema validation plus bounded re-ask; the
  tabular eval on a Claude model treats the per-run answer assertions as the hard gate.
  Moonshot pins `temperature 0` + `seed 0` for tabular turns.

## **6. Conversations, spend and replay**

- Each conversation (**thread**) keeps its analysis model — the first Ask's model persists on the
  thread, and follow-up turns keep using it while it stays enabled and allowed.
- Turns are **reserved before they run** (`reserve_turn`): per-user chat limits, per-thread turn
  and dollar caps, idempotency by request id — a repeated request **replays** the stored answer
  instead of paying again.
- The tabular eval proves the contract on the owner's real GSK workspace: five runs per case, the
  canonical **evidence** traces (filtered `query_table` calls plus `read_rows` on the target
  table) must converge ≥3/5, and **every** run's answer must pass its assertions; a run that
  never queries the target table fails outright even if its numbers look right.

## **7. The research graph, briefly**

Research runs execute as LangGraph graphs (checkpointed, resumable; Cloud Tasks drive the steps
on staging), routed per phase: **collection** (Opus-class: pulling, planning, extraction,
classification), **analysis** (Fable-class: prediction, synthesis, narrative), then **runtime
quality checks** (source traceability, cross-tenant isolation, deliverable presence, artifact
integrity) that hold the run on failure. Each phase carries its own turn and spend limits; the
per-run model choice from §5 flows into every tier. (Research is paused until 2026-11-01 by
owner decision — the wiring above is ready for it.)
