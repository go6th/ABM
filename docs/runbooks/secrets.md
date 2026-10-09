# Runbook: secrets

Terraform creates the secrets (names only); values are added by the owner and never committed.

    printf '%s' 'VALUE' | gcloud secrets versions add SECRET_NAME --data-file=-

Secrets: `p-search-api-key`, `p-scrape-api-key`, `p-signals-api-key`, `p-signals-api-token`,
`p-people-api-key`, `p-org-api-key`, `anthropic-api-key` (only with `LLM_ACCESS=anthropic_api`),
`checkpoint-database-url`. The provider-to-environment mapping and API operation status are in
[the provider credentials runbook](provider-credentials.md). Rotation: add a new version,
redeploy (Cloud Run reads `latest` at start), then disable the old version. The runtime never logs
a secret value.
