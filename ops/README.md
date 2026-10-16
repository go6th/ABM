# `ops/`: local operations tools

Small tools that support the local stack and the operational drills. None of them is part of an
image.

| Path | What it is | Used by |
| --- | --- | --- |
| `dev/fake_idp.py` | The synthetic OIDC issuer the local stacks sign in with (discovery, JWKS, token). Dev only. | `compose.yml` and `compose.replica.yml` service `fake-idp`, `docker/ci/hermetic_stack.sh` |
| `load/smoke.js` | k6 load smoke against the local stack | `make load-smoke` |
| `load/k6_profile.js` | k6 profile for a staging load test (not run automatically) | manual |
| `load/injector.py` | synthetic load injector against stub providers; writes `docs/phase4/load_report.md` | manual |
| `dr/restore_drill.py` | hermetic restore drill (a rehearsal of `docs/runbooks/restore.md`) | manual, `tests/ops` |
| `status/generate.py` | static status page from health data and scheduled maintenance | `tests/phase4` |
