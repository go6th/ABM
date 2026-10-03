# Conflict register (project additions)

The settled defaults C1 to C10 are in the skill (`references/00-sources-and-conflicts.md`).

| ID | Conflict | Resolution | Where |
|---|---|---|---|
| C11 | ARCH v2 targets AWS (Aurora, S3, Secrets Manager, Inngest); the owner instructed Google Cloud | Owner instruction wins; the cloud is behind ports, isolation and durability rules unchanged | ADR-0017 |
