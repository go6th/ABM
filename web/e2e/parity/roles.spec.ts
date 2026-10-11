// Role limits mirror the server, and a second client sees nothing of the first.
import { test, expect } from "@playwright/test";
import { mutation, seeded, signIn, type Seeded } from "./support";

let s: Seeded;
test.beforeAll(async ({ request }) => { s = await seeded(request); });

test("a viewer reads but cannot add, refresh or administer", async ({ page }) => {
  await signIn(page, s.slug_a, "val", "viewer");
  await page.goto("/accounts");
  await expect(page.getByText("acme-a.example").first()).toBeVisible();
  await expect(page.getByLabel("Company domain")).toHaveCount(0);
  await page.goto("/accounts/acme-a.example");
  await expect(page.getByRole("heading", { name: "acme-a.example", level: 1 })).toBeVisible();
  await expect(page.getByRole("button", { name: "Refresh from web" })).toHaveCount(0);
  await page.goto("/settings/users");
  await expect(page.getByText("Only admins manage users.")).toBeVisible();
  expect((await page.request.get("/v1/users")).status()).toBe(403);
});

for (const [subject, role] of [["sam", "seller"], ["ana", "analyst"]] as const) {
  test(`a ${role} can add accounts and refresh but not administer`, async ({ page }) => {
    await signIn(page, s.slug_a, subject, role);
    await page.goto("/accounts");
    await expect(page.getByLabel("Company domain")).toBeVisible();
    await page.goto("/accounts/acme-a.example");
    await expect(page.getByRole("button", { name: "Refresh from web" })).toBeVisible();
    await page.goto("/settings/support");
    await expect(page.getByText("Only admins manage support access.")).toBeVisible();
    expect((await page.request.get("/v1/audit")).status()).toBe(403);
  });
}

test("a second client sees nothing of the first", async ({ page }) => {
  await signIn(page, s.slug_b, "bob", "admin");
  await page.goto("/accounts");
  await expect(page.getByText("beta-only.example").first()).toBeVisible();
  await expect(page.getByText("acme-a.example")).toHaveCount(0);
  for (const path of ["/v1/accounts/acme-a.example/tables/people", "/v1/accounts/acme-a.example"]) {
    expect([403, 404]).toContain((await page.request.get(path)).status());
  }
  const list = (await (await page.request.get("/v1/accounts?limit=200")).json()) as { accounts: { domain: string }[] };
  expect(list.accounts.map((a) => a.domain)).toEqual(["beta-only.example"]);
  const body = (await page.locator("body").innerText()).toLowerCase();
  expect(body).not.toContain(s.slug_a);
});

test("chat history is private to its member", async ({ page }) => {
  await signIn(page, s.slug_a, "ana", "analyst");
  const created = await page.request.post("/v1/chat/threads", { data: {}, headers: await mutation(page) });
  expect(created.status()).toBe(201);
  const threadId = ((await created.json()) as { id: string }).id;
  await page.request.post("/v1/sso/logout", { headers: await mutation(page) });
  await signIn(page, s.slug_a, "sam", "seller");
  expect((await page.request.get(`/v1/chat/threads/${threadId}`)).status()).toBe(404);
  await page.request.post("/v1/sso/logout", { headers: await mutation(page) });
  await signIn(page, s.slug_b, "bob", "admin");
  expect((await page.request.get(`/v1/chat/threads/${threadId}`)).status()).toBe(404);
});

test("keyboard: the skip link is first and moves focus to the content", async ({ page }) => {
  await signIn(page, s.slug_a, "ada", "admin");
  await page.goto("/accounts");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#main$/);
});

test("signing out ends the session", async ({ page }) => {
  await signIn(page, s.slug_a, "ada", "admin");
  expect((await page.request.post("/v1/sso/logout", { headers: await mutation(page) })).status()).toBeLessThan(300);
  expect((await page.request.get("/v1/sso/session")).status()).toBe(401);
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);
});
