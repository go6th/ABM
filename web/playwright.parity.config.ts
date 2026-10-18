// Parity suite (docs/UI_BACKEND_AUDIT.md): every kept feature, by role, against the real API on
// a test database (tests/e2e/serve.py: two synthetic clients, fake IdP, synthetic model). The
// web app runs in development mode because the operator proxy refuses a plain-http API origin
// in production; it uses port 3001 because the API accepts loopback origins only on 3000 and
// 3001 (auth.py _allowed_base_url). Operator pages sign in with the dev staff verifier (APP_ENV=test).
import { defineConfig } from "@playwright/test";

const apiOrigin = process.env.E2E_API_ORIGIN ?? "http://127.0.0.1:8088";
const webOrigin = process.env.PARITY_WEB_ORIGIN ?? "http://localhost:3001";
process.env.PARITY_WEB_ORIGIN = webOrigin;
process.env.E2E_OPERATOR_SESSION_COOKIE = "6th_operator_session";
process.env.E2E_OPERATOR_SESSION_SECURE = "false";

export default defineConfig({
  testDir: "./e2e/parity",
  workers: 1,
  fullyParallel: false,
  timeout: 120_000,
  expect: { timeout: 20_000 },
  reporter: [["list"], ["json", { outputFile: "test-results/parity.json" }]],
  use: { baseURL: webOrigin, trace: "retain-on-failure", navigationTimeout: 90_000 },
  webServer: [
    {
      command: "uv run python -m tests.e2e.serve",
      cwd: "..",
      url: `${apiOrigin}/fake-idp/tenants`,
      reuseExistingServer: true,
      timeout: 180_000,
      env: { APP_ENV: "test", E2E_WEB_ORIGIN: webOrigin },
    },
    {
      command: "npx next dev -p 3001",
      url: `${webOrigin}/login`,
      reuseExistingServer: true,
      timeout: 240_000,
      env: { API_ORIGIN: apiOrigin, NEXT_DIST_DIR: ".next-parity" },
    },
  ],
});
