# `docker/`: the images and the CI image scripts

Three images are built from this repository and all three run in staging today:

| Image | Built from | Runs as |
| --- | --- | --- |
| `api` | `docker/api.Dockerfile` (context: repository root) | Cloud Run service `ai-staging-api` and the migration job `ai-staging-migrate` |
| `worker` | `docker/worker.Dockerfile` (same code; `ENTRYPOINT python -m account_intelligence.worker`, overridden to uvicorn on `/tasks` for the service) | Cloud Run service `ai-staging-worker` (with the `clamav` scan sidecar from the registry's `stable` tag) |
| `web` | `web/Dockerfile` (context: `web/`) | Cloud Run service `ai-staging-web` |

Both Python images: `python:3.12-slim`, dependencies installed with `uv sync --frozen` from
`uv.lock`, the embedding model baked into `/app/models`, non-root user, `config/` and (api only)
`migrations/` copied in, no tests, docs or env files (`.dockerignore`). `make image` builds all
three locally; `make smoke` runs the api image against the local database.

`ci/` holds the scripts the GitHub workflows run on those images:

| Script | Used by | What it does |
| --- | --- | --- |
| `image_checks.sh` | `ci.yml` images job | non-root, and none of the files an image must not carry |
| `hermetic_stack.sh` | `ci.yml` hermetic job | the just-built images as a stack of plain `docker run` containers (fake model, synthetic providers) |
| `hermetic_run.py` | `ci.yml` hermetic job | one research run end to end against that stack |
| `probe_isolation.py` | `ci.yml` hermetic job | read-only cross-tenant probe |
| `sign_images.sh` | `_deploy.yml` | keyless cosign signature per digest |
| `smoke_deployed.sh` | `_deploy.yml` | read-only smoke test of a deployed environment |

Image hygiene: the registry keeps the digests that are serving plus the previous release of each
service as a rollback target; everything older can be deleted (`/home/sakib/unused code ABM generator/cleanup.sh`
lists the commands).
