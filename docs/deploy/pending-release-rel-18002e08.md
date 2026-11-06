# Release rel-18002e08 (built 2026-11-05 22:53 UTC, DEPLOYED 2026-11-06 ~04:47 UTC)

**Deployed 2026-11-06 ~04:47 UTC** (owner: "deploy all the changes to GCP"; DB was back on RUNNABLE/ALWAYS).
No code changed between this build and the deploy. Migrate ai-staging-migrate-h77gg OK (no new migration).
Live: api 00130-f9w, worker 00112-zb4, web 00084-9np. /readyz 200, /v1/models 401, home 307, zero ERROR logs.
Digests written to infra/envs/staging/terraform.tfvars.

Built after the overnight audit. Snapshot of the whole working tree (same tree as the live
rel-7b28a9e2 plus the fix below), Cloud Build ad8ea965. Full offline backend suite on this tree:
7,738 passed, 0 failed (`-m "not eval and not live"`, 1 h 40 min). No new migration.

Not deployed because rajesh@6th.ai stopped the staging database at 21:32 UTC (activation policy
NEVER). With the DB stopped nothing runs on staging, so the leak below cannot happen there now.
Deploy when the DB is started again.

## What it adds over rel-7b28a9e2 (live: api 00129-cvs, worker 00111-r6m, web 00083-ngs)

Client-output leak fix (`src/account_intelligence/safety/prefilter.py`, `build_index`): a
restricted term (another account's name, an excluded company, a person the files do not list)
matched only with its exact whitespace. A name stored with a no-break space or a double space did
not match the same name printed with a plain space, and reached `data/people_grid.txt` in the
client workspace zip (found by `tests/exports/test_build_degrades_property.py`, which failed now
and then). The words of a term now match across any run of whitespace. Tests:
`tests/unit/test_prefilter.py` (new case), `tests/unit/test_mutation_kills_urls_prefilter.py`.

## Images

```
REG=us-central1-docker.pkg.dev/abmgenerator/ai-staging-images
API=$REG/api@sha256:088ccdb8c1c7c358f31711ecd23efc130a448ee5c6db2f5e9037caff0eb56ac9
WORKER=$REG/worker@sha256:d45a1662c9109301e532ed9d6304ccab84c52f4d63cfbb6e049a32d5d0a0fcc9
WEB=$REG/web@sha256:27442e614f7ff86c47f65bb3cb7320e3e96e841cbe54e3eca318e957e1c9e3d8
```

## To deploy (the DB must show RUNNABLE/ALWAYS first)

```bash
gcloud sql instances describe ai-staging-pg --project abmgenerator --format 'value(state,settings.activationPolicy)'
```

```bash
R="--region us-central1 --project abmgenerator"
gcloud run jobs update ai-staging-migrate $R --image "$API"
gcloud run jobs execute ai-staging-migrate $R --wait
gcloud run services update ai-staging-worker $R --image "$WORKER"
gcloud run services update ai-staging-api $R --image "$API"
gcloud run services update ai-staging-web $R --image "$WEB"
```

Then write the three digests into `infra/envs/staging/terraform.tfvars` (`images = {...}`), check
`/readyz` 200 and `/v1/models` 401 at https://136-81-60-29.sslip.io, and zero ERROR logs.

## Rollback

api 00129-cvs, worker 00111-r6m, web 00083-ngs (rel-7b28a9e2).
