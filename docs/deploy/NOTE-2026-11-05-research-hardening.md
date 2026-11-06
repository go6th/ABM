# Release note: research hardening (2026-11-05)

Design: docs/plans/research-hardening-2026-11-05.md. Verified before release: ruff, ruff format,
mypy (751 files), OpenAPI snapshot, generated web client, tracker and people docs current, web
tsc, eslint and vitest (366), backend tests of every touched area after the review fixes (jobs,
people, tracker, llm, workflow, runs, unit, agents, replica estimate/refresh/consent, replay keys:
2,341 passed). Full backend suite before the review fixes: 7,709 passed; the remaining failures
are in other uncommitted work (OpenAPI members route, exports G9 and determinism,
workspace_import writer, exact_copy loop annotations and docstrings, isolation planting).

An independent review found 7 issues, all fixed and tested: replies logged before the deploy are
still reused (old key fallback); a page read refused before any call stays open (row Partial,
not Done); slice count, provider timeouts/retries and kept page length stay with the spend caps;
the refresh warning shows only for people and full; a failed consent check never breaks the
estimate; the 12-month pull row says "feed did not return every posting"; an unverified careers
page keeps blocking instead of reading as "no careers site".

## What ships

- worker: completion policy (stated gaps), jobs row reasons, breadth follows the source, people
  mention retry bound kept at 2, model reply reuse across a re-added company (account id
  normalised in the key).
- api: `gaps` on GET /v1/research/estimate and GET /v1/accounts/{domain}/refresh.
- web: the gaps listed above Proceed with a link to the Teammates page.

No migration. No new environment variable (optional `RESEARCH_BREADTH=capped` restores fixed
breadth counts). Staging keeps `RESEARCH_CAPS=off`.

## Before deploying

1. Start the database: `gcloud sql instances patch ai-staging-pg --activation-policy=ALWAYS
   --project abmgenerator` and wait for RUNNABLE. With it stopped the migrate job times out and
   new api/worker revisions fail startup (2026-11-05 01:30).
2. Deploy api, worker and web together (the web reads the new `gaps` field; it is optional, so an
   older web with the new api is also fine).

## After deploying

- /readyz 200, /v1/models 401, zero ERROR logs on the new revisions.
- Open New research: with people consent not granted the Proceed box lists the consent gap.
- Runs already started keep the completion policy pinned at their start; new runs use the new one.

## Rollback

api 00127-fhx, worker 00109-w7d, web 00080-457 (live before this release).

## Shipped (2026-11-05 ~19:50 UTC)

Deployed inside rel-e91586d8 (api 00128-xg5, worker 00110-xgs, web 00081-rr6), the working-tree
snapshot another session released with the confirm-twice rule; it holds every file of this
change and of the green pass (the full suite before release: 7,733 backend passed in three shards,
367 web; the one shard failure, test_a_hermetic_research_never_blocks_the_loop, was CPU contention
and passed 3 of 3 alone). Checked after: /readyz 200, /v1/models 401, home 307, zero ERROR logs,
terraform.tfvars digests equal the live images. An identical build rel-f39c9fec (Cloud Build
1abb5dab) was not deployed. Rollback: api 00127-fhx, worker 00109-w7d, web 00080-457.

## Redeployed as rel-7b28a9e2 (2026-11-05 ~20:15 UTC)

Owner: "complete the steps, solve the errors and deploy it". Whole working-tree snapshot (src identical
to the fully tested tree: 7,733 backend passed; web 367 passed; ruff, mypy, OpenAPI, client, loops
current), adding the 2026-11-06 "Which company is this file for?" form (name and website only).
Cloud Build 6bb6c17f; migrate job ai-staging-migrate-lbxw4 OK (no new migration); api 00129-cvs,
worker 00111-r6m, web 00083-ngs, 100% traffic; /readyz 200, /v1/models 401, home 307, zero ERROR
logs; digests written to terraform.tfvars. Not included: the later workspace_files.py
clean-once speedup (next release). Rollback: api 00128-xg5, worker 00110-xgs, web 00082-z77.
