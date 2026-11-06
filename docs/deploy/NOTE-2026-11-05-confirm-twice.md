# Release note: rel-e91586d8, ask twice before research spends money (2026-11-05)

Owner asked to commit and deploy after an upload's automatic analysis cost about 100 USD.
Snapshot of the working tree (branch `feat/final-product`, HEAD faf9b2f5 plus the tree's other
uncommitted work), one Cloud Build 6ac4d66b (api, worker, web; 6 min 17 s). It supersedes the
pending builds rel-3b73409b and rel-f77c55bb (it carries their work too).

## What ships

- Every research start (POST /v1/research, /v1/research/resume, /v1/accounts/{domain}/refresh,
  /v1/runs) needs `confirm` and `confirm_again` both true, else 428 `confirm_twice`.
- Nothing starts research by itself: the upload's model analysis, the auto-resume sweep and the
  scheduled refresh tick are off unless `RESEARCH_AUTOMATIC_STARTS=on` (not set on staging).
- Web: Proceed leads to "Are you sure?"; Resume research and Analyze file ask twice; the upload
  screen says uploading starts nothing.
- Plus the pending rel-3b73409b work (research hardening, workspace zip layout, tolerant package
  import).

## Deployed 2026-11-05 ~19:50 UTC

migrate job ai-staging-migrate-8wdbw OK; worker 00110-xgs (0b2b7607), api 00128-xg5 (439faa3c),
web 00081-rr6 (6aec31e8). /readyz 200, /v1/models 401, home 307, zero ERROR logs. RESEARCH_CAPS=off
kept. Digests written to infra/envs/staging/terraform.tfvars.

Build tip: submit the snapshot with an empty ignore file (`--ignore-file=.noignore`); the repo's
`.gcloudignore` drops docker/ and `.dockerignore` drops web/.

## Rollback

api 00127-fhx, worker 00109-w7d, web 00080-457 (rel-350a4cdd).

## Superseded ~20:15 UTC

Another session deployed rel-7b28a9e2 (api 00129-cvs, worker 00111-r6m, web 00083-ngs), a later
whole-tree snapshot that keeps runs/start_guard.py and every confirm-twice change (checked: both
confirmations in the web, RESEARCH_AUTOMATIC_STARTS unset on api and worker, /readyz 200).
