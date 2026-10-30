import sys, uuid, time
from client import *
from q import q
name, domain = sys.argv[1], sys.argv[2]
a = Api(); a.login()
if len(sys.argv) > 3 and sys.argv[3] == "refresh":
    est = a.get(f"/v1/accounts/{domain}/refresh"); print("refresh options", est.status_code, est.text[:400])
    r = a.post(f"/v1/accounts/{domain}/refresh", {"request_id": str(uuid.uuid4()), "scope": "full"})
else:
    e = a.get("/v1/research/estimate"); print("estimate", e.status_code, e.text[:300])
    r = a.post("/v1/research", {"request_id": str(uuid.uuid4()), "company_name": name, "domain": domain})
print("start", r.status_code, r.text[:300])
jid = r.json().get("job_id")
t0 = time.time()
while time.time() - t0 < 105:
    rows = q("select r.id, r.status, r.finished_reason, r.slices_used from account_runs r join accounts a on a.id=r.account_id where a.domain=%s order by r.created_at desc limit 1", (domain,))
    print(round(time.time() - t0), rows, flush=True)
    if rows and rows[0][1] in ("COMPLETED", "PARTIAL", "FAILED", "CANCELED", "BUDGET_EXCEEDED"):
        break
    time.sleep(6)
print("job", jid)
