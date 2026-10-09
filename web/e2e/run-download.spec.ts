// The core user journey end to end: sign in -> add an account -> start a run -> the run finishes
// (driven through the SYNTHETIC world: scripted providers and model, real slice lifecycle) ->
// build the Word and Excel files -> download them. Accessibility is checked on each screen.
import { test, expect } from "@playwright/test";
import { API, noSeriousA11y, signIn } from "./helpers";

const DOMAIN = "acme.example"; // the synthetic world's scripted account (tests/system/world.py)
let seeded: { slug_a: string };

test.beforeAll(async ({ request }) => {
  seeded = await (await request.get(`${API}/fake-idp/tenants`)).json();
});

test("sign in, add an account, run it and download the deliverables", async ({ page }) => {
  test.setTimeout(180_000);
  await signIn(page, seeded.slug_a, "ada", "admin");

  await page.getByLabel("Company domain").fill(DOMAIN);
  await page.getByRole("button", { name: "Add account" }).click();
  await page.getByRole("link", { name: DOMAIN }).first().click();
  await expect(page.getByRole("heading", { name: "Start a run" })).toBeVisible();
  await noSeriousA11y(page);

  await page.getByLabel("Include history with Synthetic Partner Co").check();
  await page.getByRole("button", { name: "Start run" }).click();
  await expect(page.getByText("Run started.")).toBeVisible();

  const runs = await (await page.request.get("/v1/runs")).json();
  const run = runs.runs.find((r: { domain: string }) => r.domain === DOMAIN);
  expect(run, "the run is listed for the tenant").toBeTruthy();
  const driven = await page.request.post(`${API}/fake-idp/drive`, { data: { run_id: run.run_id } });
  expect(driven.ok()).toBeTruthy();
  const { status, finished_reason, build_error_codes } = await driven.json();
  expect(["COMPLETED", "PARTIAL"], `run failed: ${finished_reason}`).toContain(status);
  expect(build_error_codes, "the synthetic run must assemble the production export package")
    .toEqual([]);

  await page.reload();
  await expect(page.getByRole("heading", { name: "Latest run" })).toBeVisible();
  await page.getByRole("button", { name: "Word and Excel files" }).click();
  const files = page.locator('a[href^="/v1/exports/"]');
  await expect(files.first()).toBeVisible({ timeout: 60_000 });
  await noSeriousA11y(page);

  const download = page.waitForEvent("download");
  await files.first().click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/\.(docx|xlsx)$/);
  const path = await file.path();
  const { statSync } = await import("node:fs");
  expect(statSync(path).size).toBeGreaterThan(1000);
});
