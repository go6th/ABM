// End-to-end suite (Phase 3 test harness item 12): the real client app against the real API over a
// local test database, with a fake OIDC IdP. SYNTHETIC tenants only (tests/e2e/serve.py).
import { defineConfig } from "@playwright/test";

const apiOrigin = process.env.E2E_API_ORIGIN ?? "http://127.0.0.1:8088";
const webOrigin = process.env.E2E_WEB_ORIGIN ?? "http://127.0.0.1:3008";
process.env.E2E_WEB_ORIGIN = webOrigin;
process.env.E2E_OPERATOR_SESSION_COOKIE = "__Host-6th_operator_session";
process.env.E2E_OPERATOR_SESSION_SECURE = "true";

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  fullyParallel: false,
  timeout: 60_000,
  reporter: [["list"]],
  use: { baseURL: webOrigin, trace: "retain-on-failure" },
  webServer: [
    {
      command: "uv run python -m tests.e2e.serve",
      cwd: "..",
      url: `${apiOrigin}/fake-idp/tenants`,
      reuseExistingServer: true,
      timeout: 120_000,
      env: { APP_ENV: "test", E2E_WEB_ORIGIN: webOrigin },
    },
    {
      command: "node .next/standalone/server.js",
      url: `${webOrigin}/login`,
      reuseExistingServer: true,
      timeout: 120_000,
      env: { API_ORIGIN: apiOrigin, APP_ENV: "test", HOSTNAME: "127.0.0.1", PORT: "3008" },
    },
  ],
});
