# Runbook: deploy and roll back

The full procedure is `docs/deploy/RUNBOOK.md` (sections 2 and 3); this page is the short form.

- Normal path: merge to `main`. CI (`.github/workflows/ci.yml`) builds the api, worker and web
  images once, inspects and scans them, runs them hermetically, and keeps them as artifacts.
  `deploy-staging.yml` then calls `_deploy.yml`: push those images, sign them, run the `migrate`
  job, deploy api, web, worker and the spend-invariants job by digest, and smoke test staging.
- Production: `deploy-prod.yml`, run by hand for a commit already deployed to staging, waits for
  the `production` environment's reviewers (the owner).
- Roll back: a failed smoke test returns traffic to the recorded previous revisions
  automatically. By hand: `gcloud run services update-traffic SERVICE --region REGION
  --to-revisions PREVIOUS=100` for api, web and worker. Migrations are expand-only, so the previous
  image runs on the new schema; a data problem needs a database restore point
  (`docs/deploy/RUNBOOK.md` section 3.2).
- A failing `/readyz` (it fails closed, also on a schema revision mismatch) keeps a new revision
  from taking traffic; read its JSON for the failed check.
