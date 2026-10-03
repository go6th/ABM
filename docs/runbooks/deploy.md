# Runbook: deploy and roll back

`make deploy-staging` PRINTS the full command list; it runs nothing.

- Normal path: merge to main; CI runs every gate, then the `deploy-staging` job (after the
  environment's reviewers approve) builds the image, pushes it to Artifact Registry and rolls it out
  to `ai-staging-api` and `ai-staging-worker`, then requires `/readyz` to answer 200.
- Production: the same commands with the production project, run by the owner after staging is
  verified.
- Roll back: `gcloud run services update-traffic SERVICE --region REGION --to-revisions PREVIOUS=100`
  for both services. Migrations are expand-only, so the previous image runs on the new schema.
- A failing `/readyz` (it fails closed) keeps the new revision from taking traffic; read its JSON
  for the failed check.
