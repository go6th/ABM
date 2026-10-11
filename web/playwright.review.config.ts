// Design review screenshots (docs/design-review/): every screen in light and dark at 1440, 1024
// and 390 wide, against the same synthetic stack as the parity suite (playwright.parity.config.ts).
import { defineConfig } from "@playwright/test";
import parity from "./playwright.parity.config";

export default defineConfig({
  ...parity,
  testDir: "./e2e/review",
  timeout: 600_000,
  reporter: [["list"]],
  use: { ...parity.use, trace: "off" },
});
