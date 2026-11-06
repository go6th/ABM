# 5. Running it, testing it, releasing it, finding out what went wrong

*Part of the [project guide](README.md). Written 2026-10-29. Commands were checked against the
`Makefile`, `DEPLOYMENT.md`, `RUNBOOK.md` and what was done on staging on 2026-10-28.*

## 5.1 Run it on your computer

```bash
make install     # Python packages (uv sync --frozen) and pre-commit hooks
make up          # the whole local stack: db, migrate, api, worker, web, fake sign-in, emulators
make logs        # tail every service
make down        # stop (make reset also deletes the data)
```

Ports: web 3000, api 8080, worker 8081, fake sign-in 9090. Local sign-in uses the fake identity
service (`ops/dev/fake_idp.py`), so no real account is involved. `make doctor` checks your tools.

* **Web only, for screen work:** stop the web container and run `npm run dev` in `web/`. The API
  accepts only the origin `http://localhost:3000`.
* **A demo that can never spend money:** `make replica-up` starts a second copy on fake model and
  fake providers (web 3010, api 8090). Seeding needs `DEMO_PASSWORD`.
* **Real keys locally** (`compose.live.yml`) costs real money. Do not start a live run unasked.

## 5.2 Run the tests

```bash
# always: the markers "eval" and "live" call the paid model
TEST_DB=ai_test_mine PGPASSWORD=dev \
APP_RW_PASSWORD="$(grep '^APP_RW_PASSWORD=' .env | cut -d= -f2-)" \
MIGRATOR_DATABASE_URL=postgresql://postgres:dev@localhost:5432/ai_test_mine \
.venv/bin/pytest tests/agents tests/grading -m "not eval and not live" -q
```

Three rules that have bitten people:

1. **Always pass `-m "not eval and not live"`.** An explicit `-m` replaces the default; `-m "not eval"`
   alone still runs live tests.
2. **One pytest session per test database.** The session fixture *drops and recreates* `TEST_DB`.
   Pick your own name (`ai_test_<name>`); two sessions on one name lose their tables.
3. **Never `source .env`.** Its `DATABASE_URL` points at a real database. Export only
   `APP_RW_PASSWORD` as shown.

Other gates: `make static` (import contracts, docstrings, loops register, security scan),
`make contract` (API contract and generated web client), `make check` (the full pre-PR gate on a
scratch database). The generated references (`docs/api/openapi.json`, `docs/WIRING.md`,
`docs/architecture/loops.md`, …) must be regenerated when their source changes; `scripts/README.md`
says which script does which, and `make static` fails when one is stale.

**Mutation testing** (are the tests able to notice a wrong rule?): `make mutation` mutates 35 pure
or fake-driven modules (counts, windows, spend, tracker, exports, link archive, credits) with the
hermetic tests only, takes about 10 minutes and fails under 90 percent of the non-equivalent
mutants of any module. `make mutation-report` reads the last run. Method, score table and the
rules for adding a module: [`docs/testing/mutation.md`](../testing/mutation.md).

## 5.3 Where it runs in the cloud

* Google Cloud project `abmgenerator`, region `us-central1`. Staging is reached at
  `https://136-81-60-29.sslip.io` (a temporary address).
* Cloud Run services **`ai-staging-api`**, **`ai-staging-worker`** (with a virus-scan sidecar),
  **`ai-staging-web`**; Cloud Run Job **`ai-staging-migrate`**.
* Cloud Tasks queues: **`ai-staging-runs`** (research slices), **`ai-staging-documents`**,
  **`ai-staging-jobs`**. The worker holds at most (max instances × 4) in flight.
* Cloud Scheduler calls the worker on a timer: `sweep` every 5 min, `schedules-tick` every 15 min,
  `notify-relay` every 5 min, `metrics` every 2 min, `maildrive-sync` hourly, plus nightly
  isolation, access-expiry and audit-verify checks.
* The worker **scales to zero** (0–3 instances), so an in-process timer only runs while it is
  warm; that is why the sweep is also driven by Cloud Scheduler.

## 5.4 Release to staging

The CI path (`DEPLOYMENT.md` §2.1) is: green CI on `main` → `deploy-staging.yml` pushes signed
images → runs the migration job from the *new* api image → deploys each service by digest → smoke
test → rolls back on failure. Production is a manual, approval-gated workflow.

The **hand path** used in practice on this project (`DEPLOYMENT.md` §2.2):

1. Snapshot the tree and build the images on **Cloud Build** (laptop builds of the worker stall
   downloading GPU wheels). The repository's `.gcloudignore` drops `docker/`, so the snapshot must
   drop that file or the Dockerfiles never reach the build.
2. If a migration is new: update and run the **`ai-staging-migrate`** job with the new api image.
3. Update the services **by digest**, worker first if only the worker changed:
   `gcloud run services update ai-staging-worker --image …@sha256:…`.
4. Check: the new revision is `Ready=True`; no ERROR logs; `GET /readyz` is 200 and `/healthz`
   answers; the log event `run.finished` appears for the next research.
5. **Rollback** = send Cloud Run traffic to the previous revision. Do not undo a migration unless its
   `.down.sql` is safe.

Cautions learned the hard way:

* **Do not deploy while a slice is running.** The new revision replaces the instance and the slice
  in flight is lost (its paid calls replay free, but the time is lost). Deploy when the run queue
  is empty: `gcloud tasks list --queue=ai-staging-runs …`.
* **Terraform:** `terraform plan -out=plan.tfplan` then apply *that file*. A stale plan once rolled
  the api and worker back. Cloud Run modules ignore image changes, so images are deployed with
  `gcloud`, and the digests are written to `terraform.tfvars` for bookkeeping only.
* The research queues have been **paused** before when the model provider's usage limit was hit;
  resume them only deliberately.
* **Never start a live paid research just to test.** A failed run once cost about $500.

## 5.5 Find out why a research stopped (a checklist)

1. **Find the run and its end event.** In Cloud Logging, the worker logs `slice.started`,
   `slice.ended` (with `outcome` = `progressed` / `no_progress` / `graph_stopped` and the `stage`) and
   `run.finished` (with `state` and `stop_reason`).

   ```bash
   gcloud logging read 'resource.labels.service_name="ai-staging-worker"
     AND jsonPayload.event=~"^(run|slice)\."' --freshness=1d --order=asc \
     --format='value(timestamp,jsonPayload.event,jsonPayload.outcome,jsonPayload.stage,jsonPayload.stop_reason)'
   ```
2. **Read the stage.** Three `no_progress` slices in one stage means a single step is longer than a
   slice (or hung). `pull.row_slow` names a pulled row that took over 540 s.
3. **Look for an error.** `severity>=ERROR` in the worker, and a Python traceback in `textPayload`.
   Log *messages* from libraries are stripped by the log catalogue (only allow-listed fields
   survive), so a bare `urllib3.connectionpool` warning has no text.
4. **A worker that vanished:** search for `LIVENESS HTTP probe failed` and `Container terminated on
   signal`. Then read the **stall dump** (`most recent call first` / `Thread 0x…` lines in
   `textPayload`) written by `obs/stalldump.py`: the main thread's frame names what blocked the
   event loop.
5. **Did the platform restart it?** `runs.swept` then `runs.auto_resumed` in the worker log. No
   `auto_resumed` after a stop means the limit of 2 automatic continuations was used up (or the
   dead slice's 30-minute lease has not expired yet): the owner presses **Resume**.
6. **The real error behind a failed run** is in the LangGraph checkpoint, not in `runs.error`: the
   thread id is `<tenant_id>:<run_id>` and `graph.aget_state(config).tasks[*].error` names the node
   and message.
7. **Cloud Tasks view:** `resource.type="cloud_tasks_queue"` logs show each attempt's duration and
   status; `gcloud tasks list --queue=ai-staging-runs` shows what is still queued.

## 5.6 What a normal healthy research looks like in the logs

`slice.started (start)` → `pull.row_slow` ×0–2 (the two job-posting rows today) →
`slice.ended progressed (pull→pipelines)` → `pipelines.jobs` / `pipelines.tech` →
`slice.ended progressed (person_searches)` → … → `run.finished state=COMPLETED`. Each slice is
about 27.5 minutes (1650 s); a full research takes several slices. `llm.usage` lines are the model
calls.

Next: [6. Known problems](06-known-problems.md).
