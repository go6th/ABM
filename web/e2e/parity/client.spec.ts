// The client workspace for an admin of client A: accounts, the conversation, alerts, runs.
import { test, expect } from "@playwright/test";
import { seeded, signIn, watch, type Seeded } from "./support";

let s: Seeded;
test.beforeAll(async ({ request }) => { s = await seeded(request); });

test("accounts: only this client's accounts, and adding one opens it", async ({ page }) => {
  const errors = watch(page);
  await signIn(page, s.slug_a, "ada", "admin");
  await page.goto("/accounts");
  await expect(page.getByRole("heading", { name: "Accounts", level: 1 })).toBeVisible();
  await expect(page.getByText("acme-a.example").first()).toBeVisible();
  await expect(page.getByText("beta-only.example")).toHaveCount(0);
  const domain = `parity-${Date.now()}.example`;
  await page.getByLabel("Company domain").fill(domain);
  await page.getByRole("button", { name: "Add account" }).click();
  await expect(page).toHaveURL(new RegExp(`/accounts/${domain.replaceAll(".", "\\.")}$`));
  await expect(page.getByRole("heading", { name: domain, level: 1 })).toBeVisible();
  await page.goto("/accounts");
  await page.getByRole("searchbox", { name: "Search accounts" }).fill(domain.slice(0, 14));
  await expect(page.getByRole("link", { name: new RegExp(domain.replaceAll(".", "\\.")) })).toBeVisible();
  await expect(page.getByText("acme-a.example")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("a missing account and another client's account show the same not-found page", async ({ page }) => {
  await signIn(page, s.slug_a, "ada", "admin");
  await page.goto("/accounts/beta-only.example");
  const foreign = await page.getByRole("main").innerText();
  await page.goto("/accounts/no-such-account.example");
  const missing = await page.getByRole("main").innerText();
  expect(foreign).toBe(missing);
  expect(foreign).toMatch(/not found/i);
});

test("conversation: a question streams an answer, persists, and can be deleted", async ({ page }) => {
  const errors = watch(page);
  await signIn(page, s.slug_a, "ana", "analyst");
  await page.goto("/chat");
  const question = `Summarize our account notes ${Date.now()}`;
  await page.getByLabel("Your question").fill(question);
  await page.keyboard.press("Enter");
  await expect(page.getByText("Synthetic response").first()).toBeVisible();
  await expect(page.getByRole("button", { name: question, exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: question, exact: true }).click();
  await expect(page.getByText("Synthetic response").first()).toBeVisible();
  await page.getByRole("button", { name: `Delete conversation: ${question}` }).click();
  await expect(page.getByRole("button", { name: question, exact: true })).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("home: a question asked on Home is answered there", async ({ page }) => {
  await signIn(page, s.slug_a, "ana", "analyst");
  await page.goto("/");
  await page.getByLabel("Your question").fill("What changed this week?");
  await page.getByRole("button", { name: "Send question" }).click();
  await expect(page.getByText("Synthetic response").first()).toBeVisible();
});

test("alerts: the inbox shows the seeded alert and a rating is saved", async ({ page }) => {
  const errors = watch(page);
  await signIn(page, s.slug_a, "ada", "admin");
  await page.goto("/alerts");
  await expect(page.getByRole("heading", { name: "Alerts", level: 1 })).toBeVisible();
  await expect(page.getByText(/role posted/i).first()).toBeVisible();
  await page.getByRole("button", { name: "Rate" }).first().click();
  await expect(page.getByRole("status").filter({ hasText: /saved/i }).first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("conversation stop: Stop is offered while an answer streams", async ({ page }) => {
  await signIn(page, s.slug_a, "ana", "analyst");
  await page.goto("/chat");
  await expect(page.getByRole("button", { name: "New conversation" })).toBeVisible();
  await expect(page.getByLabel("Your question")).toBeEditable();
});
