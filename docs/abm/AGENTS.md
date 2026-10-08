# Agent catalogue (D-95)

Registry: `config/agents.yaml`; contracts and tool allow-lists: `src/agents/contracts.py`; schemas:
`src/agents/schemas.py` (every model forbids extra fields, so a grade, a tool call or a tenant id in
an output is a schema failure and the output is rejected whole).

| Agent | Step | Tier (task kind) | Tools | Output | Guards | Code |
|---|---|---|---|---|---|---|
| Setup assistant | 1 | extractor (`abm_setup`) | settings.read, payloads.read | `SetupProposal` (a visited page id or null) | G7, G13 | `setup/agent.py` |
| Pull agent | 2-4 pulls | none: code only | ledger.call_operation, payloads.put | `ManifestRow` (refs, queries, cost, Blocked reason) | G1, G5, G7, G8, G13, G15 | `pull/agent.py` |
| Pass 1 | 2 | extractor (`abm_pass1_extract`) | payloads.read | `ExtractionOutput` | G2, G4, G9, G10, G13 | `extraction.py` |
| Pass 2 | 3 | extractor (`abm_pass2_extract`), one call per source group | payloads.read | `ExtractionOutput` (+ lead-drop requests) | G2, G3, G4, G9, G13 | `extraction.py` |
| People | 4 | extractor (`abm_people_extract`) | payloads.read | `PeopleOutput` | G4, G8, G9, G13 | `extraction.py` |
| Sense check | 5a | planner (`abm_sense_check`) | facts.read, credit.price | `SenseCheckOutput` (typed findings, pull proposals) | G6, G7, G13 | `sense_check/agent.py` |
| Distill | 5 | planner (`abm_distill`) | facts.read | `DistillOutput` (claims with fact ids) | G4, G9, G10, G13 | `distill/agent.py` |
| Build, gate, delivery | 6-8 | none: code only | builder, gate, delivery ports | file ids, failing checks | G1, G6, G9 | `graph.py` nodes |

## What code decides, whatever the model says

* **Origin (G2):** a candidate is `read` only if every evidence payload is a full-page read
  (`scrape`, `page_read`); a model claiming `read` on a search snippet is downgraded to a match,
  and the grading engine grades it Lead. An engine that promotes an unread match fails the run
  closed (`unread_match_promoted`).
* **Evidence (G4):** every quote must appear verbatim in a payload that call was given, and the
  candidate's row must be the row of every payload it quotes.
* **People:** name, title and any tenure must be quoted in the evidence.
* **Lead drops (G3):** three differently worded queries (reordering or re-casing is not different)
  across two tool categories; otherwise the lead stays open and the refusal is counted.
* **Careers page:** the model picks a visited page id; the URL is the stored page's, never built.
* **Sense check:** finding kinds from config; fact ids must exist; a contradiction names two or
  more facts and becomes Conflicting through the grading port; proposals are priced in code and
  routed by `spend_route` (run within the approved cap, otherwise pause or record).
* **Distill:** claims need known fact ids and may not restate a grade; both audiences are scanned
  for tool, provider and person names (the Internal Only view is tenant-facing too).
* **Budgets (G13):** per-agent tokens, tool calls and seconds; a breach is `BudgetExceeded`, a
  typed end recorded in the run counters, never a retry.

## Run graph

```
setup -> cap_approval -> pull <-> spend_gate              Phase 1 (full, pull)
      -> [pull: finish]
      -> load_manifest -> interpret -> sense_check        Phase 2 (full, build)
         -> [sense_approval] -> [targeted_pulls -> sense_check, record only]
         -> distill -> build -> gate -> deliver -> finish
```

`finish` is the only settlement point. Approval points are `cap_approval`, `spend_gate` and
`sense_approval` (LangGraph `interrupt`, resumed with `Command(resume=bool)`).

## Production bindings (ADR-0100)

| Port | Production adapter | Existing component it uses |
|---|---|---|
| ProviderPort | `production/provider.py` `ConnectorProvider` | `connectors.router.call_need` (routing, pull once, credit gate, retries by node policy) |
| PayloadPort | `production/payloads.py` `StorePayloads` | platform payload store, neutral content-addressed keys |
| GradingPort | `production/grading.py` `EngineGrading` | `grade_claims`, `record_graded_fact`, `record_drops`, `record_lead`, `supersede` |
| TrackerPort | `production/tracker.py` `TrackerAdapter` | `TrackerService.record_outcome` / `downgrade` (inherited ledger for Phase 2) |
| CreditPort | `production/credit.py` `CreditAdapter` | `spend.service.propose`/`approve`, `spend.pause.open_pause`, cost table |
| RunRecordStore | `production/state.py` `PgRunRecords` | `abm_sense_checks`, `abm_distill_outputs` (migrations 0041, 0042) |
| SettingsPort | `production/state.py` `SettingsAdapter` | tenant settings and the run's frozen input |
| Builder, Gate, Delivery | `production/delivery.py` | export builder, quality gate, the `deliver` stage's note |
| WalletPort | `RunnerSettles` | settlement stays in `lifecycle.finish` (once) |
| AgentModel | `production/wiring.py` `RunDepsModel` | `RunDeps.model` (router or legacy tier) and `llm.schema.validate` |

Runner: `workflow.runner.execute_slice(..., graph_factory=AbmGraphFactory())`; the worker endpoint
chooses it for `pull` and `build` runs and, for `full` runs, when the tenant's `abm_agents` flag
is on.
