# Routing decisions (D-51)

Every version of `config/model_routing.yaml` that reaches `main` is listed here with its sha256
(`scripts/check_routing_governance.py` enforces it). A version backed by a benchmark names its
report (`benchmark: benchmark-<id>`) and a human sign-off; a version without one is
`status: proposal-unapplied` and cannot go live (the container refuses `MODEL_ROUTER=on` while
`benchmark_report` is null).

| Date | Config sha256 | Decision | Signed off by |
| --- | --- | --- | --- |
| 2026-10-06 | 8594b0261390416e24e4baed5b734fcaf0cadf8b2e10e28abfefef5bea84d802 | status: proposal-unapplied (no benchmark: needs analyst-labelled golden accounts, model access and credits; one model family only, so judge independence fails closed) | pending (product owner) |
