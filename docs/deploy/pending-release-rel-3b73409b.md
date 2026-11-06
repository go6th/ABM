# Pending release rel-3b73409b (built 2026-11-05 15:38 UTC, NOT deployed)

> **Superseded 2026-11-05 ~19:50 UTC** by rel-e91586d8 (live: api 00128-xg5, worker 00110-xgs, web 00081-rr6), which carries this work too. Do NOT deploy these images: they would roll staging back. See `NOTE-2026-11-05-confirm-twice.md`.

Built on the owner's word ("build and wait for me, I will deploy"). Snapshot of the working tree
(branch `feat/final-product`, HEAD 3a3e1398 plus the tree's uncommitted work) as one Cloud Build
d138b88d (api, worker, web in parallel). No new migration since 0111; the migrate job still runs
as a safety step. **Supersedes rel-f77c55bb** (`pending-release-rel-f77c55bb.md`): deploy this
one instead; it carries everything rel-f77c55bb did plus the work below.

## What it carries beyond the revisions live now (rel-350a4cdd)

From rel-f77c55bb (`docs/audits/ucb-workspace-deep-audit-2026-11-05.md` section 6): the research
workspace zip in the owner's `data/` and `notes/` layout, no header-only CSVs in Download
everything, one file of an uploaded zip downloadable from the account page, the size-cap retry
fix.

New in this build (section 7 of the same audit): an uploaded package that differs a little from
the reference is stored as properly as the original:

- files named in words ("UCB People List.xlsx", "Sources & Evidence.docx"), a `Deliverables`
  folder in any case, no RESTORE.md, or the five files zipped at the root;
- renamed columns ("Full Name", "Job Title", "Seniority level", "LinkedIn URL", "Req ID",
  "Platforms", "Posting URL"), new columns and new sheets kept as written;
- a workbook named in no known way registered by what its sheets hold;
- `Logs/import_report.md` says per sheet what was read, what was read under another name and
  which columns are kept as written only.

Web image rebuilt 2026-11-05 ~16:10 UTC after the final check (the "Download this file" link moved to its own line; web unit tests 366 passed). Use the digests below.

Proof: the real UCB zip changed six ways, all six SAME (tables cell for cell, five files
registered, people 1,620, postings 104); documents and replica suites 867 passed.

## Images

```
REG=us-central1-docker.pkg.dev/abmgenerator/ai-staging-images
API=$REG/api@sha256:90a787719c7a9e3332b70f84fe3cf335e4f5d4afcb3ac8f7b3c2fc51c40a67cb
WORKER=$REG/worker@sha256:62dbc17a79e3de9249d1e3d71c6de75dbfa2fba1c3e51e51a3d3ae3fcd9eccfd
WEB=$REG/web@sha256:8276dfe64998e3b3d64d462cb7d024c39829d7a9d04e51f6fadfa10002573ae0
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
