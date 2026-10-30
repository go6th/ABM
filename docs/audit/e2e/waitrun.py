import sys, time
from q import q
d, n = sys.argv[1], int(sys.argv[2])   # wait until n runs exist and the newest is terminal
t0 = time.time()
while time.time() - t0 < 100:
    rows = q("select r.id, r.status, r.finished_reason from account_runs r join accounts a on a.id=r.account_id where a.domain=%s order by r.created_at", (d,))
    print(round(time.time()-t0), len(rows), rows[-1] if rows else None, flush=True)
    if len(rows) >= n and rows[-1][1] in ("COMPLETED","PARTIAL","FAILED","CANCELED","BUDGET_EXCEEDED"): break
    time.sleep(6)
