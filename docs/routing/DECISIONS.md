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
| 2026-10-13 | cf42ab55b66c47e17f69f379282fea3eda5cf5e2425a4b6321d0b54fc53f3d52 | status: proposal-unapplied; raises max_tokens of the small task kinds (support_judge, source_reread, narrative_reread, second_pass_posting, classify_person, abm_setup to 4000; classify_posting to 2000): the configured models think before answering and thinking tokens count against max_tokens, so the judge and setup replies were cut off before any JSON (runs failed 2026-10-12); tiers, candidates and prices unchanged; no benchmark | pending (product owner) |
| 2026-10-13 | ae4a298ff721c5e2a497035549028bc875d80222bd69a67354e92273e9a7c2f2 | status: proposal-unapplied; adds the research file task kinds file_summarize_section (planner tier; legacy port tier synthesis) and file_answer_question (extractor tier, the cheapest configured model), 2000 max_tokens each; tiers, candidates and prices unchanged; no benchmark | pending (product owner) |
| 2026-10-13 | 96fb324966cc4ccd7a8aad757ed6e9c09ba997b0026e6f214033533dc02add1e | status: proposal-unapplied; adds the task kind file_extract_facts (extractor tier, the cheapest configured model; 8000 max_tokens) for facts read from uploaded documents in input mode file; tiers, candidates and prices unchanged; no benchmark | pending (product owner) |
