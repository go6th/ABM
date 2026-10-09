# Environment and infrastructure plan

Environment-specific non-secret defaults are in `config/dev.yaml`, `config/staging.yaml` and `config/production.yaml`. Credentials are injected via environment variables; never commit `.env`, cloud credentials, provider tokens, tenant data or benchmark outputs.

`make deploy-staging` is a one-command **local staging** path: it starts the Compose database and Redis, migrates, builds/starts the API and Inngest, and probes `/healthz`. It requires Docker Compose, an untracked `.env` with the variables in docs/ENVIRONMENT.md, and an exported `APP_RW_PASSWORD`. It does not create cloud infrastructure, publish an image or deploy to a cloud account. The script is not considered a successful staging release until all those local gates pass.

Cloud staging and production remain blocked because no cloud account, registry, region decision (D-05), managed PostgreSQL, object storage, KMS key or deployment credentials were provided. Production additionally needs the S3 raw-payload adapter; the current raw-payload implementation supports local storage only. The production config is a non-secret target-settings template, not a runnable deployment profile.

Terraform is intentionally not added until the provider, region, tenancy/isolation tier and state backend are selected and `terraform validate` can be run in a provisioned toolchain. Before then, the planned IaC should cover encrypted tenant-scoped object storage and lifecycle rules, managed PostgreSQL with pgvector, private networking, runtime/migrator roles, KMS, secret references, least-privilege worker identity, logs/metrics allow-lists, and separate dev/staging/production state. No cloud resources were provisioned in this phase.
