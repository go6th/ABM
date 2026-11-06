# Staging this morning (written 2026-11-04 21:10 UTC, overnight)

## What is live

`rel-fc9aa617` — a snapshot of the whole working tree taken at 20:43 UTC, after the other
session had stopped editing. Deployed 21:05 UTC:

| service | revision | rollback |
|---|---|---|
| api | ai-staging-api-00120-xx2 | 00119-mzp (other session's build, 20:37) / 00118-dsl (rel-5132465d) |
| worker | ai-staging-worker-00102-8qr | 00101-hbn / 00100-6kb |
| web | ai-staging-web-00075-pg9 | 00074-zmn / 00073-cs4 |

Checks after the rollout: migrate job `ai-staging-migrate-5b8fq` Completed (no new migration
beyond 0110), `/readyz` 200, `/v1/models` 401, home 307, zero ERROR log lines on the three new
revisions. Digests are in `infra/envs/staging/terraform.tfvars` (git-ignored). Nothing is
committed; the tree is as the two sessions left it.

Gate before the build: ruff, ruff format and mypy clean on the whole tree (748 source files);
1,845 export/replica/chat tests and 1,522 document/repair/unit tests green on this tree.

**Full backend suite on the deployed tree: 7,635 passed, 4 failed** (1 h 39 min, finished
22:52 UTC). None of the four is a runtime defect; all are static guards, and two are fixed:

| test | cause | state |
|---|---|---|
| `tests/isolation/test_matrix.py` | the new `POST /v1/documents/{id}/same-company` route was not in `tests/isolation/matrix/matrix.yaml` | fixed (entry added, probe tenant; 567 passed) |
| `tests/system/test_static_guards.py …w6` | `docs/architecture/loops.md` and `module-map.md` were stale against the code's LOOP comments | fixed (`scripts/gen_loops.py` run; the two docs changed) |
| `tests/system/test_single_write_paths.py` | `src/documents/repo.py` now writes the `accounts` table; the guard allows only `datamodel/…` and `runs/lifecycle.py` | **open, for the author of the same-company work**: move that write behind lifecycle, or extend the guard's owner list on purpose |
| `tests/system/test_event_loop.py …never_blocks_the_loop` | one first-request callback spends 0.22 s on the loop thread building a route's pydantic/FastAPI schema (`routing.py app -> _generate_schema …`); the guard allows 0.05 s; fails alone too | **open**: one-time per process, not a leak; find which route compiles lazily and build it at startup |

Both open items are guards on the other session's additions, not on anything deployed from
this session; staging runs fine with them.

## Two things I did that you should know

1. **I started the Cloud SQL instance** (`ai-staging-pg`, activation ALWAYS) at 21:02 UTC. It
   was STOPPED when I came to deploy (the migrate job timed out on it), and a visitor cannot use
   staging with the database off. It is still running. When the visit is over:
   `gcloud sql instances patch ai-staging-pg --activation-policy=NEVER --project=abmgenerator`.
2. **Another session was deploying to staging at the same time** (api 00117 at 18:51, worker
   00101 at 20:33, api 00119 at 20:37, web 00074 at 20:40). Its own note,
   `docs/deploy/pending-release-rel-fc603e07.md`, says its api and worker revisions never
   served because the database was stopped. My release supersedes it: same tree, snapshotted a
   few minutes later after its last edits (workspace_identity, workspace_raw, exports/repair),
   and the database is now up. I marked that note superseded so nobody rolls the api/worker
   back to its older images. **Its last step still stands for you:** on the UCB Inc account
   page upload `UCB_workspace.zip` again and answer "Yes, same company" to the held question —
   knowing that a completed upload auto-starts a FULL research run (paid; see the audit below),
   which is why I did not do it tonight.

## Errors fixed in this release (from the Olympus run audit)

- `GET /v1/chat/threads/{id}` 500: a stored answer carried `context_tokens` the contract did
  not know; the field is in the contract and `routes/chat.py` `stored_answer()` drops one
  unreadable answer instead of failing the whole thread.
- Account Summary withheld for one dash in a *stored* field: `exports/builder.py`
  `stored_texts()` keeps every non-authored string outside the dash rule.
- `6th.ai` workspace 422 "Response held back": one refused field is blanked, the screen loads;
  `sheet_cells_withheld` now logs allow-listed fields (it arrived empty before).

## Not done, on purpose: no paid model call was made

- The Olympus research (olympusamerica.com, 18:44–19:08 UTC, PARTIAL `files_withheld`) still
  has its Summary withheld. With the builder fix live, rebuilding the files costs nothing:
  `POST /v1/exports` for that run (the web has no button for it), or **Resume research** on the
  account page — resume reuses the stored sources but may run the distill/judge model steps
  again, so I did not press it.
- Your two presses of Resume last night never reached the API (no `POST /v1/research/resume`
  in the logs at any time after 19:08); the page you had open was served before the 19:50
  deploy. A hard reload first.

## What the audit says about the Olympus data (unchanged by this release)

Run on **Haiku 5.5** for extraction and planning (your model choice for research; the config's
tested arm is Opus). 2,786 candidate rows → 633 off-target, 316 duplicates, 486 with the value
not in the cited sentence; 565 facts kept (181 Confirmed, 378 Lead, 6 Probable). Jobs: 144
postings found, 23 read in full, **0 matched a platform**. Section 1 of the Summary lost 7 of
its authored blocks to `unregistered_number` because the distill prompt never tells the model
which figures are registered (`src/agents/distill/prompt.md`, B6 in `exports/numbers.py`): a
prompt change, left to you. The UCB zip upload at 16:56 auto-started a FULL paid run at 17:45
(`app/auto_analysis.py`, 249 Haiku calls) until you stopped it at 18:21.
