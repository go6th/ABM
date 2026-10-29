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
| 13 | "Repeating data" in the output | ❓ unconfirmed |
| 14 | Four packages are stubs; `security` always reports "all clear" | 🔴 documentation risk |

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
*Fix (deployed 00077):* `workflow/runner.py` `_begin`: a redelivered slice that finds itself already
counted, **no later slice begun**, the lease free and the run still active (RUNNING, DISTILLING
or VERIFYING) **resumes from the checkpoint** instead of dropping. A copy of a slice the run has
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

### 13 — "Repeating data" ❓
Reported by the owner. Not reproduced because the output was not available to inspect. What is
known: recording a graded fact is idempotent per content *within a run*; a resume creates a **new
run** that re-records its claims under its own run id, so if a table or file combines facts from
several runs of the same job, the same claim can appear once per run. This research has had four
runs. Need: which tab or file, and two or three example rows.

### 14 — Stubs 🔴
`metrics`, `registers`, `unitcost` do nothing; `security` (pentest gate, rotation, secret scan,
SOC 2 coverage) always reports "all clear" and scans nothing. Do not describe them as working
controls. Also, `privacy`, `quality`, `contracts`, `evalgate` have no importer in `src/` (they are
used by tests, scripts or CI); their runtime use is unverified.

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
