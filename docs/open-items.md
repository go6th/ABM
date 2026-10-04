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
| V-JOBS-2 | Real PostgreSQL behaviour of the 0013 rollback under concurrent pass writers (the down path drops `pass_no` columns) | migrations/0013 | run `alembic downgrade 0012` on a staging copy with live rows |
| V-PEOPLE-1 | The title-search record shape (employer, current flag, headline) and whether a provider cursor can be exposed to the caller | people/adapters.py, people/models.py RawPerson | one recorded sample per people operation |
| V-PEOPLE-2 | The full-profile record shape (prior employers, dates and their precision, certifications, own description, last activity) | people/adapters.py UnavailableProfiles | a recorded profile sample and the grid contract |
| V-PEOPLE-3 | A posts and comments operation and its record shape (post URL, date, text, last activity) | people/adapters.py UnavailablePosts | a sourced operation and a recorded sample |
| V-EXP-1 | The Excel cell limit of 32,767 characters (taken from Microsoft's "Excel specifications and limits"; not checked against Excel itself) | config/export_rules.yaml limits | open a workbook with a 32,767 and a 32,768 character cell in Excel |
| V-EXP-2 | LibreOffice recalculates openpyxl formulas on load and caches results (verified locally with LibreOffice 26.2.4.2 by `test_integration_checks.py`); the production image has no LibreOffice | exports/verify.py | install it in the gate image and re-run the integration checks there |
| V-EXP-3 | Package normalisation holds for future openpyxl and python-docx versions (verified for openpyxl 3.1.5 and python-docx 1.2.0 by the byte-identical rebuild test) | exports/ooxml.py | the determinism test on every dependency upgrade |
| p_* | Provider facts tagged in docs/providers/CLAIMS.md | connectors/providers | per CLAIMS.md |

## Open items (behaviour decisions)

| ID | Item | Default today |
|---|---|---|
| OI-LLM-1 | anthropic 1.11.0 `messages.create` has no `temperature`; fixed temperature cannot be set | schema validation and one re-ask |
| OI-TRK-1 | The verifier may report only `lead_verification`; "rows holding Leads" needs a Lead-to-row map | verifier rows from config |
| OI-TRK-2 | A run with no Leads cannot report `lead_verification` as Done or None (no calls to cite) | Not done `not_planned`: such runs end PARTIAL |
| OI-JOBS-1 | `job_postings_read_classified` is derived from ledger counts, so "nothing matched" is Done, not the spec's None | tracker derivation |
| OI-JOBS-2 | The live board search is a web search restricted to the careers host (no keyword job-board operation) | web_search per keyword |
| OI-RUN-1 | `runs.status` cannot be widened expand-only; non-COMPLETED ends map to `failed` with the state in `error` (ADR-0015) | mapping |
| OI-BLD-1 | No deliverable builder or production quality gate exists: every run ends PARTIAL `builders_not_configured` | NotConfigured |
| OI-PPL-1 | p_people unsupported until the grid contract is supplied; people stage uses p_org | config/people_sources.yaml |
| OI-PPL-2 | The tracker accepts only this run's calls as proof, so a run that reuses every profile from an earlier run reports `full_profiles_senior_people` Not done `not_planned` | honest Not done; needs a "reused from run X" proof kind |
| OI-PPL-3 | No earlier stage stores people named in research as structured data, so the mention step has no input in production | `NoMentions` (NotConfigured) |
| OI-PPL-4 | Association search needs a technology identifier per lane (`association.technology_by_lane`) | lanes end `not_available_yet` |
| OI-PPL-5 | Migration 0014 has `down_revision` 0012 because 0013 is uncommitted on `feat/job-posting-pipeline`; re-point it when the branches meet | revision 0014 |

## Decisions needing the owner (register: docs/decisions/open-decisions.md)

D-01 ext, D-04 (settlement rule, ADR-0016), D-06, D-10, D-11 ext, D-12, D-13, D-14, D-15, D-16,
D-17, D-18, D-19, D-20, D-21, D-22, D-23, the judge model profile (the active arm routes judge and generator to one
model).

## Export builders (open items and follow-ups)

| ID | Item | Default today |
|---|---|---|
| OI-EXP-1 | Distill does not write `report_sections` yet (first follow-up) | the Summary is refused (`missing_narrative`); the run ends PARTIAL `build_failed` |
| OI-EXP-2 | The jobs stage does not register posting links in the source registry | posting rows fail B4 (`missing_source_code`); the Postgres test registers them to prove the rest |
| OI-EXP-3 | The people stage does not register Findings post links | findings fail B4; the Postgres test registers them |
| OI-EXP-4 | Grading's `record_graded_fact` does not write `facts.lane` | facts render under "Findings not assigned to a lane" |
| OI-EXP-5 | No pipeline stores team data | the Teams sheet carries the visible "None recorded" note |
| OI-EXP-6 | `tech_detections` has no writer; its `source_ref` must be a registered URL | detections without a URL fail B4 |
| OI-EXP-7 | `tenants.policy_json.salesforce_partner` has no settings API | false unless set in the database |
| OI-EXP-8 | Client staff names come from the Phase B redaction map, which does not exist | only other accounts' names and domains and guarded people names are forbidden terms |
| OI-EXP-9 | Cloud Storage put-if-absent uses get then put (a race writes the same content-addressed bytes); an `if_generation_match=0` precondition is better | content addressing makes the race harmless |
| OI-EXP-10 | An object put before a killed commit is unreferenced until the retry reuses it; a sweeper for unreferenced objects | never served; reused on retry |
| OI-EXP-11 | Download links and roles for deliverables (API) | none |
| OI-EXP-12 | The legacy Node library, workbook scripts and one real delivered file set (port, characterization tests, legacy parity) | Blocked; neutral proposal theme |
| OI-EXP-13 | Stale-phrase list, abbreviation list, more partner framings, RESTORE.md wording, news attribute prefixes | empty or placeholder, `source: TODO` |
| OI-EXP-14 | `config/output_spec.yaml` comments name the Internal Only file after a person; the skill names it "Internal Only" | the builders never build it; rename with the owner |
