# Open items

Everything below is unverified or undecided. Nothing in this list is claimed to work.

## VERIFY items (cloud and provider facts not checked against the real service)

| ID | What | Where | How to close |
|---|---|---|---|
| V-REGION-1 | Cloud Run, Cloud SQL, Cloud Tasks, Memorystore and the chosen Claude models are available in asia-south1 | infra/envs, ADR-0020 | check in the owner's project before apply |
| V-RUN-1 | Cloud Run deterministic URL form `https://SERVICE-PROJECT_NUMBER.REGION.run.app` used for WORKER_URL | infra/envs/main.tf.tmpl | compare with `terraform output worker_url` after apply |
| V-TASKS-1 | Named-task de-duplication window and dispatch deadline for a 240 s slice on the real queue | platform/gcp/adapters.py, ADR-0018 | staging run of the kill/resume spike |
| V-SQL-1 | IAM database authentication and private-IP reachability through the connector | platform/gcp/adapters.py, runbooks/database.md | staging `/readyz` 200 |
| V-SQL-2 | The checkpointer's psycopg DSN over the Cloud SQL socket (`checkpoint-database-url`) | runbooks/secrets.md | staging run of one slice |
| V-REDIS-1 | Memorystore in-transit encryption: the client needs the instance server CA | app/container.py build_connectors | staging rate-limit test |
| V-LLM-2 | Claude model id format on Vertex AI and enabled models in the region | platform/llm.py, config/models.yaml | one call from staging with the chosen ids |
| V-JOBS-1 | Whether the job feed's description is complete (today treated as unknown, so escalated) | jobs/adapters.py | a recorded sample (docs/providers) |
| p_* | Provider facts tagged in docs/providers/CLAIMS.md | connectors/providers | per CLAIMS.md |

## Open items (behaviour decisions)

| ID | Item | Default today |
|---|---|---|
| OI-LLM-1 | anthropic 1.11.0 `messages.create` has no `temperature`; fixed temperature cannot be set | schema validation and one re-ask |
| OI-TRK-1 | The verifier may report only `lead_verification`; "rows holding Leads" needs a Lead-to-row map | verifier rows from config |
| OI-TRK-2 | A run with no Leads cannot report `lead_verification` as Done or None (no calls to cite) | Not done `not_planned`: such runs end PARTIAL |
| OI-JOBS-1 | `job_postings_read_classified` is derived from ledger counts, so "nothing matched" is Done, not the spec's None | tracker derivation |
| OI-JOBS-2 | The live board search is a web search restricted to the careers host (no keyword job-board operation) | web_search per keyword |
| OI-JOBS-3 | Gap re-entry of the jobs stage replays the stored record; a remainder-only pass is not built | replay |
| OI-RUN-1 | `runs.status` cannot be widened expand-only; non-COMPLETED ends map to `failed` with the state in `error` (ADR-0015) | mapping |
| OI-BLD-1 | No deliverable builder or production quality gate exists: every run ends PARTIAL `builders_not_configured` | NotConfigured |
| OI-PPL-1 | p_people unsupported until the grid contract is supplied; people stage uses p_org | config/people_sources.yaml |

## Decisions needing the owner (register: docs/decisions/open-decisions.md)

D-01 ext, D-04 (settlement rule, ADR-0016), D-06, D-10, D-11 ext, D-12, D-13, D-14, D-15, D-16,
D-17, D-18, D-19, D-20, the judge model profile (the active arm routes judge and generator to one
model).
