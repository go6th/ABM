# ADR-0001: Platform stack

- Status: Accepted as the architecture baseline
- Source: platform skill `references/02-architecture.md`, decisions 2 and 3

## Decision

Use Python 3.12 as the primary runtime (also support validation on 3.11), FastAPI, SQLAlchemy async, PostgreSQL 16, Alembic, LangGraph Postgres checkpointing, and Inngest for triggers, schedules, and slice looping. The LangGraph checkpoint is the durability source of truth. Use the starter code as the initial implementation, not as a finished product.

## Consequences

Inngest SDK call shapes and operational limits (V4) need verification before an endpoint is wired. Vendor IDs and pricing remain configuration and open verification items. Terraform and Helm are not part of the foundation implementation.
