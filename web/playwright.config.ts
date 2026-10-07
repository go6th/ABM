// End-to-end suite (Phase 3 test harness item 12): the real client app against the real API over a
// local test database, with a fake OIDC IdP. SYNTHETIC tenants only (tests/e2e/serve.py).
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  fullyParallel: false,
  timeout: 60_000,
  reporter: [["list"]],
  use: { baseURL: "http://localhost:3000", trace: "retain-on-failure" },
  webServer: [
    {
      command: "uv run python -m tests.e2e.serve",
      cwd: "..",
      url: "http://127.0.0.1:8080/fake-idp/tenants",
      reuseExistingServer: true,
      timeout: 120_000,
      env: { APP_ENV: "test" },
    },
    {
      command: "npx next start -p 3000",
      url: "http://localhost:3000/login",
      reuseExistingServer: true,
      timeout: 120_000,
      env: { API_ORIGIN: "http://127.0.0.1:8080" },
    },
  ],
});
