# 6. Known problems (the errors we are keeping)

*Part of the [project guide](README.md). State as of **2026-10-28 19:50 UTC** (every fix below is deployed to staging). Nothing below is committed to
git yet: the changes are in the working tree. The live staging worker is revision
`ai-staging-worker-00077-k2k` (image `rel-1a5d388`, deployed 2026-10-28 19:46 UTC); rollback
targets are `00076-4p7`, `00075-6xw`, `00074-cgh`, `00073-fgp`.*

Legend: ✅ fixed and deployed · 🟡 fixed in the tree, **not deployed** · 🔴 open · ❓ unconfirmed.

## 6.1 Summary table

| # | Problem | Status |
|---|---|---|
| 1 | One hung provider call stalls the whole `pull` step → three empty slices → run ends `no_progress` | ✅ |
| 2 | A long step that is still paying for model answers is counted as "no progress" | ✅ |
| 3 | A guard (G2) killed a run: an unread keyword match with conflicting sources became Probable | ✅ |
| 4 | The Google token check blocked the worker (synchronous certificate fetch) | ✅ (not the cause of #5) |
| 5 | **The worker stops answering its health check ~25 min into `person_searches` and is killed** | ✅ cause found and fixed (deployed 00077); watch the next `person_searches` |
| 6 | The worker crashed with a segfault (signal 11) after a stall | ✅ probable cause (the C stall dumper) replaced (00077) |
| 7 | A started-but-dead slice is not retried: its redelivery is a no-op, so the run is orphaned until the sweep | ✅ the redelivery resumes the run (00077) |
| 8 | Automatic resume was limited to 2 per chain; then the owner had to press Resume | ✅ bound raised to 5 (00077) |
| 9 | Two job-posting rows hit the 540 s ceiling on every pull | ✅ not a gap: the jobs pipeline finishes the pull; relabelled (00077) |
| 10 | A 503 "connection error" on a slice request after the slice ended normally | 🔴 |
| 11 | Unlabelled `urllib3` warnings in every slice | 🔴 |
| 12 | A 500 after 779 s on a slice request before the G2 crash | 🔴 not investigated |
| 13 | "Repeating data" in the output | 🟡 found and fixed in the tree (2026-10-30, wave D2): the account page, live sheets, Ask and CRM read every run of an account; **not deployed** |
| 14 | Four packages are stubs; `security` always reports "all clear" | 🔴 documentation risk |
| 15 | A Resume (or any slice after a "no slot" timeout) ends `no_progress` in three slices: dead queue entries starve the new run | 🟡 fixed in the tree (2026-10-29), **not deployed** |
| 16 | With caps on, a repeat or resumed run reserved a tiny budget taken from earlier cheap runs and ended `BUDGET_EXCEEDED` | 🟡 fixed in the tree (2026-10-30): half-rate floor, resume at the steps it has left; **not deployed** |
| 17 | Orchestration review 2026-10-30, wave D1: a transient 429/5xx killed a whole pipeline; a failed pipeline read Done; a slice ceiling refunded a run that held results; a dead worker's retries were dropped; hand-offs lost on a kill; unanswered token pause continued the run; other findings (see 6.5) | 🟡 fixed in the tree (2026-10-30), **not deployed** |

## 6.2 The details

### 1 — A hung provider call stalls `pull` ✅
*Seen:* 2026-10-28 06:26–07:21. Slices 1–3 all ended `no_progress` in `pull`; the run ended PARTIAL.
*Why:* the `pull` step is one graph node, and with caps off the provider timeouts are raised to
15–120 minutes (`connectors/settings.py` `uncapped_config`), longer than a 27.5-minute slice. One
call that never answered held the node, the checkpoint never moved, and each new slice restarted
the node.
*Fix:* every pulled row has a **540 s ceiling** (`PULL_ROW_CEILING_S`, `agents/graph.py`). A row over
the ceiling becomes a named gap (`provider_slow`, log `pull.row_slow`) and the rest are kept. Test:
`tests/agents/test_run_guards.py`.

### 2 — Progress was measured only by the checkpoint ✅
*Seen:* 2026-10-28, `pipelines` stage: two slices in a row ended "empty" although the step was
busy reading postings.
*Fix:* a slice that times out counts as progress if the run stored **new model replies**
(`runs/lifecycle.py` `model_replies`, used by `workflow/runner.py` `_timed_out`). Three truly empty
slices still end the run. **Side effect to watch:** a run that keeps making new paid calls without
ever finishing a step is no longer cut off after three slices.

### 3 — Guard G2 `unread_match_promoted` ✅
*Seen:* 2026-10-28 10:26. The run stopped with `agents.errors.GuardViolation: G2
unread_match_promoted: probable`.
*Why:* in the grading order the "conflicting sources → at most Probable" rule runs *before* the
"keyword match not read this run → Lead" rule, so a conflicting unread match came out Probable.
Guard G2 treats that as an engine defect and ends the run.
*Fix:* `grading/product.py`: the conflict cap is a ceiling, not a promotion. Test:
`tests/grading/test_unread_match.py`.

### 4 — Blocking token check ✅
`api/routes/tasks.py` `_authorise` fetched Google's signing certificates over synchronous HTTPS
on the event loop. It now runs in a thread with a 20 s limit (503 on timeout so the caller
retries). Test: `tests/system/test_wiring.py`. This did **not** stop the freezes (#5).

### 5 — The worker stops answering its health check ✅ (deployed, awaiting a live confirmation)
*Seen:* 2026-10-28 at 12:12, 14:00, 15:44 and 18:32 UTC, each ≈25 minutes after a `person_searches`
slice began. Cloud Run logged `LIVENESS HTTP probe failed 3 times consecutively … The instance has
been shut down.` and the slice in flight was lost. CPU and memory were low.
*Evidence:* the stall dump (`obs/stalldump.py`) fired on 18:32 and showed the **main thread inside
`yaml/parser.py`** (two dumps 15 s apart) with all 16 worker threads idle.
*Cause (probable):* `load_grading_config()` had no cache and is called **once per fact**
(`agents/graph.py` `may_state`, `tracker/leads.py`, `agents/grading.py`). Each call parses four YAML
files in pure Python. One load takes 0.02 s here, so a batch of 732 facts blocks the loop for
about 16 s (more on the worker's CPU).
*Fix (in the tree):* `grading/config.py` caches by directory **and** file modification time/size, so
an edited file is read again; `agents/lessons.py` uses the cached skill config. YAML parses in a
graph test run fell from 708 to 4. Deployed in worker 00077. Confirmation: a `person_searches`
stage passing the 25-minute mark with no stall dump and no liveness failure.

### 6 — Segfault (signal 11) ✅ (probable cause removed)
Cloud Run logged `Container terminated on signal 11` at 18:32:36, 15 s after the first stall dump
and in the same second as the second one. The first stall dumper used
`faulthandler.dump_traceback_later`, whose C watchdog walks the frames of *running* threads
without the interpreter lock; that is the probable cause of the crash. It is replaced by a plain
Python watchdog thread (`obs/stalldump.py`) that reads the main thread's stack under the lock,
which cannot corrupt anything. Trade-off: a C call that holds the lock throughout is not
reported; pure-Python stalls (the YAML case) and blocking I/O are. Tests:
`tests/obs/test_stalldump.py`.

### 7 — Orphaned run after a dead slice ✅
When a worker died mid-slice, Cloud Tasks redelivered the slice, but `begin_slice` had already
counted it, so the redelivery answered "noop" (200) and was dropped. The run then waited for the
sweep (every 5 min), which acts only after the lease expires (30 min), and then for a continuation
job: each crash cost 30–40 minutes.
*Fix (deployed 00077; extended 2026-10-30, 6.5 F5):* `workflow/runner.py` `_begin`: a redelivered slice that finds itself already
counted, **no later slice begun**, the lease free and the run still active (RUNNING, DISTILLING
or VERIFYING) **resumes from the checkpoint** instead of dropping. After a *hard* kill the lease was
still live (it lasted 1800 s), so the retry never reached `_begin`; the runner now renews the lease
every 20 s and the ledger lets another worker take a lease that has not been renewed for 90 s, so the
fourth of Cloud Tasks' retries (about +150 s) resumes the run; the queue now allows eight attempts
(retries at +10, +30, +70, +150, +310, +610, +910 s), so three more remain. A copy of a slice the run has
moved past is still dropped (the 2026-10-26 redelivery storm stays fixed). Log event
`slice.resumed`. Tests: `tests/workflow/test_slice_drive.py`.

### 8 — Automatic resume bound ✅
`MAX_AUTO_RESUMES` (`replica/auto_resume.py`) was 2 per chain; on 2026-10-28 three worker
shutdowns used it up by 14:26 and the owner had to press Resume. Raised to **5** (owner: a research
"should not stop"). A continuation reuses every finished pull and model reply at no cost, so the
bound guards against a crash loop, not against spend. Test: `tests/replica/test_auto_resume.py`.

### 9 — The two job-posting rows always hit the ceiling ✅ (reclassified)
`job_postings_12_months` and `job_postings_read_classified` (`config/skill/pull_plan.yaml`, need
`structured_signals`, provider operation `job_openings`) hit the 540 s ceiling on every Phase-1 pull.
*Why:* for a large account the 12-month postings feed paginates for about 20 minutes with the
page caps lifted. *It is not a gap:* the **jobs pipeline owns these rows** (`pipelines` node,
`agents/production/pipelines.py` `run_jobs`): it re-issues the same ledger keys, replays the pages
Phase 1 already fetched and finishes the pull (`pipelines.jobs` completes ~18 minutes into the
`pipelines` stage), and it reports the rows with its own evidence. The Job Postings workbook comes
from the pipeline's tables. *Change (deployed 00077):* at the ceiling a pipeline-owned row (jobs,
tech) is now recorded `owned_by_pipeline` and logged at INFO with outcome `handed_to_pipeline`;
only a row nobody else pulls becomes `provider_slow`. The Phase-1 head start is kept on purpose:
without it the `pipelines` stage would take ~27 minutes, the length of a slice.

### 10 — 503 on a slice request after a normal end 🔴
At 11:47:35 a `run-slice` request returned 503 "malformed response or connection error" after
1527 s although the slice itself logged `slice.ended progressed`. Unexplained; possibly the same
freeze reaching the response.

### 11 — Unlabelled `urllib3` warnings 🔴
Every slice logs `urllib3.connectionpool` warnings with no text, because the log catalogue strips
messages (`obs/logging.py`, allow-list). They are probably "connection pool is full" from
thread-based storage calls, but that is **unverified**. Adding the library message to the allow-list
for this logger only would settle it.

### 12 — A 500 after 779 s 🔴
`run-slice` returned 500 after 779 s at 10:13:43 on 2026-10-28. Not investigated.

### 13 — "Repeating data" 🟡
*Answer (leakage review 2026-10-30, F2):* it was real, and only in the readers that select by
account with no run filter. The delivered Word and Excel files read ONE run and never repeated; the
account page's tab counts and top-five key findings, the live sheets (People, Job postings, Tech,
News), Ask's rows and the CRM items read every run of the account, so after a Resume or an
incremental re-research each carried fact, person and posting counted once per run. They now share
one rule (`replica/current.py`): of the rows that are the same thing (same fact fingerprint, same
posting key, same person key, same technology) only the newest is read. See 6.6.

*Original report (kept):* Reported by the owner. Not reproduced because the output was not available to inspect. What is
known: recording a graded fact is idempotent per content *within a run*; a resume creates a **new
run** that re-records its claims under its own run id, so if a table or file combines facts from
several runs of the same job, the same claim can appear once per run. This research has had four
runs. Need: which tab or file, and two or three example rows.

### 14 — Stubs 🔴
`metrics`, `registers`, `unitcost` do nothing; `security` (pentest gate, rotation, secret scan,
SOC 2 coverage) always reports "all clear" and scans nothing. Do not describe them as working
controls. Also, `privacy`, `quality`, `contracts`, `evalgate` have no importer in `src/` (they are
used by tests, scripts or CI); their runtime use is unverified.

### 15 — A Resume stalls in the pull 🟡
*Seen:* e2e test 2026-10-29 (`docs/audit/2026-10-29-e2e-report.md` D-4), three times, caps on and
off. The resumed run adopted every finished call of the stopped run at once (100 calls, cost 0),
then made no call for three slices of about 33 s and ended PARTIAL `no_progress`, "pull made no
progress". Reproduced again on `ai_e2e_c` with the fake model and providers (stop at 28 s, resume).
*Why (found by reading `provider_waiters`):* the provider governor queues each unit in
`provider_waiters` and grants a slot to the **oldest live waiter**. A unit that stops polling (the
research was stopped, or its slice ended on a 30 s "no slot" wait) leaves its row for
`waiter_ttl_s` (60 s). A dead waiter never takes the slot it is chosen for, and it ranks ahead of
every waiter that came after it, so with no lease held at all the governor granted nothing to the
resumed run. Each such slice then left a new generation of dead waiters that outlived the next
slice's 30 s wait, so the starvation repeated until the run ended. Only waiting a minute before the
next resume made it work (the owner's "resume after a stall works").
*Fix (in the tree):* `config/parallel.yaml` `waiter_ttl_s` 60 to 10 (twenty polls). The same
reproduction then finished in 30 s (99 calls adopted, 24 new). Test:
`tests/parallel/test_governor.py::test_a_dead_runs_queued_units_do_not_starve_the_next_run` (fails
at 60 s, passes at the configured value). *Not done:* a unit does not delete its own waiter row when
it dies (the table is read-only for the application role; it would need a migration and a
`SECURITY DEFINER` function). Worth doing if 10 s ever proves long.

### 16 — Budgets of repeat and resumed runs 🟡
With the caps on, `replica/credits.py` `estimate` averaged `credits_used` of the account's last
three `done` jobs of the same scope. A cheap or poor earlier run (a full refresh that reserved 29
credits, a resume that reserved 1) lowered the next reserve, and the run ended `BUDGET_EXCEEDED
budget_cap`. Seen in the e2e test (D-5).
*Fix (in the tree, 2026-10-30, owner decision):* with the caps on, `estimate` never returns less
than **half of the configured rate of the scope** (`floor_credits`, `ESTIMATE_FLOOR_SHARE`; full
110, people 30, jobs 10, topic 20), and a resume reserves through `estimate_resume`: the estimate
of its scope times the share of the stopped run's planned steps still not done, never below that
floor (a run with no coverage rows is reserved whole). With `RESEARCH_CAPS=off` nothing changes
(history alone, as before). Tests: `tests/replica/test_credit_floor.py` (the e2e scenario: a cheap
run of 29 credits, a resume that reserved 1). Staging is unaffected while the caps are off.

## 6.3 Smaller things noticed

* `docker/README.md` points at a different `cleanup.sh` path than `unwanted-things/cleanup.sh`.
* Migration number 0069 does not exist (a gap in numbering).
* `config/claims.yaml`, `messaging_rules.yaml`, `pricing.yaml`, `secrets_rotation.yaml`,
  `soc2_controls.yaml`, `risk_register.yaml`, `data_model_spec.yaml` are read by no product code.
* The live view and some tables only show **web research**, not file analysis or library adds.
* Provider response-format drift detection (`contracts/`) and release checks (`quality/`) are not
  wired into the running product that we could find.

## 6.4 What to do next, in order

1. Watch the next `person_searches` stage pass the 25-minute mark on worker 00077 (#5).
2. If the worker freezes again, read the **stall dump** (guide §5.5) and fix the named line.
3. Show the owner's "repeating data" example to settle #13.
4. Watch the first resumed-on-redelivery slice (`slice.resumed`) end `progressed`.
5. Commit the work (nothing is committed yet).

## 6.5 Orchestration review 2026-10-30, wave D1 (fixes in the tree, not deployed)

Source: `docs/audit/2026-10-30-orchestration-review.md` (findings F1 to F14). Evidence per finding:
`docs/audit/2026-10-30-fix-wave-d1-done.md`. What changed, in the order of the review:

| Finding | What was wrong | Now |
|---|---|---|
| F1 | The `pipelines` node caught every exception, so a single 429/5xx on one page ended a pipeline | A retryable provider error is re-raised to the node retry policy; after its attempts the adapters state a source failure. The tech and stack writers follow the same rule |
| F2 | A failed pipeline's rows read Done when Phase 1 had left call evidence; the run could end COMPLETED with empty workbooks | Rows are reported `source_failed` and taken down to Partial; a run with a failed pipeline ends `PARTIAL steps_waiting` |
| F3 | A technology feed handed to the pipeline at the 540 s ceiling stayed "Not done: source_failed" | The pipeline reports that row with its own call once the detections are written |
| F4 | `max_slices` ended a run FAILED with a full refund even when it held results; pauses used slices | PARTIAL `slice_ceiling` keeping the results (FAILED + refund only when nothing is usable); each wait for a person adds a slice to the ceiling |
| F5 | After a hard kill the dead worker's lease (1800 s) outlived Cloud Tasks' retries, which were dropped | The runner renews the lease every 20 s; a lease not renewed for 90 s can be taken (ttl stays 1800 s so a live owner keeps its epoch); the retry of the last begun slice answers 503 until then; a worker that lost its lease stops its slice; an unanswered pause found by a redelivery is recorded, not approved |
| F6 | Four hand-offs (job, run, continuation, approval timeout) committed state and then called Cloud Tasks; a kill or a failed call left a job or run nothing started | `runs/recovery.py` in every sweep (see 2.7); the enqueue failures are logged, not raised |
| F7 | Waiting for a provider slot, or finishing provider calls, counted as "no progress" | Finished provider calls are progress; a slot wait never counts and delays the next slice 30 s |
| F9 | Only `pull` had a per-call ceiling | person searches, follow-up pulls, the entity lock and the pipelines' single page reads and searches are bounded; a slow one is a gap |
| F10 | A token-guard pause that timed out resumed as "continue" and raised the spend guard | A lapsed or declined wait resumes the graph with `no` (`token_guard_stopped` / declined spend) |
| F11 | `entity_mismatch` ended PARTIAL and charged at least one credit | FAILED with a full refund, like the other pre-spend stops |
| F12 | A continuation recomputed the incremental inputs | It copies them from the run it continues |
| F13 | Two paid planner calls over an empty fact list | None are made |
| F14 | A blip in build, gate, deliver or finish ended the run | Those nodes retry transient infrastructure errors; one that outlasts the retries is auto-resumed |

*Owner decisions of 2026-10-30 (evidence: `docs/audit/2026-10-30-owner-decisions-done.md`):* (a) the
Cloud Tasks `runs` queue has **8 attempts** (`infra/stack/main.tf`; in the tree, `terraform apply`
is the owner's); the takeover still happens at the +150 s retry and three retries remain after it;
(b) a **provider outage** that outlasts the node retries (`RateLimited`, `RetryableProviderError`,
`ProviderJobTimeout`, `CallInFlight`, `Overloaded`, `CircuitOpen`) is now auto-resumed like
`model_unavailable` (5 per chain, 24 h window; not credits, key or auth errors); (c)
`slice_ceiling` is **not** auto-resumed: the owner presses Resume; (d) `replica/workspace.py`
`STOP_TEXT` now has a plain message for every stop code that can occur (`slice_ceiling` says
Resume is needed); (e) open question 3 of the review (a call cancelled by a
ceiling leaves a `started` ledger attempt that makes an identical claim in the same slice wait for
`in_flight_wait_s`) is confirmed by reading `connectors/ledger.py claim`: a started attempt by the
same worker and lease epoch that is not yet `in_flight_stale_s` old answers `InFlight`. A small
fix would track the attempts a process really has in flight and close the others `abandoned` at
once; it touches the paid-call ledger, so it was left for the owner.

## 6.6 Leakage review 2026-10-30, wave D2 (fixes in the tree, not deployed)

Source: `docs/audit/2026-10-30-leakage-review.md` (F1 to F12). Evidence per finding:
`docs/audit/2026-10-30-fix-wave-d2-done.md`.

| Finding | What was wrong | Now |
|---|---|---|
| F1 | An exclusion added after a run was applied to the archive only: the rebuilt files used the settings version pinned at run start, the web tables were cached per run, Ask and the live sheets used the pull-time flag | The **current** exclusions win (owner asleep: privacy-safe default). Every built file and every materialised table records the exclusion set it honoured (`exclusion_hash`); one built under another set is not listed, not served and not downloadable until it is built again, and the routes that serve them rebuild first (`refresh_stale_files`; free, no provider). Excluded people and their profile addresses leave the sources list and `build/urls.json`; the live sheets and Ask hide them |
| F2 | Facts, people and postings repeated across runs in the account-wide readers (#13) | One "newest row of the same thing" rule in `replica/current.py` for counts, key findings, live sheets, Ask and CRM items |
| F3 | 90 owned and 51 tenant tables were in none of the erase, blocked or never lists; no object delete | `config/deletion.yaml` classifies every tenant table (107 erased, 12 blocked, 61 kept, each with a reason) and `tests/system/test_erasure_classification.py` fails on any table in none or two; migration 0093 lets the eraser delete them; the stored objects (five delivered files, workspace zips, raw uploads, archived bodies) are deleted by prefix after the rows (`tenancy/lifecycle.py erase_objects`, `ObjectStore.delete_prefix`). No new trigger endpoint |
| F4 | Archived page and news copies, and `data/releases/*.md`, kept emails and phone numbers | Removed when stored, when served and when exported (`archive_records.redact_contact`; a date is not a number) |
| F5 | A user-triggered rebuild did not see colleagues' private accounts as forbidden terms | The other accounts are listed in system scope (`exports/snapshot.open_system_lookup`) |
| F6 | Deleting a document removed the raw object another user's identical file pointed at | The count and the registry row are read in system scope |
| F7 | A rebuild's change note compared the run with itself | `previous_manifest` ignores the run's own sets |
| F8 | Person erasure left the person in the web tables | The account's materialised tables are discarded in the same transaction; delivered files and facts are counsel's decision (D-06) |
| F9 | `ask.route` logged client table values | Counts only (`matched_column_count`, `matched_value_count`) |
| F10 | The archive stored and printed addresses with credentials or signed queries | Credentials and secret parameters are stripped (`urls.strip_secrets`); either spelling finds the copy |
| F11 | The gate's host check read only the visible text of a file it did not make | It runs the builder's whole-file third-party scan too |
| Orch. F8 | A run-level gate failure still listed and served its built files | `workspace.deliverables` lists none of a run's files after a run-level FAIL verdict |

## 6.7 Web research speed and coverage 2026-10-30 (in the tree, not deployed)

Source and numbers: `docs/audit/2026-10-30-web-research-speed-and-coverage.md`.

| Where the time went | Now |
|---|---|
| A search's page reads, its own-site search and its reads ran one after another (about 9 of the 21 minutes of Phase 1) | Queries of a row run 4 at a time; the searches of a query together; every page read together. At most the governor's ceiling of one research's calls reach a provider at a time (`crosscut/gates.py`); the rest wait in the research, with no time limit, not in the governor's 30 s wait (`SlotUnavailable`). Measured on the fake harness: 1,275 s to 748 s for the same calls |
| **Every grading batch re-read and re-parsed every stored payload of the run**, one at a time, parsing on the event loop (12 to 20 batches a research: minutes, and the same kind of loop block as #5) | A payload is read and parsed once per slice (`grading/adapters.py`), side by side, in a thread |
| Stored pages, person-search pages, posting texts and the export reader's payloads were read or written one by one | 8 to 16 at a time |
| Phase 1 is bound by the scrape provider's ceiling of 4 (`parallel.yaml`) | **Owner decision:** the Firecrawl plan decides how high `p_scrape` can go |

Fuller search (all behind the entity lock, the off-account filters and the reading step's hostile
text drop): 15 more queries (91 web queries), own-site links followed (`pull_plan.yaml` `follow`),
a search restricted to sec.gov and the three main newswires for five rows (`also_on`), vendor
queries for the Data and AI lane and the also-tracked platforms. A row's ceiling is 540 s per wave
of 4 queries (`agents/graph.py row_ceiling_s`), so a 15-query row is not cut for its size.

Open from this work: the research cache is tenant scoped, not account scoped (L1); a rate-limited
page read is dropped without a gap (L2); `pipelines` and `person_searches` still run in order
(source numbering); the reference research's 300-release newsroom walk needs a sitemap operation.
