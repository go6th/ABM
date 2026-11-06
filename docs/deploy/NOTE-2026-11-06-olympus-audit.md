# Release rel-oly11061436 (2026-11-06): Olympus run audit fixes

Deployed about 15:10 UTC 2026-11-06 at the owner's request. It is a working-tree snapshot: the live rel-18002e08 plus the
files below. The changes are not committed.
Cloud Build d22159eb (api and worker only; web unchanged). The migrate job ai-staging-migrate-2x9fz completed
(no new migration).

Live revisions: api 00131-96c (`api@sha256:ef182396...`), worker 00113-4m5 (`worker@sha256:7934611f...`), and web
00084-9np (unchanged). /readyz returned 200, /v1/models 401 and home 307, with zero ERROR logs on the new revisions.
The digests are written to `infra/envs/staging/terraform.tfvars`.

Rollback: api 00130-f9w, worker 00112-zb4.

## Why

The research run on olympusamerica.com (run a08b5b47, Haiku 5.5 extractor and planner, Sonnet 5.5 judge)
ended PARTIAL with `files_withheld`:

- The Summary was withheld (`missing_narrative`).
- The workspace zip was withheld (`workspace:leak`).
- The Job Postings file listed 0 postings although 144 had been pulled.
- The People List was empty.

## Fixes

1. **Cut-off model replies** (`llm/route.py`, `platform/llm.py`, `agents/production/wiring.py`,
   `workflow/stages/research.py`).
   - The sense check and distill on Haiku spent 16000 and then 21000 output tokens thinking and were cut
     off twice, so no narrative was written.
   - A cut-off reply is now asked again at the model's own output limit (128000). Calls above 21000 tokens
     are streamed, because the SDK refuses large non-streamed calls.
2. **Jobs file empty** (`jobs/pipeline.py`).
   - Every careers page returned "position filled". The read fell back to the feed's full description
     but left the posting unread, and the Jobs file lists read postings only.
   - The feed description is now read when the page read fails, unless the provider marked it an excerpt.
   - A short shell page no longer replaces a longer feed text.
3. **False leak: "Workday"** (`exports/policy.py`).
   - The tenant also has a workday.com account, so "Workday", which is a lexicon platform, was a restricted
     term.
   - Platform names and aliases from `config/job_lanes.yaml` are never restricted terms now.
4. **Workspace zip withheld** (`exports/workspace_files.py`).
   - The archived release copies were named after their page titles. The package scan reads every name,
     but the names were never cleaned.
   - A name that is refused now uses the cleaned title, or the date and hash only.
5. **Delivery note** (`agents/graph.py`, `agents/production/delivery.py`, `agents/ports.py`).
   - The note always printed 0 claims graded and 0 postings on agent runs. It now carries the real counts.
6. **Upload indexing** (`documents/service.py`, `documents/repo.py`, `documents/embed.py`).
   - The document lease is now held per call, not per instance. Two tasks on one instance shared it, so
     pages were embedded two or three times and analysis started three times.
   - A call that lost its lease stops before it writes.
   - `index_page` writes only under the lease that is indexing.
   - `indexed_through` never moves backwards.
   - The embedder loads once per instance (a lock).

## Follow-up release rel-oly11061544 (about 15:55 UTC)

Deployed after the confirming research run 739d6e34, which ran 15:23–15:42. That run delivered all six files,
the gate passed, and the Summary had its opening. Its Jobs file was still empty: a second research
carries the previous run's postings without reading them again, so the 22 matched postings came over
still unread under the old rule.

- **Fix:** `JobsPipeline._revive` reads such a carried lead from its stored feed text. This makes no page
  read; only the extractor classifies it.
- **Test:** `tests/jobs/test_read_repairs.py`.
- **Build:** Cloud Build 0bf16f61, then migrate qzgzr (no new migration).
- **Live:** api 00132-wlt (`2c249fe7...`), worker 00114-hbg (`e3535105...`).
- **Rollback:** api 00131-96c, worker 00113-4m5.
- **Data already stored:** the Jobs and Tech tables of the stored runs stay empty until the next research of
  the account carries the postings again.

Signals = 0 on an account created by research is by design. `signal_events` is written by the workspace
import and by scheduled monitoring (refresh probes), never by a research run.

## Release rel-oly11061803 (about 18:20 UTC): people data consent from the admin console

No client admin can sign in on staging. A password admin session is refused (`admin_requires_mfa`),
and client admin rights come through the client's SSO with multi-factor sign-in. So people data
consent could never be granted, and every research skipped the people steps.

- **API:** new routes `GET` and `POST /operator/v1/console/clients/{tenant_id}/people-consent` in
  `api/routes/admin_console.py`.
- **Web:** a "People data consent" card on the client page (`/operator/clients/<id>`).
- **Behaviour:**
  - Records consent to the current text version (pdc-1), or withdraws it, for the client.
  - The record names `operator:<staff id>`.
  - Writes an `operator_audit` row and the client's own audit event (`operator.people_consent.*`).
- **Tests:** `tests/replica/test_admin_console.py`, and both routes added to the isolation matrix. 951
  api/isolation tests and 367 web tests passed.
- **Regenerated:** OpenAPI, the generated web client and the wiring inventory.
- **Build:** Cloud Build 18b53548, then migrate h4bhf (no new migration).
- **Live:** api 00133-bxh (`a3a241c9...`), worker 00115-676 (`737ef596...`), web 00085-wgk (`8c1aa19f...`).
- **Rollback:** api 00132-wlt, worker 00114-hbg, web 00084-9np.

## Release rel-oly11061911 (about 19:45 UTC): people shown, own-name block, Rebuild files

Research run 534b9112 was the first with people consent. It found 1,268 people records and kept 356,
yet the People List was empty and 280 texts were withheld, including all 22 job descriptions.

- **People hidden by rule:** ADR-0025 was narrowed by owner decision.
  - `people_rubric.yaml internal_only_providers.listed_in_files: true` lists the organisation
    provider's people in the People List and the web tables, flagged `internal`. The workspace zip
    still leaves them out.
  - `people.config.internal_people_sql` applies the same rule to the web sheet, the account
    counts, Ask and the operator view.
- **"Olympus Corporation" blocked as a person name:**
  - the account's home page title now counts as its own name (`exports.readers.common.own_names`);
  - company words (Corporation, Inc, Ltd...) mean "not a person" (`people.names.ORG_WORDS`);
  - only person-like names are guarded (`exports.readers.pg`).
- **Rebuild files:** a new route, `POST /operator/v1/console/clients/{id}/rebuild-files {domain}`,
  with a card on the operator client page. It builds an account's files again from its newest
  delivered run and re-materialises the web tables (`views.materialize(force=True)`). It reads
  stored data only, with no provider call and no model call.
- **Tests:** 4,712 passed. One failure was already there before: `test_name_scan_exemption` flags
  the earlier session's pending-release note. The three tests these changes broke were updated;
  the people docs were regenerated.
- **Build:** Cloud Build 8676223d, then migrate ndd96.
- **Live:** api 00134-vqk, worker 00116-s6z, web 00086-hrw.
- **Rollback:** api 00133-bxh, worker 00115-676, web 00085-wgk.

## Release rel-oly11062012 (about 20:25 UTC): show every collected item, for every workspace (owner decision)

- **Named executives:** a person named on a public page the research read (a `mention`, such as
  a CEO in a press release) may be named in the files even when not on the People List. A tenant
  exclusion or an erasure still guards them (`exports.readers.pg.may_be_named`).
- **Job postings:** every read posting inside the window is listed, not only those naming a
  platform (`jobs.export.is_exported`). The new Counts line "listed that name no platform" keeps
  the sums. The account counter counts open and closed postings.
- **Signals:** the Signals sheet and counter add the research's news and leadership facts beside
  the `signal_events` (`workspace.FACT_CLASS["signals"]`, `sheets.SHEETS["signals"]`).
- **Rebuild files:** a 422 now reads "Enter the full domain".
- **Tests:** affected suites green. `tests/exports/test_db.py` had a vacuous Ref-column check:
  `sheet[n]` read a row, not a column. It is fixed.
- **Build:** Cloud Build 667176b9, then migrate lkkv5.
- **Live:** api 00135-9jw, worker 00117-csg, web 00087-279.
- **Rollback:** api 00134-vqk, worker 00116-s6z, web 00086-hrw.

## Not changed (owner action or decision)

- **People data consent** has never been granted for the workspace. All people steps are off until an admin
  clicks "Grant consent" on Teammates (/team).
- **Research cache:** the Redis cache of the purged workspace still answered 41% of the searches. It expires
  within 7 days. Start a re-research with `fresh: true`.
- **Single-word account names** such as Merge or Plausible can still match ordinary words in the leak scan.
- **Upload indexing items still open:**
  - embedding metering is counted twice when a page is written twice;
  - there is no unique index on chunks;
  - bge-small truncates passages longer than 512 tokens;
  - the hash embedder ignores non-Latin text;
  - research pages are not indexed for Ask.

## Tests

- Offline suite (`-m "not eval and not live"`): 7,762 collected, all run in two parts.
- Two tests failed only inside the full run and pass when run alone (flaky).
- `test_name_scan_exemption` fails on a person's name in `docs/deploy/pending-release-rel-18002e08.md`.
  It was already failing before these changes.
- `docs/architecture/loops.md` was regenerated after `test_one_retry_owner_per_failure_class_w6` flagged
  stale line numbers.
- New tests: `tests/llm/test_reask_full_limit.py`, `tests/jobs/test_read_repairs.py`,
  `tests/exports/test_release_scrub.py`, `tests/documents/test_retrieve_db.py` (the lease and progress case).
