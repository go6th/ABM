# ADR-0121: local stacks listen on loopback only; images take no package upgrade at build

- Status: Accepted
- Date: 2026-11-03
- Deciders: owner (fix prompt D, task 6)

## Context

`compose.yml` and `compose.replica.yml` published every port on `0.0.0.0`: the dev Postgres
(`postgres:dev`), Redis (no password), the fake object store and the task emulator were reachable
from the whole network. `docker/api.Dockerfile` and `docker/worker.Dockerfile` ran
`apt-get upgrade` on a digest-pinned base, so two builds of one commit could differ.

## Decision

Every published port binds `127.0.0.1` (containers still reach each other on the compose network;
the browser and tests use localhost as before). The images drop the build-time upgrade: the pinned
base digest defines the image, and a fixable HIGH CVE the Trivy gate reports is answered by
bumping the digest. `tests/system/test_local_stack_bindings.py` enforces all three rules
(loopback ports, no upgrade, Python bases pinned by digest).

## Consequences

The Trivy gate (`make release-check`) was not run in this session: if the current digest carries
a fixable HIGH CVE the gate fails until the digest is bumped. Running containers keep their old
port bindings until the stack is recreated (`docker compose up -d`).
