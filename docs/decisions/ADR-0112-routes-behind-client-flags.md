# ADR-0112: routes with no screen answer 404 until the client's flag is on

- Status: Accepted
- Date: 2026-11-03
- Deciders: owner (fix prompt B, task 4)

## Context

`docs/audits/orphan-routes.md` lists the tenant routes no web screen calls. Connectors, CRM
write-back and the monitoring product (signals, alerts, subscriptions) are product work in
progress: deleting them throws away tested code, leaving them open exposes unfinished surface.

## Decision

`@behind_flag(flag)` (`src/account_intelligence/api/flags.py`) gates a route on a client feature
flag (`feature_flags`, `admin/flags.evaluate`, deny by default). It runs after the route's own
authentication and authorisation and before its body; with the flag off the answer is the same
404 problem body an unknown path gets, so the catalogue's default deny is untouched and nothing
reveals the route. The decorator keeps the endpoint's FastAPI signature (resolved annotations,
the endpoint's own `Request` reused). Gated: all 8 `connectors.py` routes (`connectors`), all 7
`crm.py` routes (`crm`), all 9 `signals.py` routes (`monitoring`); `GATED_FLAGS` is the one list.
Nothing was deleted; the (b) list is for prompt F.

## Consequences

Turning a feature on for a client is an operator flag change (`POST /operator/v1/flags`), no
deploy. The isolation matrix turns the three flags on for every client so its probes still reach
the handlers. Tests: `tests/api/test_behind_flag.py`.
