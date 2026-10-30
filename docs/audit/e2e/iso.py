from client import *
import json, httpx
b = Api("l.m@northline.example"); r = b.login(); print("northline login", r.status_code, r.text[:100])
a = Api(); a.login()
dom = "orionsystems.example"
orion_files = a.get(f"/v1/accounts/{dom}/deliverables").json()["files"]
fid = orion_files[0]["id"]
jobs_id = [f for f in orion_files if f["kind"] == "jobs"][0]["id"]
arch_url = "https://orionsystems.example/careers/jobs/s1"
tests = [
 ("own accounts list does not include orion", lambda: b.get("/v1/accounts"), lambda x: dom not in x.text),
 ("GET account", lambda: b.get(f"/v1/accounts/{dom}"), lambda x: x.status_code == 404),
 ("GET workspace", lambda: b.get(f"/v1/accounts/{dom}/workspace"), lambda x: x.status_code == 404),
 ("GET archive page url", lambda: b.get(f"/v1/accounts/{dom}/archive", params={"url": arch_url}), lambda x: x.status_code == 404),
 ("GET archive list", lambda: b.get(f"/v1/accounts/{dom}/archive/list"), lambda x: x.status_code == 404),
 ("GET archive download=true", lambda: b.get(f"/v1/accounts/{dom}/archive", params={"url": arch_url, "download": "true"}), lambda x: x.status_code == 404),
 ("GET deliverables", lambda: b.get(f"/v1/accounts/{dom}/deliverables"), lambda x: x.status_code == 404),
 ("download file id via orion domain", lambda: b.get(f"/v1/accounts/{dom}/deliverables/download", params={"ids": jobs_id}), lambda x: x.status_code == 404),
 ("download orion file id via OWN domain", lambda: b.get("/v1/accounts/cigna.example/deliverables/download", params={"ids": jobs_id}), lambda x: x.status_code in (404, 400, 422)),
 ("sheets jobs", lambda: b.get(f"/v1/accounts/{dom}/sheets/jobs"), lambda x: x.status_code == 404),
 ("sheets tab", lambda: b.get(f"/v1/accounts/{dom}/sheets/jobs/tabs/counts"), lambda x: x.status_code == 404),
 ("sheet csv", lambda: b.get(f"/v1/accounts/{dom}/sheets/jobs/csv"), lambda x: x.status_code == 404),
 ("sheet xlsx", lambda: b.get(f"/v1/accounts/{dom}/sheets/jobs/xlsx"), lambda x: x.status_code == 404),
 ("refresh options", lambda: b.get(f"/v1/accounts/{dom}/refresh"), lambda x: x.status_code == 404),
 ("refresh start", lambda: b.post(f"/v1/accounts/{dom}/refresh", {"request_id": "11111111-1111-4111-8111-111111111111", "scope": "jobs"}), lambda x: x.status_code == 404),
 ("download-everything", lambda: b.post(f"/v1/accounts/{dom}/download-everything", {"request_id": "22222222-2222-4222-8222-222222222222"}), lambda x: x.status_code == 404),
 ("history", lambda: b.get(f"/v1/accounts/{dom}/history"), lambda x: x.status_code == 404),
]
# job/run ids
ji = a.get("/v1/research/live").json()
import sys
from q import q
job = q("select id from research_jobs where account_id=(select id from accounts where domain=%s) order by created_at limit 1", (dom,))[0][0]
dl = q("select id from download_jobs limit 1") if False else []
tests += [
 ("stop another tenant's job", lambda: b.post(f"/v1/research/{job}/stop"), lambda x: x.status_code == 404),
 ("resume another tenant's run", lambda: b.post("/v1/research/resume", {"request_id": "33333333-3333-4333-8333-333333333333", "run_id": q("select id from account_runs limit 1")[0][0]}), lambda x: x.status_code in (404, 403)),
 ("unauthenticated archive", lambda: httpx.get(BASE + f"/v1/accounts/{dom}/archive", params={"url": arch_url}), lambda x: x.status_code in (401, 403)),
]
for name, f, chk in tests:
    x = f()
    print(("PASS " if chk(x) else "FAIL ") + name, x.status_code, x.text[:110].replace("\n", " "))
# and the legit user still gets 200
print("control orion jobs for owner:", a.get(f"/v1/accounts/{dom}/archive", params={"url": arch_url}).status_code)
