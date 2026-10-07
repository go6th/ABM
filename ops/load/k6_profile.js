// k6 profile for a STAGING run (HD2): the same mix as ops/load/injector.py. Not run here (k6 is
// not installed); [VERIFY] k6 options and thresholds against the k6 docs before use. No target is
// asserted except reporting against the sourced alert-freshness SLO.
import http from "k6/http";
import { check } from "k6";

export const options = {
  scenarios: {
    readers: { executor: "constant-vus", vus: 10, duration: "5m" },
  },
};

const BASE = __ENV.BASE_URL;            // the staging origin
const COOKIE = __ENV.SESSION_COOKIE;     // a synthetic viewer's session (never a real user)

export default function () {
  const path = Math.random() < 0.5 ? "/v1/alerts" : "/v1/signals";
  const res = http.get(`${BASE}${path}`, { headers: { Cookie: `__Host-ai_session=${COOKIE}` } });
  check(res, { "status is 200": (r) => r.status === 200 });
}
