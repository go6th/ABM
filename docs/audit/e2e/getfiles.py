import sys, os
from client import *
domain, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
a = Api(); a.login()
files = a.get(f"/v1/accounts/{domain}/deliverables").json()["files"]
for f in files:
    r = a.get(f"/v1/accounts/{domain}/deliverables/download", params={"ids": f["id"]})
    open(f"{out}/{f['filename']}", "wb").write(r.content)
    print(f["kind"], f["filename"], r.status_code, len(r.content), f["id"])
