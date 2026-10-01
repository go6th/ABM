# Phase 1 infrastructure cost estimate (minimum viable, 1–2 pilots)

Cap: **≤ $600 / month** (T8 exit criterion). Prices are list-price estimates at
pre-spike time; T12/T13 and the unit-economics pass (T89) update the measured values.

| Line | Item | Monthly (USD) |
|------|------|---------------|
| DB | Managed Postgres 16, 2 vCPU / 8 GB, multi-AZ standby | $180 |
| Compute | 2 small app nodes (API + worker, on-demand) | $120 |
| Cache | Managed Redis, 1 GB | $35 |
| Storage | S3-compatible object storage + backups, ~200 GB | $45 |
| Observability | Self-hosted Langfuse on app node; OTel → managed logs/metrics, low volume | $60 |
| Network | ALB, NAT gateway (single), data transfer | $70 |
| Workflow | Inngest free tier (pilot volumes) | $0 |
| Identity | IdP free tier (≤ 2 orgs) | $0 |
| Contingency | Snapshots, secrets, misc | $40 |
| **Total** | | **$550** |

## Cost model (per-run), Spec 16.2 parametrised — to be replaced by measured values

| Parameter | Planning value | Source |
|-----------|----------------|--------|
| Planner-tier price | $3 in / $15 out per M tokens | Illustrative [V1] |
| Extractor-tier price | $1 in / $5 out per M tokens | Illustrative [V1] |
| Cache-read multiplier | 0.10 × base input | Illustrative [V1] |
| Cache-read ratio | target ≥ 0.70 (alert < 0.70) | **Measured by T12** |
| Tokens per standard run | planner 250k in / 25k out; extractor 300k in / 40k out | Calibrate from pilot |
| Broker cost per run | $4.50 MVP | **Measured by T13** |
| LLM cost per run @ 75% cache ratio | ≈ $1.12 | Spec 16.2 sensitivity table |
| Cost per run (all-in, MVP) | ≈ $7.04 | Spec 16.2 |
| Price per run for 70% gross margin | ≈ $23.48 | Spec 16.2 |

## Measured values (fill after T12/T13 run)

| Measurement | Value | Date |
|-------------|-------|------|
| Prompt-cache read ratio (spike 3) | _pending_ | — |
| Broker cost per standard run (spike 4) | _pending_ | — |
| Coverage of expected attributes (spike 4) | _pending_ | — |

> The T14 gate review blocks "Go" while either measurement is pending.
