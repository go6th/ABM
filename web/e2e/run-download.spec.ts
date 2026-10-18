// The core user journey end to end: sign in -> add an account -> start a run -> the run finishes
// (driven through the SYNTHETIC world: scripted providers and model, real slice lifecycle) ->
// build the Word and Excel files -> download them. Accessibility is checked on each screen.
import { test, expect } from "@playwright/test";
import { API, noSeriousA11y, signIn } from "./helpers";
import { tenantRequest } from "./parity/support";

const DOMAIN = "acme.example"; // the synthetic world's scripted account (tests/system/world.py)
let seeded: { slug_a: string };

test.beforeAll(async ({ request }) => {
  seeded = await (await request.get(`${API}/fake-idp/tenants`)).json();
});

test("sign in, add an account, run it and download the deliverables", async ({ page }) => {
  test.setTimeout(180_000);
  await signIn(page, seeded.slug_a, "ada", "admin");

  await page.goto("/accounts");
  await page.getByLabel("Company domain").fill(DOMAIN);
  await page.getByRole("button", { name: "Add account" }).click();
  await expect(page.getByRole("heading", { name: DOMAIN })).toBeVisible();
  await noSeriousA11y(page);

  await page.getByRole("button", { name: "Refresh from web" }).click();
  await expect(page.getByRole("dialog", { name: "Refresh from web" })).toBeVisible();
  await page.getByLabel("Include history with Synthetic Partner Co").check();
  await noSeriousA11y(page);
  const confirm = page.getByRole("button", { name: "Confirm refresh" });
  await confirm.dblclick();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByText("Research in progress")).toBeVisible();

  const runs = await tenantRequest(page, "GET", "/v1/runs").then((response) => response.json()) as { runs: { domain: string; run_id: string }[] };
  const mine = runs.runs.filter((r: { domain: string }) => r.domain === DOMAIN);
  expect(mine, "a double click starts exactly one run").toHaveLength(1);
  const run = mine[0]!;
  const driven = await page.request.post(`${API}/fake-idp/drive`, { data: { run_id: run.run_id } });
  expect(driven.ok()).toBeTruthy();
  const { status, finished_reason, build_error_codes } = await driven.json();
  expect(["COMPLETED", "PARTIAL"], `run failed: ${finished_reason}`).toContain(status);
  expect(build_error_codes, "the synthetic run must assemble the production export package")
    .toEqual([]);

  await page.reload();
  await expect(page.getByRole("heading", { name: "Downloads" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "What we know" })).toBeVisible();
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
