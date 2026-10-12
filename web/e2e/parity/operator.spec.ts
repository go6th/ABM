// The operator console with the dev staff verifier: sign in page, overview, clients, flags,
// support lookups, and the support role's limits. Question text never appears here.
import { test, expect } from "@playwright/test";
import { operatorSignIn, seeded, watch, type Seeded } from "./support";

let s: Seeded;
test.beforeAll(async ({ request }) => { s = await seeded(request); });

test("operator sign in offers Google sign-in and names no client", async ({ page }) => {
  await page.goto("/operator/login");
  await expect(page.getByRole("link", { name: /continue with google/i })).toHaveAttribute("href", /\/api\/operator\/login/);
  const text = (await page.locator("body").innerText()).toLowerCase();
  expect(text).not.toContain(s.slug_a);
});

test("an operator console visit without a session goes to operator sign in", async ({ page }) => {
  await page.goto("/operator/tenants");
  await expect(page).toHaveURL(/\/operator\/login/);
});

test("operator overview lists clients by id with their counts", async ({ page }) => {
  const errors = watch(page);
  await operatorSignIn(page, "operator");
  await page.goto("/operator");
  await expect(page.getByRole("heading", { level: 1 })).toContainText(/overview/i);
  await expect(page.getByText(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/).first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("an operator provisions a client and suspends it", async ({ page }) => {
  await operatorSignIn(page, "operator");
  await page.goto("/operator/tenants");
  await page.getByLabel("Workspace name").fill(`Parity client ${Date.now()}`);
  await page.getByRole("button", { name: /create tenant|create client|provision/i }).click();
  const made = page.getByRole("status").filter({ hasText: /provisioned/i }).first();
  await expect(made).toBeVisible();
  const id = /([0-9a-f]{8}-[0-9a-f-]{27})/.exec(await made.innerText())?.[1];
  expect(id).toBeTruthy();
  const row = page.getByRole("row").filter({ hasText: id! });
  await row.getByText("Lifecycle").click();
  await row.getByLabel("Reason code").first().fill("parity_check");
  await row.getByRole("button", { name: /suspend/i }).click();
  await expect(page.getByText(/saved|suspended/i).first()).toBeVisible();
  await expect(row.getByText("Suspended")).toBeVisible();
});

test("an operator adds a feature flag version", async ({ page }) => {
  await operatorSignIn(page, "operator");
  await page.goto("/operator/configuration");
  const flag = `parity_flag_${Date.now().toString(36).replace(/[0-9]/g, "")}`.slice(0, 40);
  await page.getByLabel("Flag identifier").fill(flag);
  await page.getByLabel("Reason code").first().fill("parity_check");
  await page.getByLabel("Rule JSON").fill('{"percent": 0}');
  await page.getByRole("button", { name: "Add version" }).click();
  await expect(page.getByText(new RegExp(`Saved ${flag} as version`))).toBeVisible();
  await expect(page.getByRole("cell", { name: flag })).toBeVisible();
});

test("support lookups refuse a malformed grant before calling the API", async ({ page }) => {
  await operatorSignIn(page, "operator");
  for (const path of ["/operator/support", "/operator/failures"]) {
    await page.goto(path);
    await page.getByLabel(/support grant id/i).fill("not-a-uuid");
    await page.getByRole("button", { name: /load tenant data|load/i }).click();
    await expect(page.getByRole("alert").filter({ hasText: /uuid/i })).toBeVisible();
  }
});

test("run inspection pages ask for a grant and a run id", async ({ page }) => {
  await operatorSignIn(page, "operator");
  for (const path of ["/operator/runs", "/operator/cost", "/operator/spend"]) {
    await page.goto(path);
    await expect(page.getByLabel(/support grant id/i)).toBeVisible();
    await expect(page.getByLabel("Run ID")).toBeVisible();
  }
});

test("the support role cannot change flags or provision clients", async ({ page }) => {
  await operatorSignIn(page, "support", "helper");
  await page.goto("/operator/configuration");
  await expect(page.getByText(/restricted to operator staff|operator role/i).first()).toBeVisible();
  await page.goto("/operator/tenants");
  await expect(page.getByLabel("Workspace name")).toHaveCount(0);
});

test("operator sign out ends the staff session", async ({ page }) => {
  await operatorSignIn(page, "operator");
  await page.goto("/operator");
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/operator\/login/);
});
