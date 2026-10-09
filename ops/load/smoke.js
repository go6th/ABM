// k6 load smoke against the LOCAL stack (`make up`), run in Docker by `make load-smoke`.
// Authenticated as the fixed SYNTHETIC member that `make doctor` seeds (tenant LOCAL_TENANT,
// user "doctor", viewer). Thresholds fail the run: error rate under 1%, p95 under 500 ms.
// For staging use ops/load/k6_profile.js with a synthetic session (never a real user).
import http from "k6/http";
import { check } from "k6";

const API = __ENV.API_URL || "http://host.docker.internal:8080";
const IDP = __ENV.IDP_URL || "http://host.docker.internal:9090";
const TENANT = "00000000-0000-4000-8000-00000000d0c7";

export const options = {
  scenarios: {
    readers: { executor: "ramping-vus", startVUs: 1, stages: [
      { duration: "10s", target: 12 }, { duration: "30s", target: 12 }, { duration: "5s", target: 0 },
    ] },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    "http_req_duration{kind:read}": ["p(95)<500"],
    "http_req_duration{kind:probe}": ["p(95)<200"],
  },
};

export function setup() {
  const res = http.post(`${IDP}/token`, JSON.stringify({ sub: "doctor", tenant_id: TENANT, role: "viewer" }),
    { headers: { "Content-Type": "application/json" } });
  check(res, { "token minted": (r) => r.status === 200 });
  return { token: res.json("access_token") };
}

export default function (data) {
  const auth = { headers: { Authorization: `Bearer ${data.token}` }, tags: { kind: "read" } };
  const r = Math.random();
  const res = r < 0.2
    ? http.get(`${API}/readyz`, { tags: { kind: "probe" } })
    : http.get(`${API}${r < 0.6 ? "/v1/accounts" : "/v1/runs"}`, auth);
  check(res, { "status 200": (x) => x.status === 200 });
}
