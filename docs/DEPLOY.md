# Deploying to Google Cloud

Superseded in Phase 4C (2026-10-15). The deployment design, one-time setup, deploy, rollback and
post-deploy checks are in:

- `docs/deploy/ARCHITECTURE.md`: services, data flow, sizes, connections, estimated cost
- `docs/deploy/RUNBOOK.md`: the owner's one-time setup, deploy, rollback (revision and database
  restore point), rotating secrets, restoring a backup, checks after each deploy
- `docs/deploy/REPORT.md`: the Phase 4C report, with the owner inputs still needed

Infrastructure is Terraform (`infra/`); releases go through the GitHub Actions workflows
(`.github/workflows/`). Deploying happens only when the owner says so.
