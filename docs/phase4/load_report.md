# Load profile (report only)

Synthetic injector against stub providers and a local PostgreSQL 16 container (laptop). In-process HTTP (no network, no TLS). No target is asserted; the sourced alert-freshness SLO (95% within 24 h) is reported against. k6 is not installed here; `ops/load/k6_profile.js` is the same profile for staging.

```json
{
  "profile": {
    "tenants": 5,
    "accounts_per_tenant": 10,
    "weeks": 3,
    "concurrency": 10,
    "api_reads": 400
  },
  "probes": 150,
  "probe_wall_s": 6.18,
  "probe_ms": {
    "50": 386.9,
    "95": 606.6,
    "99": 634.9
  },
  "probe_mean_ms": 399.4,
  "read_ms": {
    "50": 151.8,
    "95": 187.9,
    "99": 202.5
  },
  "read_errors": 0,
  "freshness_sli_tenant0": {
    "good": 10,
    "judged": 10,
    "pending": 0,
    "slo": 0.95
  }
}
```
