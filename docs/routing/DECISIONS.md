# Routing decisions (D-51)

Every version of `config/model_routing.yaml` that reaches `main` is listed here with its sha256
(`scripts/check_routing_governance.py` enforces it). A version backed by a benchmark names its
report (`benchmark: benchmark-<id>`) and a human sign-off; a version without one is
`status: proposal-unapplied` and cannot go live (the container refuses `MODEL_ROUTER=on` while
`benchmark_report` is null).

| Date | Config sha256 | Decision | Signed off by |
| --- | --- | --- | --- |
| 2026-10-06 | 8594b0261390416e24e4baed5b734fcaf0cadf8b2e10e28abfefef5bea84d802 | status: proposal-unapplied (no benchmark: needs analyst-labelled golden accounts, model access and credits; one model family only, so judge independence fails closed) | pending (product owner) |
| 2026-10-08 | dcdae3fa527c0c00de2c827c68460282c3b87d824a732951f363c879c841889e | status: proposal-unapplied; adds the six ABM task kinds (abm_setup and the three extract kinds on the extractor tier, abm_sense_check and abm_distill on the planner tier; split mode, D-95, G11); tiers, candidates and prices unchanged; no benchmark | pending (product owner) |
| 2026-10-12 | ee98c83056357d346f8ca4e9ad380ca98c609e9345865ca64f0b9dc2c1e2cb6d | status: proposal-unapplied; raises max_tokens for the three ABM extract kinds (4000 to 16000) and abm_sense_check and abm_distill (3000 to 8000): replies with the output schema were truncated mid-JSON and failed validation; tiers, candidates and prices unchanged; no benchmark | pending (product owner) |
