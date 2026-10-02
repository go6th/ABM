# Model split benchmark protocol

**Status: Blocked.** No September account outputs or approved model/provider measurements were supplied. No live provider was called and no result is inferred from synthetic fixtures.

| Account | Single coverage / citation / cost / time | Split coverage / citation / cost / time | Status |
|---|---|---|---|
| — | — | — | Blocked: September run outputs and model cost/time records not supplied |

This results table intentionally has no account rows until real, approved measurements exist.

The candidate arms are `single` and `split`. The split arm routes planner and extractor work to the fast tier and judge work to the strong tier. The selected role-to-tier map is in `config/model_routing.yaml`; provider and model identifiers are supplied only through environment variables. `active_arm` remains proposed until measured results and the D-02 decision are reviewed.

For each of the same three September account runs, preserve the input snapshot, model/router config version, prompt version, extracted facts, source citations, token usage, provider-reported cost, start/end timestamps and the analyst's citation-validity rubric. The JSON accepted by `scripts/benchmark_model_split.py` records, for each arm and account:

- coverage numerator and denominator using the same expected-attribute set;
- citation-valid numerator and denominator under the same analyst rubric;
- actual measured cost in USD and elapsed seconds.

The harness rejects synthetic data, requires exactly three unique accounts and both complete arms, and reports arithmetic means. Run locally with:

```bash
uv run --frozen python scripts/benchmark_model_split.py /secure/path/september-model-results.json
```

Keep the source results outside the repository if they contain account or personal data. Supply sanitized, approved measurements for the decision log; do not commit raw account material. The decision remains reversible through routing config.
