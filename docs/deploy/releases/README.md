# Staging releases

`scripts/release/staging_release.py` builds and rolls out staging. Each release leaves a record here.

```bash
uv run python scripts/release/staging_release.py status
uv run python scripts/release/staging_release.py build --commit <sha> [--patch docs/deploy/releases/<file>.patch ...] --services api,worker,web
uv run python scripts/release/staging_release.py deploy docs/deploy/releases/<tag>.json --dry-run
uv run python scripts/release/staging_release.py deploy docs/deploy/releases/<tag>.json [--allow-migrations]
```

- **build** archives the commit (`git archive`, never the working tree, so another session's
  unreleased work is not shipped), applies the patches, runs one Cloud Build and writes
  `<tag>.json` with the image digests and the snapshot's migration files.
- **deploy** refuses while the database is not RUNNABLE/ALWAYS (a release on 2026-11-07 left an api
  revision that could not start), and refuses a migration `live.json` does not have unless
  `--allow-migrations` (migrations cannot be rolled back). It runs the migrate job, rolls out worker,
  api and web in that order, and checks each new revision: serving all traffic, `/readyz` 200,
  `/v1/models` 401, no ERROR in its first minute. Any failure moves every changed service back to
  its previous revision. On success the digests go into `infra/envs/staging/terraform.tfvars` and
  the record becomes `live.json`.

## Live line (2026-11-08)

Staging runs `feat/final-product` commit `832c077b` (`rel-832c077b`, released by hand 11:29-11:34
UTC: schema 0113, Mail and drive removed, erase-on-delete), recorded with `adopt`. It already holds
the scoped-refresh carry and the Rebuild files keepalive. A hand release outside this tool makes
`deploy` refuse until `adopt --commit <sha>` records what is live.

| Patch | What it fixes |
| --- | --- |
| `01-scoped-refresh-carry.patch` | (in HEAD) a topic, jobs or people refresh built its files from its own rows only and replaced the account's file set (Olympus 0 people / 0 postings, 2026-11-07) |
| `02-rebuild-keepalive.patch` | (in HEAD) operator Rebuild files answered after the build (328 s) and the web relay gave up at 300 s |
| `03-page-opening-after-part-refresh.patch` | (against HEAD) the account page's one-minute version and Findings count took a part refresh's sections; a question was shown as the opening |
