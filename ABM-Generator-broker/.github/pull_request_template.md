## Change summary

<!-- Describe one work package and its scope. -->

## Required checks

- [ ] `make check` passes, including the isolation suite.
- [ ] Migration lint passes; migrations preserve forced RLS and scoped roles.
- [ ] No tenant ID is accepted from a request body, path, query, or model output.
- [ ] No broker names or internal cost details appear in client-facing output.
- [ ] Tests cover the change and relevant failure paths.
- [ ] `BUILD_LOG.md` records commands, results, defects, and open items.
