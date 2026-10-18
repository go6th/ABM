// The research path: refresh an account, the run finishes over the synthetic world, then the
// account, the deep dive tables, the run pages and the downloads all show its results.
import { test, expect } from "@playwright/test";
import { API, seeded, signIn, tenantRequest, watch, type Seeded } from "./support";

const DOMAIN = "acme.example"; // the synthetic world's scripted account (tests/system/world.py)
let s: Seeded;
test.describe.configure({ mode: "serial" });
test.beforeAll(async ({ request }) => { s = await seeded(request); });

test("refresh: one confirmed refresh (double click) starts exactly one run, which finishes", async ({ page }) => {
  test.setTimeout(240_000);
  await signIn(page, s.slug_a, "ada", "admin");
  await page.goto("/accounts");
  await page.getByLabel("Company domain").fill(DOMAIN);
  await page.getByRole("button", { name: "Add account" }).click();
  await expect(page.getByRole("heading", { name: DOMAIN, level: 1 })).toBeVisible();
  await page.getByRole("button", { name: "Refresh from web" }).click();
  const dialog = page.getByRole("dialog", { name: "Refresh from web" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Include history with Synthetic Partner Co").check();
  await dialog.getByRole("button", { name: "Confirm refresh" }).dblclick();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByText("Research in progress")).toBeVisible();
  const runs = (await tenantRequest(page, "GET", "/v1/runs").then((response) => response.json())) as { runs: { domain: string; run_id: string }[] };
  const mine = runs.runs.filter((r) => r.domain === DOMAIN);
  expect(mine).toHaveLength(1);
  const driven = await page.request.post(`${API}/fake-idp/drive`, { data: { run_id: mine[0]!.run_id } });
  expect(driven.ok()).toBeTruthy();
  expect(["COMPLETED", "PARTIAL"]).toContain(((await driven.json()) as { status: string }).status);
  await page.reload();
  await expect(page.getByRole("heading", { name: "What we know" })).toBeVisible();
});

test("downloads: Word and Excel files are built and downloadable", async ({ page }) => {
  test.setTimeout(180_000);
  await signIn(page, s.slug_a, "ada", "admin");
  await page.goto(`/accounts/${DOMAIN}`);
  await page.getByRole("button", { name: "Word and Excel files" }).click();
  const file = page.locator('a[href^="/v1/exports/"]').first();
  await expect(file).toBeVisible({ timeout: 90_000 });
  const download = page.waitForEvent("download");
  await file.click();
  expect((await download).suggestedFilename()).toMatch(/\.(docx|xlsx)$/);
});

test("deep dive: the Facts table lists rows, searches, saves a view and opens a source", async ({ page }) => {
  const errors = watch(page);
  await signIn(page, s.slug_a, "ada", "admin");
  // Facts, not People: the synthetic world makes no people call (consent required, G8)
  await page.goto(`/accounts/${DOMAIN}/tables/facts`);
  const table = page.getByRole("table").first();
  await expect(table.getByRole("row").nth(1)).toBeVisible();
  await page.getByLabel("Search", { exact: true }).fill("zzzz-no-match");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(table.getByRole("row")).toHaveCount(1);
  await page.getByLabel("Search", { exact: true }).fill("");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(table.getByRole("row").nth(1)).toBeVisible();
  const name = `Parity view ${Date.now()}`;
  page.once("dialog", (d) => void d.accept(name));
  await page.getByRole("button", { name: "Save this view" }).click();
  const field = page.getByLabel("View name");
  if (await field.isVisible().catch(() => false)) {
    await field.fill(name);
    await page.getByRole("button", { name: "Save view" }).click();
  }
  await expect(page.getByLabel("Saved view").locator("option", { hasText: name })).toHaveCount(1);
  const source = table.getByRole("button").filter({ hasNotText: /^(Sort|Name|Role)/ }).last();
  await source.click();
  await expect(page.getByRole("complementary", { name: /source/i }).or(page.getByRole("dialog", { name: /source/i }))).toBeVisible();
  expect(errors).toEqual([]);
});

test("deep dive: each research table opens", async ({ page }) => {
  await signIn(page, s.slug_a, "ada", "admin");
  for (const t of ["people", "jobs", "tech", "facts", "coverage"]) {
    await page.goto(`/accounts/${DOMAIN}/tables/${t}`);
    await expect(page.getByRole("table").first()).toBeVisible();
  }
});

test("deep dive: row feedback is saved", async ({ page }) => {
  await signIn(page, s.slug_a, "ada", "admin");
  await page.goto(`/accounts/${DOMAIN}/tables/facts`);
  const table = page.getByRole("table").first();
  await table.getByRole("button").last().click();
  await page.getByRole("button", { name: "Send feedback" }).click();
  await expect(page.getByRole("status").filter({ hasText: /feedback saved/i })).toBeVisible();
});

test("signals history opens for the account", async ({ page }) => {
  await signIn(page, s.slug_a, "ada", "admin");
  await page.goto(`/accounts/${DOMAIN}/history`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
});

test("runs: the run is listed and its page shows status and coverage", async ({ page }) => {
  const errors = watch(page);
  await signIn(page, s.slug_a, "ada", "admin");
  await page.goto("/runs");
  await expect(page.getByRole("heading", { name: "Research runs", level: 1 })).toBeVisible();
  await page.getByRole("link", { name: /open run/i }).first().click();
  await expect(page.getByRole("heading", { name: "Research run", level: 1 })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Research area" })).toBeVisible();
  expect(errors).toEqual([]);
});
