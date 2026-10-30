import sys, json
from q import q
d = sys.argv[1]
aid = q("select id from accounts where domain=%s", (d,))[0][0]
print("== runs")
for r in q("select id,status,finished_reason,created_at,run_input,tool_calls_used,spent_usd from account_runs where account_id=%s order by created_at", (aid,)): print(" ", r)
print("== tool_calls per run (ledger rows, sum cost, provider cost)")
for r in q("select run_id,count(*),round(sum(coalesce(cost_usd,0))::numeric,4),round(sum(coalesce(provider_cost_usd,0))::numeric,4),sum(coalesce(credits,0)) from tool_calls where run_id in (select id from account_runs where account_id=%s) group by 1", (aid,)): print(" ", r)
print("== job_postings per run")
for r in q("select run_id,count(*),sum(case when new_since_previous then 1 else 0 end),sum(case when new_since_previous is null then 1 else 0 end),sum(case when full_text_read then 1 else 0 end) from job_postings where account_id=%s group by 1", (aid,)): print(" ", r)
print("== job_postings keys / flags latest")
for r in q("select run_id,posting_key,status,new_since_previous,first_seen,last_seen from job_postings where account_id=%s order by run_id,posting_key", (aid,)): print(" ", r)
print("== sources / link_archive counts", q("select count(*) from sources where account_id=%s", (aid,)), q("select count(*) from link_archive where account_id=%s", (aid,)))
print("== research_jobs")
for r in q("select id,status,scope,created_at from research_jobs where account_id=%s order by created_at", (aid,)): print(" ", r)
