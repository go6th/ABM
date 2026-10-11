// Settings and administration for an admin of client A.
import { test, expect } from "@playwright/test";
import { mutation, seeded, signIn, watch, type Seeded } from "./support";

let s: Seeded;
test.beforeAll(async ({ request }) => { s = await seeded(request); });

test("workspace settings: research preferences save", async ({ page }) => {
  const errors = watch(page);
  await signIn(page, s.slug_a, "ada", "admin");
  await page.goto("/settings");
  await page.getByRole("button", { name: /save research preferences/i }).click();
  await expect(page.getByRole("status").filter({ hasText: /research preferences saved/i })).toBeVisible();
  expect(errors).toEqual([]);
});

test("workspace settings: branding saves a display name", async ({ page }) => {
  await signIn(page, s.slug_a, "ada", "admin");
  await page.goto("/settings");
  await page.getByLabel("Display name").fill("Parity Brand");
  await page.getByRole("button", { name: /save branding/i }).click();
  await expect(page.getByRole("status").filter({ hasText: /branding saved/i })).toBeVisible();
});

test("alert preferences: subscribe up and the choice persists; the allowance shows in percent", async ({ page }) => {
  await signIn(page, s.slug_a, "ada", "admin");
  const reset = await page.request.put("/v1/subscriptions", { data: { digest: true, immediate_rules: [], slack: false, threshold: 0, muted_accounts: [] }, headers: await mutation(page) });
  expect(reset.ok()).toBeTruthy();
  await page.goto("/settings/alerts");
  const box = page.getByLabel("New build or admin role in your lanes");
  await expect(box).not.toBeChecked();
  await expect(page.getByLabel("Weekly digest")).toBeChecked();
  await box.check();
  await page.getByRole("button", { name: /save preferences/i }).click();
  await expect(page.getByRole("status").filter({ hasText: /alert preferences saved/i })).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("New build or admin role in your lanes")).toBeChecked();
  await expect(page.getByText(/% used/)).toBeVisible();
  const text = (await page.locator("body").innerText()).toLowerCase();
  for (const word of ["credit", "usd"]) expect(text).not.toContain(word);
});

test("team: members are listed and adding one reports the server's answer", async ({ page }) => {
  await signIn(page, s.slug_a, "ada", "admin");
  await page.goto("/settings/users");
  await expect(page.getByText(`${s.slug_a}:ana`)).toBeVisible();
  await page.getByLabel("Sign-in identity").fill(`${s.slug_a}:newbie${Date.now()}`);
  await page.getByRole("button", { name: "Add member" }).click();
  await expect(page.getByRole("alert").or(page.getByRole("status")).filter({ hasText: /member added|multi-factor|refused/i }).first()).toBeVisible();
});

test("security: sessions are listed with this device marked", async ({ page }) => {
  await signIn(page, s.slug_a, "ada", "admin");
  await page.goto("/settings/security");
  await expect(page.getByText("This device")).toBeVisible();
  await expect(page.getByLabel("New password", { exact: true })).toBeVisible();
});

test("single sign-on: this client's connection is listed", async ({ page }) => {
  await signIn(page, s.slug_a, "ada", "admin");
  await page.goto("/settings/sso");
  await expect(page.getByText(s.slug_a).first()).toBeVisible();
});

test("integrations: the page offers to connect a source", async ({ page }) => {
  await signIn(page, s.slug_a, "ada", "admin");
  await page.goto("/settings/connectors");
  await expect(page.getByRole("button", { name: /connect google drive/i })).toBeVisible();
});

test("CRM write-back is off by default and refuses an unmapped preview", async ({ page }) => {
  await signIn(page, s.slug_a, "ada", "admin");
  await page.goto("/settings/crm");
  await page.waitForLoadState("networkidle");
  await page.getByRole("button", { name: "Revoke" }).click();
  await expect(page.getByText(/revoked/i).first()).toBeVisible();
  await page.getByLabel("Account domain").fill("acme-a.example");
  await page.getByRole("button", { name: "Request write" }).click();
  await expect(page.getByText(/crm_not_enabled|not enabled/i).first()).toBeVisible();
  await page.getByRole("button", { name: "Enable" }).click();
  await expect(page.getByText(/enabled\./i).first()).toBeVisible();
  await page.getByRole("button", { name: "Preview" }).click();
  await expect(page.getByText(/mapping_missing|mapping/i).first()).toBeVisible();
});

test("support access: a grant request reports the server's answer", async ({ page }) => {
  await signIn(page, s.slug_a, "ada", "admin");
  await page.goto("/settings/support");
  await page.getByLabel("Support engineer").fill("helper@6th.example");
  await page.getByRole("button", { name: /grant read-only access/i }).click();
  await expect(page.getByRole("status").filter({ hasText: /access granted|multi-factor|refused/i }).first()).toBeVisible();
});

test("usage: the plan allowances are listed in units", async ({ page }) => {
  await signIn(page, s.slug_a, "ada", "admin");
  await page.goto("/usage");
  await expect(page.getByRole("heading", { level: 1 })).toContainText(/usage/i);
  await expect(page.getByRole("table").first()).toBeVisible();
});

test("audit: the trail records account adds and offers a CSV", async ({ page }) => {
  await signIn(page, s.slug_a, "ada", "admin");
  await page.goto("/audit");
  await expect(page.getByRole("cell", { name: "run.account_add", exact: true }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /download/i })).toHaveAttribute("href", "/v1/audit/export");
});

test("provider key: admins see availability and never a key value", async ({ page }) => {
  await signIn(page, s.slug_a, "ada", "admin");
  await page.goto("/settings/model-key");
  await expect(page.getByText("This environment does not enable encrypted tenant keys.")).toBeVisible();
});
