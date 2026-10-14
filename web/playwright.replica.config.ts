// The replica's screens against the running replica stack (make replica-up; demo seed loaded):
// every built screen at 1360 and 375 wide, with axe, zero console errors (Content Security Policy
// included), no horizontal scroll and no "not found". The demo password comes from the shell
// (DEMO_PASSWORD), never from a file in the repository.
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e/replica",
  timeout: 120_000,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: process.env.REPLICA_WEB_ORIGIN ?? "http://localhost:3010", trace: "off", screenshot: "off" },
  projects: [
    { name: "wide", use: { viewport: { width: 1360, height: 900 } } },
    { name: "phone", use: { viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true } },
  ],
});
