# Pending release rel-f77c55bb (built 2026-11-05 14:33 UTC, NOT deployed; SUPERSEDED by rel-3b73409b)

> **Superseded 2026-11-05 ~19:50 UTC** by rel-e91586d8 (live: api 00128-xg5, worker 00110-xgs, web 00081-rr6), which carries this work too. Do NOT deploy these images: they would roll staging back. See `NOTE-2026-11-05-confirm-twice.md`.

Built on the owner's word ("just build and keep, we will deploy it later"). Snapshot of the
working tree (branch `feat/final-product`, HEAD 3a3e1398 plus the tree's other uncommitted work)
as one Cloud Build fe5181fd (api, worker, web in parallel, 6 min 11 s). No new migration since
0111; the migrate job still runs as a safety step.

What it carries beyond rel-350a4cdd (the revisions live now): `docs/audits/ucb-workspace-deep-audit-2026-11-05.md`
section 6: the research workspace zip in the owner's `data/` and `notes/` layout, no header-only
CSVs in Download everything, one file of an uploaded zip downloadable from the account page
(`GET /v1/documents/{id}/members/{member_id}`), and the size-cap retry fix (commit 3a3e1398).

## Images

```
REG=us-central1-docker.pkg.dev/abmgenerator/ai-staging-images
API=$REG/api@sha256:554ba9f37634858f9165894f8e31db1fa320cca4a7e51348be930d5d650b83d1
WORKER=$REG/worker@sha256:9683026b2be5f34494b77a239cdb264e50cef1a0cdd0e21fd00a880067842418
WEB=$REG/web@sha256:d197b5baadd222ffd820b5a5afe6cfc7091fe422507bfbd88aedbd4dc5ea49bc
```

## To deploy (DB must be running: `gcloud sql instances describe ai-staging-pg` shows RUNNABLE/ALWAYS)

```bash
R="--region us-central1 --project abmgenerator"
gcloud run jobs update ai-staging-migrate $R --image "$API"
gcloud run jobs execute ai-staging-migrate $R --wait
gcloud run services update ai-staging-worker $R --image "$WORKER"
gcloud run services update ai-staging-api $R --image "$API"
gcloud run services update ai-staging-web $R --image "$WEB"
```

Then write the three digests into `infra/envs/staging/terraform.tfvars` (`images = {...}`), check
`/readyz` 200, `/v1/models` 401 at https://136-81-60-29.sslip.io, and zero ERROR logs on the new
revisions.

## Rollback (what runs today)

api ai-staging-api-00127-fhx (b80bab85), worker ai-staging-worker-00109-w7d (1f290792),
web ai-staging-web-00080-457 (95829760).

## After the deploy

Re-upload `UCB_workspace.zip` to the ucb.com account on staging (the import there predates file
registration).
