# ADR-0003: Starter-code defects and known limitations

- Status: Accepted as a defect record; fixes and gaps tracked separately
- Source: platform skill `references/13-starter-code-and-known-defects.md`

## Fixed in the starter and retained

1. Parallel LangGraph branches reduce `spent_usd` rather than writing an unreduced shared value.
2. Tenant and public-reader RLS policies name their roles; this prevents permissive-policy OR behavior from exposing private facts to the public renderer.

## Known defects retained as strict expected failures

1. Two workers can execute and bill the same call concurrently; the starter has no per-run lease or intent row. Agree on a guarantee and close the crash/replay gap before claiming exact-once charging.
2. An `Idempotency-Key` replay with a different request body currently returns the earlier run without rejecting the mismatch.

## Consequences

Keep both `xfail(strict=True)` tests and their reasons until the defects are fixed. Remove the markers only when the corresponding regression tests pass and the original failing behavior is no longer possible.
