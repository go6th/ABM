# Release rel-carry2-11071634 (built 2026-11-07, NOT deployed: staging DB stopped)

api only. Snapshot = commit add14705 + src/exports/{carry.py,obs.py,readers/pg.py} + my hunks of
src/account_intelligence/api/routes/admin_console.py (the uncommitted `account_of_domain` edit of
another session is NOT in it). Cloud Build 1d1566e2.

Adds over live rel-carry-11071524 (api 00136-vn4, worker 00118-66n): operator "Rebuild files" replies
at once and sends a space every 15 s while the build runs, then the same JSON, so the web relay's
300 s header wait no longer cuts it off (Olympus rebuild 2026-11-07 took 328 s). The build runs as its
own task. Test: tests/replica/test_admin_console.py::test_a_slow_rebuild_sends_its_headers_at_once_and_keeps_the_relay_alive.

Revision 00137-htx failed to start at 16:4x UTC only because the DB had just been stopped; traffic
stayed on 00136-vn4.

## Deploy (DB must be RUNNABLE/ALWAYS; no migration)

```bash
gcloud run services update ai-staging-api --region us-central1 --project abmgenerator --image us-central1-docker.pkg.dev/abmgenerator/ai-staging-images/api@sha256:6eb110e89f83e476ad3eb1acf69ed349fd448939d1d657b8afb03db539abe157
```

Then write that digest into infra/envs/staging/terraform.tfvars. Rollback: api 00136-vn4.
