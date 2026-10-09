// The tenant plane end to end: SSO sign-in per role, the account list, adding an account, the admin
// pages, role limits, a second tenant that sees nothing of the first, sign-out, keyboard and axe.
import { test, expect } from "@playwright/test";
import { API, noSeriousA11y, signIn } from "./helpers";

let seeded: { slug_a: string; slug_b: string };

test.beforeAll(async ({ request }) => {
  seeded = await (await request.get(`${API}/fake-idp/tenants`)).json();
});

test("an admin signs in with SSO, sees only the tenant's accounts and adds one", async ({ page }) => {
  await signIn(page, seeded.slug_a, "ada", "admin");
  await expect(page.getByText("acme-a.example").first()).toBeVisible();
  await expect(page.getByText("beta-only.example")).toHaveCount(0);
  await noSeriousA11y(page);
  await page.getByLabel("Company domain").fill("added-by-e2e.example");
  await page.getByRole("button", { name: "Add account" }).click();
  await expect(page.getByText("added-by-e2e.example").first()).toBeVisible();
  const pages: [string, string][] = [["/settings/users", "Users"], ["/settings/support", "Support access"], ["/audit", "Audit"], ["/usage", "Usage"], ["/settings", "Settings"]];
  for (const [path, heading] of pages) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: heading, exact: false }).first()).toBeVisible();
    await noSeriousA11y(page);
  }
  // the audit log recorded the reads and the add
  await page.goto("/audit");
  const added = page.getByRole("row").filter({ hasText: "added-by-e2e.example" });
  await expect(added.getByRole("cell", { name: "run.account_add", exact: true }).first()).toBeVisible();
  // client pages never name a provider, a credit count or a cost
  const text = (await page.locator("body").innerText()).toLowerCase();
  for (const word of ["credit", "usd", "p_people", "p_search"]) expect(text).not.toContain(word);
});

test("a viewer reads but cannot add, run or administer", async ({ page }) => {
  await signIn(page, seeded.slug_a, "val", "viewer");
  await expect(page.getByLabel("Company domain")).toHaveCount(0);
  await page.goto("/accounts/acme-a.example");
  await expect(page.getByRole("heading", { name: "acme-a.example" })).toBeVisible();
  await expect(page.getByRole("form", { name: "Start a run" })).toHaveCount(0);
  await page.goto("/settings/users");
  await expect(page.getByText("Only admins manage users.")).toBeVisible();
  expect((await page.request.get("/v1/users")).status()).toBe(403);
});

test("Claude chat streams, persists history and passes accessibility checks", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await signIn(page, seeded.slug_a, "ana", "analyst");
  await page.goto("/chat");
  await expect(page.getByRole("heading", { name: "How can I help?" })).toBeVisible();
  await expect(page.getByLabel("Model")).toBeVisible();
  await noSeriousA11y(page);
  await page.getByLabel("Message Claude").fill("Summarize our account notes");
  await page.keyboard.press("Enter");
  await expect(page.getByText("Synthetic response")).toBeVisible();
  await expect(page.locator(".chat-message.user").getByText("Summarize our account notes", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Summarize our account notes", exact: true })).toBeVisible();
  await noSeriousA11y(page);
  await expect(page.getByRole("button", { name: "Dark" })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Light" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(page.getByRole("button", { name: "Light" })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "New chat" }).click();
  await expect(page.getByText("Start a conversation")).toBeVisible();
  await expect(page.getByRole("button", { name: "Summarize our account notes", exact: true })).toBeVisible();
});

test("chat history is private to its tenant member", async ({ page }) => {
  await signIn(page, seeded.slug_a, "ana", "analyst");
  const csrf = (await (await page.request.get("/v1/sso/session")).json()).csrf_token as string;
  const created = await page.request.post("/v1/chat/threads", {
    data: {},
    headers: { "X-CSRF-Token": csrf },
  });
  expect(created.status()).toBe(201);
  const threadId = (await created.json()).id as string;
  await page.request.post("/v1/sso/logout", { headers: { "X-CSRF-Token": csrf } });

  await signIn(page, seeded.slug_a, "sam", "seller");
  expect((await page.request.get(`/v1/chat/threads/${threadId}`)).status()).toBe(404);
  const otherCsrf = (await (await page.request.get("/v1/sso/session")).json()).csrf_token as string;
  await page.request.post("/v1/sso/logout", { headers: { "X-CSRF-Token": otherCsrf } });

  await signIn(page, seeded.slug_b, "bob", "admin");
  expect((await page.request.get(`/v1/chat/threads/${threadId}`)).status()).toBe(404);
});

test("admins see BYO key availability without a key value", async ({ page }) => {
  await signIn(page, seeded.slug_a, "ada", "admin");
  await page.goto("/chat");
  await page.getByText("Provider key", { exact: true }).click();
  await expect(page.getByText("This environment does not enable encrypted tenant keys.")).toBeVisible();
  await expect(page.getByLabel("Anthropic API key")).toHaveCount(0);
  await noSeriousA11y(page);
});

const members: [string, string][] = [["sam", "seller"], ["ana", "analyst"]];
for (const [subject, role] of members) {
  test(`a ${role} can add accounts and start runs but not administer`, async ({ page }) => {
    await signIn(page, seeded.slug_a, subject, role);
    await expect(page.getByLabel("Company domain")).toBeVisible();
    await page.goto("/accounts/acme-a.example");
    await expect(page.getByRole("form", { name: "Start a run" })).toBeVisible();
    await page.goto("/settings/support");
    await expect(page.getByText("Only admins manage support access.")).toBeVisible();
    expect((await page.request.get("/v1/audit")).status()).toBe(403);
  });
}

test("a second tenant sees nothing of the first", async ({ page }) => {
  await signIn(page, seeded.slug_b, "bob", "admin");
  await expect(page.getByText("beta-only.example").first()).toBeVisible();
  await expect(page.getByText("acme-a.example")).toHaveCount(0);
  for (const path of ["/v1/accounts/acme-a.example/tables/people", "/v1/accounts/acme-b.example/runs"]) {
    const status = (await page.request.get(path)).status();
    expect([403, 404]).toContain(status);
  }
  const list = await (await page.request.get("/v1/accounts?limit=200")).json();
  expect(list.accounts.map((a: { domain: string }) => a.domain)).toEqual(["beta-only.example"]);
});

test("keyboard: the skip link is first and moves focus to the content", async ({ page }) => {
  await signIn(page, seeded.slug_a, "ada", "admin");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#main$/);
});

test("signing out ends the session", async ({ page }) => {
  await signIn(page, seeded.slug_a, "ada", "admin");
  const csrf = (await (await page.request.get("/v1/sso/session")).json()).csrf_token as string;
  expect((await page.request.post("/v1/sso/logout", { headers: { "X-CSRF-Token": csrf } })).status()).toBeLessThan(300);
  expect((await page.request.get("/v1/sso/session")).status()).toBe(401);
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);
});

test("pages carry a nonce CSP and the security headers", async ({ page }) => {
  const res = await page.goto("/login");
  const h = res!.headers();
  expect(h["content-security-policy"]).toMatch(/script-src[^;]*'nonce-/);
  expect(h["x-frame-options"]).toBe("DENY");
  expect(h["x-content-type-options"]).toBe("nosniff");
});

test("alerts: the inbox shows the alert and a rating is saved", async ({ page }) => {
  await signIn(page, seeded.slug_a, "ada", "admin");
  await page.goto("/alerts");
  await expect(page.getByRole("heading", { name: /New role posted at/ })).toBeVisible();
  await page.getByRole("button", { name: "Rate" }).first().click();
  await expect(page.getByText("Thanks.")).toBeVisible();
  await noSeriousA11y(page);
});

test("subscriptions start digest-only and subscribe up; admins see the budget in percent", async ({ page }) => {
  await signIn(page, seeded.slug_a, "ada", "admin");
  // start from the digest-only defaults (a fresh user's state; the API test covers new users)
  const csrf = (await (await page.request.get("/v1/sso/session")).json()).csrf_token as string;
  await page.request.put("/v1/subscriptions", { data: { digest: true, immediate_rules: [], slack: false, threshold: 0, muted_accounts: [] }, headers: { "X-CSRF-Token": csrf } });
  await page.goto("/settings/alerts");
  const box = page.getByLabel("New build or admin role in your lanes");
  await expect(box).not.toBeChecked();
  await expect(page.getByLabel("Weekly digest")).toBeChecked();
  await box.check();
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved.")).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("New build or admin role in your lanes")).toBeChecked();
  await expect(page.getByText(/% used/)).toBeVisible();
  const text = (await page.locator("body").innerText()).toLowerCase();
  for (const word of ["credit", "usd", "units"]) expect(text).not.toContain(word);
  await noSeriousA11y(page);
});

test("CRM write-back is admin-only, off by default and refuses an unmapped preview", async ({ page }) => {
  await signIn(page, seeded.slug_a, "ada", "admin");
  await page.goto("/settings/crm");
  await page.waitForLoadState("networkidle");
  await page.getByRole("button", { name: "Revoke" }).click();  // start from "off"
  await expect(page.getByText("Revoked: writes stop now.")).toBeVisible();
  await page.getByLabel("Account domain").fill("acme-a.example");
  await page.getByRole("button", { name: "Request write" }).click();
  await expect(page.getByText("Refused (crm_not_enabled).")).toBeVisible();
  await page.getByRole("button", { name: "Enable" }).click();
  await expect(page.getByText("Enabled.")).toBeVisible();
  await page.getByRole("button", { name: "Preview" }).click();
  await expect(page.getByText("Refused (mapping_missing).")).toBeVisible();
  await noSeriousA11y(page);
});

test("a seller cannot manage CRM write-back", async ({ page }) => {
  await signIn(page, seeded.slug_a, "sam", "seller");
  await page.goto("/settings/crm");
  await expect(page.getByText("Only admins manage CRM write-back.")).toBeVisible();
  expect((await page.request.post("/v1/crm/salesforce/enable", { headers: { "X-CSRF-Token": (await (await page.request.get("/v1/sso/session")).json()).csrf_token } })).status()).toBe(403);
});
