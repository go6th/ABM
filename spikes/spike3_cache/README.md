# Spike 3 — Prompt-cache read ratio (T12)

**Pass criterion:** cache-read ratio ≥ 0.70 from the second call onward; no invalidation
across slices of ≥ 6 minutes.

## Run

```bash
# deterministic, offline (CI)
.venv/bin/python spikes/spike3_cache/run.py --calls 10

# demonstrate silent invalidation (timestamp-like change) -> FAIL
.venv/bin/python spikes/spike3_cache/run.py --invalidate

# long gap between slices (cache lifetime check)
.venv/bin/python spikes/spike3_cache/run.py --gap 400

# live provider (OpenAI-compatible; set SPIKE3_API_KEY / SPIKE3_BASE_URL in .env)
.venv/bin/python spikes/spike3_cache/run.py --provider live --model <model-id> --calls 10
```

The prompt follows the Spec 10.2 playbook: sorted tool definitions, then system SOP, then
stable reference text; canonical JSON; explicit breakpoints; tenant data only in the tail.

Results are recorded in `docs/phase0/cache_measurement.md`.
