# Pending staging release rel-fc603e07 (built 2026-11-05 ~01:30 UTC)

> **Superseded 2026-11-04 21:05 UTC** by `rel-fc9aa617` (a later snapshot of the same tree, taken
> after these edits were finished): api `00120-xx2`, worker `00102-8qr`, web `00075-pg9` are live and
> the database was started. Do not run the two `services update` commands below: they would roll the
> api and worker back to older images. The upload step at the end still applies. See
> `docs/deploy/NOTE-2026-11-05-morning.md`.

Images are built and pushed (Cloud Build 87af1cb3, tag `rel-fc603e07`). The web service is
already on it (revision `ai-staging-web-00074-zmn`). The api and worker could not start their new
revisions because the staging database was stopped (Cloud SQL connector: no certificate), so
Cloud Run kept `ai-staging-api-00118-dsl` and `ai-staging-worker-00100-6kb`. No new migration.

Start the database, then update the two services:

```bash
gcloud sql instances patch ai-staging-pg --project abmgenerator --activation-policy=ALWAYS
```

```bash
gcloud run services update ai-staging-api --project abmgenerator --region us-central1 --image us-central1-docker.pkg.dev/abmgenerator/ai-staging-images/api@sha256:780c985c8ba41ac79744970536acca8b405d1edfa6541fb29edf9b27ea0494cb
```

```bash
gcloud run services update ai-staging-worker --project abmgenerator --region us-central1 --image us-central1-docker.pkg.dev/abmgenerator/ai-staging-images/worker@sha256:c1305bf92e928310277c47f6295cabd6f65c249e88f7dbd2c33508be25480600
```

Then on the UCB Inc account page upload `UCB_workspace.zip` again. The upload is held with the
question "Is this the right account? This workspace is about UCB (ucb.com)..."; answer "Yes, same
company". The import then runs with every table, and "Download everything" is rebuilt from it.

Rollback (quick revert only): api 00118-dsl, worker 00100-6kb, web 00073-cs4.
